"""用同一本书的参考 EPUB 校正正文顺序。

linovelib 的反爬会向爬虫（纯请求/无浏览器上下文）固定下发「被打断顺序」的正文：
同一篇场景被拆散，例如真序「直勾勾→你有点冷漠→拍桌→乱加设定→水蒸青梅」五连段，
在服务器返回里被甩到不同位置。更糟的是——这个乱序 R 是**确定性**的，而且客户端（即便
用真实浏览器）拿到的是同一份被打乱的内容，pctheme.js 也只是「克隆+藏克隆」，并不负责
还原顺序。

所以服务器返回里**根本没有真序**。唯一可靠的真序来源是这本书的参考 EPUB。本模块把
下载到的段落，按其在参考 EPUB 中出现的位置重新排序，即可还原真序。

注意：参考与下载必须是同一部作品、同一文本；段落按「归一化后完全一致」匹配。未能在
参考里找到段落的（例如参考版本少一句话、或站点新加了内容）会在末尾按原相对顺序保留，
不会丢弃。
"""

import re
import pathlib
import warnings
from difflib import SequenceMatcher

from bs4 import BeautifulSoup, XMLParsedAsHTMLWarning
from ebooklib import epub

# EPUB 章节是带 xmlns 的 XHTML，用 HTML 解析器解析时 bs4 会告警，这里静默。
warnings.filterwarnings("ignore", category=XMLParsedAsHTMLWarning)

_WS = re.compile(r"[\s　 ​﻿]+")

# 克隆污染护栏阈值：参考书跨度内，去重后的句子里「重复出现（≥2 次）」的占比一旦超过
# 此值，就判定该跨度是 pctheme 克隆污染的脏数据（整句被重复多遍+顺序被打乱），
# 不可用作真序来源，回退到服务器顺序。真实正版书里合法重复多为语气词，占比一般 <0.15；
# 克隆污染的章节（如序章）可高达 0.30 以上，两者用 0.25 可安全分开。
_CLONE_SUSPECT_RATIO = 0.25


def norm(text):
    """把换行/全角空格/零宽字符等拍平为单个半角空格并去首尾，便于跨来源匹配。"""
    return _WS.sub(" ", text).strip()


def _bigrams(text):
    compact = re.sub(r"[^\u4e00-\u9fffA-Za-z0-9]", "", norm(text))
    return {compact[i:i + 2] for i in range(len(compact) - 1)} or ({compact} if compact else set())


def _score(left, right, left_grams=None, right_grams=None):
    """跨译文段落的轻量相似度：共有双字优先，再结合字符序列相似度。"""
    a, b = norm(left), norm(right)
    if a == b:
        return 1.0
    left_grams = left_grams if left_grams is not None else _bigrams(a)
    right_grams = right_grams if right_grams is not None else _bigrams(b)
    if not left_grams or not right_grams:
        return 0.0
    overlap = len(left_grams & right_grams) / len(left_grams | right_grams)
    sequence = SequenceMatcher(None, a, b, autojunk=False).ratio()
    return 0.55 * sequence + 0.45 * overlap


def _bigram_index(paragraphs):
    index = {}
    grams = []
    for pos, paragraph in enumerate(paragraphs):
        row = _bigrams(paragraph)
        grams.append(row)
        for gram in row:
            index.setdefault(gram, set()).add(pos)
    return index, grams


def _top_matches(paragraph, references, index, reference_grams, limit=6):
    grams = _bigrams(paragraph)
    candidates = set()
    for gram in grams:
        candidates.update(index.get(gram, ()))
    if not candidates:
        # 同义短句可能没有共同双字（如“灯号变绿”与“红绿灯变成绿色”），
        # 此时回退到本章全部段落，仍可由字符序列相似度给出唯一候选。
        candidates = range(len(references))
    scored = [(_score(paragraph, references[pos], grams, reference_grams[pos]), pos)
              for pos in candidates]
    scored.sort(reverse=True)
    return scored[:limit]


class ReferenceAligner:
    """以参考 EPUB 的段落出现顺序作为真序，重排下载得到的段落。

    用法::

        aligner = ReferenceAligner("败北女角太多了！ 第4卷.epub")
        ordered = aligner.align(paragraphs)   # paragraphs 被打乱 -> ordered 真序
    """

    def __init__(self, epub_path):
        # text -> [pos1, pos2, ...]：同一句在参考书里出现多次时记录所有位置（升序）。
        # 这样真正序里重复的短句（如「什麼！？」「…………」）能按出现次数摊回各自位置，
        # 而不是被折叠到首次出现处彼此相邻。
        self._pos = {}
        self._documents = []
        self._build(pathlib.Path(epub_path))

    def _build(self, path):
        book = epub.read_epub(str(path))
        idx = 0
        for item in book.get_items_of_type(9):  # ebooklib.ITEM_DOCUMENT
            raw = item.get_content().decode("utf-8", "replace")
            soup = BeautifulSoup(raw, "lxml")
            document = []
            for p in soup.find_all("p"):
                t = norm(p.get_text(" ", strip=True))
                if t:
                    self._pos.setdefault(t, []).append(idx)
                    document.append(t)
                    idx += 1
            if document:
                self._documents.append(document)

    def align(self, paragraphs):
        """按参考书位置重排整章段落。

        章节只是参考书的**一段连续切片**，而参考书里同一短句（多为「咦？」这类语气词）
        会在整本多处出现。不能按「全局第几次出现」直接摊开——那样会把章节里的重复短句
        映射到章节范围之外的位置、拉到章节开头之前。正确做法是**边界锚定**：
        先用整本书只出现一次的句子（无歧义）定出本章节的参考跨度 [lo,hi]，再让每段取其
        候选位置里落在该跨度内的那个；不在跨度内的（版本差异/新增内容）取最接近者并保持
        原相对顺序，绝不丢弃。
        """
        if not paragraphs:
            return list(paragraphs)
        if not self._pos:
            return self._fuzzy_align(paragraphs)
        keys = [norm(p) for p in paragraphs]
        cands = [self._pos.get(k, []) for k in keys]
        # 安全护栏：参考书必须确实覆盖本卷。若命中的段占比过低，说明用户拿的是别的卷的
        # 参考书（或版本差异过大），此时硬对齐会把少数命中段拽到前面、破坏顺序。
        # 命中率太低时整体放弃对齐，返回服务器顺序（宁可保留原样，也不乱排）。
        found = sum(1 for c in cands if c)
        if found / len(paragraphs) < 0.5:
            return self._fuzzy_align(paragraphs)
        # 无歧义锚点：只出现一次的句子，位置唯一，用它界定章节跨度
        single = [c[0] for c in cands if len(c) == 1]
        lo = min(single) if single else None
        hi = max(single) if single else None
        # 克隆污染护栏：参考书本身若是 pctheme 克隆产物，跨度内会「整句重复多遍」，且
        # 其真实顺序也已被打乱（克隆与乱序同源）。此时再对齐会把服务器干净的正确序给
        # 排成脏序，所以直接回退服务器顺序，宁可不排也不乱排。
        if lo is not None and hi is not None and self._clone_heavy(keys, cands, lo, hi):
            return list(paragraphs)
        used = {}
        scored = []
        for i, (k, c) in enumerate(zip(keys, cands)):
            if not c:
                pos = float("inf")          # 参考书里找不到：保留原顺序，排在末尾
            elif lo is None:
                pos = c[0]                  # 没有锚点可用：退化为取首个候选位
            else:
                in_range = [x for x in c if lo <= x <= hi]
                if in_range:
                    # 同章重复短句：同一句多次出现时，摊到互不相同的章节内部位置，
                    # 避免全部叠到首次出现处把后续句子挤乱（这正是排版混乱的来源）。
                    j = used.get(k, 0)
                    used[k] = j + 1
                    pos = in_range[0] if j >= len(in_range) else in_range[j]
                else:
                    # 版本差异/新增内容：取最接近章节跨度的候选，保持原相对顺序。
                    pos = min(c, key=lambda x: min(abs(x - lo), abs(x - hi)))
            scored.append((pos, i, paragraphs[i]))
        # 有参考位置按参考升序；同一位置/无位置的按原始顺序稳定保留。
        scored.sort(key=lambda x: (x[0], x[1]))
        return [p for _, _, p in scored]

    def _fuzzy_align(self, paragraphs):
        """当参考与网站属于不同译文时，按段落相似度恢复参考的章节顺序。

        先根据长度和抽样匹配选择对应参考章节，再用稀疏双字索引建立高置信的一对一
        映射。找不到足够共同文字时保持服务器顺序，避免把错误参考书强行套用。
        """
        size = len(paragraphs)
        slack = max(8, int(size * 0.25))
        candidates = [doc for doc in self._documents if abs(len(doc) - size) <= slack]
        if not candidates:
            candidates = self._documents
        if not candidates:
            return list(paragraphs)

        def document_score(doc):
            index, grams = _bigram_index(doc)
            sample_count = min(12, len(paragraphs))
            sample = paragraphs if sample_count == len(paragraphs) else [
                paragraphs[round(i * (len(paragraphs) - 1) / (sample_count - 1))]
                for i in range(sample_count)
            ]
            values = [_top_matches(text, doc, index, grams, limit=1) for text in sample]
            return sum(row[0][0] if row else 0.0 for row in values) / len(sample)

        reference = max(candidates, key=document_score)
        index, reference_grams = _bigram_index(reference)
        matches = [_top_matches(text, reference, index, reference_grams)
                   for text in paragraphs]
        best_scores = [row[0][0] if row else 0.0 for row in matches]
        if sum(best_scores) / len(best_scores) < 0.20:
            return list(paragraphs)

        # 贪心地消除“多个网站段落映射到同一参考段”的冲突。只保留每段的前六个
        # 候选，复杂度随实际共有双字数量增长，能处理千段长章节而无需 SciPy。
        edges = [(score, source, target)
                 for source, row in enumerate(matches)
                 for score, target in row]
        edges.sort(reverse=True)
        assigned_source, used_target, target_of = set(), set(), {}
        for score, source, target in edges:
            if source in assigned_source or target in used_target:
                continue
            assigned_source.add(source)
            used_target.add(target)
            target_of[source] = target

        # 低置信/无共有文字的段落仍保留，紧邻其最佳候选且保持彼此原顺序。
        keyed = []
        for source, paragraph in enumerate(paragraphs):
            if source in target_of:
                position = target_of[source]
            elif matches[source]:
                position = matches[source][0][1]
            else:
                position = float("inf")
            keyed.append((position, source, paragraph))
        keyed.sort(key=lambda row: (row[0], row[1]))
        return [paragraph for _, _, paragraph in keyed]

    def _clone_heavy(self, keys, cands, lo, hi):
        """判断参考书在该章节跨度内是否克隆污染（整句重复多遍 → 乱序同源）。

        统计跨度 [lo,hi] 内每个去重句子的出现次数：同一句子在参考书跨度内出现 ≥2 次，
        说明它被 pctheme 克隆过（干净的正版书里同一整句不会在相邻位置原样重复多遍）。
        重复句占去重句的比例超过阈值即判定为脏跨度。
        """
        from collections import Counter
        in_span = Counter()
        for k, c in zip(keys, cands):
            if not c:
                continue
            # 该句在跨度 [lo,hi] 内的候选位置数 = 参考书里原样重复该句的次数
            n = sum(1 for x in c if lo <= x <= hi)
            if n:
                in_span[k] += n
        distinct = len(in_span)
        if not distinct:
            return False
        dup_distinct = sum(1 for t, c in in_span.items() if c > 1)
        return dup_distinct / distinct > _CLONE_SUSPECT_RATIO
