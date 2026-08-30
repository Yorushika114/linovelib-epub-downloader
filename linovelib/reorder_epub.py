# -*- coding: utf-8 -*-
"""对已下载的乱序 EPUB 逐章重排,纠正正文顺序。

linovelib 深层的反爬会把长章正文的段落【位置】打乱(开头/结尾随抓取而变),即便下载
时从真浏览器实时 DOM 读取、按文本去重(见 downloader),成品 epub 仍可能乱序。本模块
作为后处理工具:对 epub 里每一章提取正文段 → 用参考书 ReferenceAligner 按参考出现顺序
重排 → 写回,其余条目(mimetype/opf/ncx/nav/样式/封面图)保持字节不变。

用法::

    reorder_epub_file("错乱.epub", "纠正.epub", reference_epub_path)
"""

import zipfile
import re
from bs4 import BeautifulSoup, XMLParsedAsHTMLWarning
import warnings

from .order_align import ReferenceAligner
from .downloader import _split_header

warnings.filterwarnings("ignore", category=XMLParsedAsHTMLWarning)

_CHAPTER_RE = re.compile(r"vol\d+_ch\d+\.xhtml$", re.I)


def _reorder_chapter_xhtml(data, aligner):
    """把单个章节 XHTML 的正文段按参考书重排,保留 章节标题(img/样式)与章节末的插图段。"""
    soup = BeautifulSoup(data.decode("utf-8", "replace"), "lxml")
    body = soup.body
    if body is None:
        return data

    heading = body.find("h1")
    if heading is not None:
        heading.extract()

    text_ps = [p for p in body.find_all("p") if p.find("img") is None]
    img_ps = [p for p in body.find_all("p") if p.find("img") is not None]
    if not text_ps:
        return data  # 无正文段,跳过

    candidates = [p.get_text(strip=True) for p in text_ps]
    banner, bodycands = _split_header(list(candidates))
    # bodycands 已去掉横幅标题段;参考对齐器只重排正文,横幅保持在章首
    ordered = aligner.align(bodycands) if bodycands else []
    final = banner + ordered

    for p in text_ps:
        p.extract()
    for p in img_ps:
        p.extract()

    if heading is not None:
        body.insert(0, heading)
    for t in final:
        np = soup.new_tag("p")
        np.string = t
        body.append(np)
    for p in img_ps:
        body.append(p)
    return soup.prettify("utf-8")


def reorder_epub_file(src_path, dst_path, reference_epub_path, progress=print):
    """重排 src epub 的全部章节,写出 dst。返回重排过的章节数。"""
    aligner = ReferenceAligner(reference_epub_path)
    changed = 0
    with zipfile.ZipFile(src_path) as zin, \
            zipfile.ZipFile(dst_path, "w", zipfile.ZIP_DEFLATED) as zout:
        for item in zin.infolist():
            name = item.filename
            data = zin.read(name)
            if name.endswith(".xhtml") and _CHAPTER_RE.search(name):
                new_data = _reorder_chapter_xhtml(data, aligner)
                if new_data != data:
                    changed += 1
                    data = new_data
                    progress(f"  重排: {name}")
            zout.writestr(item, data)
    return changed
