import sys
import re
import dataclasses
from linovelib.fetcher import Fetcher
from linovelib.resolver import resolve_id, fetch_novel, ResolveError
from linovelib.catalog import (parse_catalog, parse_volume_chapters,
                               parse_volume_page)
from linovelib.downloader import download_chapter
from linovelib.epub_builder import build_epub
from linovelib.cli import build_parsed_args, choose_volumes
from linovelib.paths import CACHE_DIR, DEFAULT_DOWNLOAD_DIR

# 中文 Windows 的 stdout/stderr 默认是 GBK：章节标题或内容里一旦出现 GBK 编不了的字符
# （如 ♡、✓、→ 等）就会抛 UnicodeEncodeError 闪退，而且不知道会在哪一章触发。
# 这里**保留 GBK**（确保中文能正常显示），只把编不了的字符替换成 "?" 而非抛异常，
# 这样打印永不崩溃。注意不能把编码设成 UTF-8——那会让 GBK 控制台上的中文全变乱码。
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(errors="replace")
    except Exception:
        pass


def _sanitize(name):
    bad = '<>:"/\\|?*\n\t'
    for c in bad:
        name = name.replace(c, "")
    return name.strip() or "novel"


def _volume_suffix(volumes, book_title):
    """从所选卷构建「卷序」后缀，用于 EPUB 文件名（爬取到的书名 + 卷数）。

    卷数**取卷标题里书名之后的文字**（如「书名 4」→4、「书名 8.5」→8.5、「书名 SSS 篇」→SSS 篇）。
    **注意不能取 Volume.vid**——vid 是卷页内部 id(如 181027)，不是友好卷号。
    单卷纯数(含 .5) -> " 第4卷"；多卷连续整数 -> " 第1-5卷"；非数字标签(如 SSS)原样追加。
    拿不到标签时回退为空串（仅书名命名）。
    """
    labels = []
    for v in volumes:
        t = v.title or ""
        if book_title and book_title in t:
            lab = t.split(book_title, 1)[1].strip()
        else:
            lab = t.strip()
        if lab:
            labels.append(lab)
    if not labels:
        return ""
    labels = list(dict.fromkeys(labels))  # 去重保序
    isnum = lambda s: re.fullmatch(r"\d+(?:\.\d+)?", s) is not None
    if len(labels) == 1:
        return f" 第{labels[0]}卷" if isnum(labels[0]) else f" {labels[0]}"
    if all(isnum(l) for l in labels) and all(float(l).is_integer() for l in labels):
        ints = sorted({int(float(l)) for l in labels})
        parts = []
        i = 0
        while i < len(ints):
            j = i
            while j + 1 < len(ints) and ints[j + 1] == ints[j] + 1:
                j += 1
            parts.append(str(ints[i]) if i == j else f"{ints[i]}-{ints[j]}")
            i = j + 1
        return " 第" + ",".join(parts) + "卷"
    if all(isnum(l) for l in labels):
        return " 第" + ",".join(labels) + "卷"
    return " " + ",".join(labels)


def _expand_volume_spec(spec):
    """把卷选择串展开为「从 1 开始的卷位号」列表，支持逗号与 '-' 区间。

    如 "1-3,5,7-9" -> [1,2,3,5,7,8,9]。空串/None -> []。去重保序；区间两端可反向(如 5-3)。
    """
    if not spec:
        return []
    out = []
    for part in str(spec).split(","):
        part = part.strip()
        if not part:
            continue
        if "-" in part:
            lo, hi = part.split("-", 1)
            lo, hi = lo.strip(), hi.strip()
            if not (lo.isdigit() and hi.isdigit()):
                raise ValueError(f"无法解析卷号区间：{part!r}")
            lo, hi = int(lo), int(hi)
            if lo > hi:
                lo, hi = hi, lo
            out.extend(range(lo, hi + 1))
        else:
            if not part.isdigit():
                raise ValueError(f"无法解析卷号：{part!r}")
            out.append(int(part))
    seen = set()
    res = []
    for x in out:
        if x in seen:
            continue
        seen.add(x)
        res.append(x)
    return res


def _novel_subset(novel, volumes, marker):
    """构造一本只含给定卷的子 Novel，供按卷单独生成 EPUB 用。

    marker 进入 identifier，使同一小说的多份 EPUB 各自唯一（避免阅读器/calibre 当同书去重）。
    """
    return dataclasses.replace(novel, volumes=list(volumes),
                               id=f"{novel.id}-{marker}")


def _sweep_temp(folder):
    """删除目录下残留的中断临时文件 *.epub.tmp（如用户强行中断导致的残留）。

    正常流程 build_epub 会在 finally 里删掉临时文件；只有被硬中断/杀软锁住时才可能留
    在原地。清扫它们仅针对旧残留，不碰正在使用中的文件（构建由本函数调用前尚未开始）。
    """
    try:
        for stale in folder.glob("*.epub.tmp"):
            try:
                stale.unlink()
            except Exception:
                pass
    except Exception:
        pass


def main(argv=None):
    args = build_parsed_args(argv)
    if not args.novel and not args.name:
        print("请提供 --novel <编号> 或 --name <书名>。")
        return 2

    # 卷页(vol_XXX.html)偶发慢响应，默认 timeout=15 会频繁超时浪费重试；提到 30s
    # 让慢但正常的响应直接成功（页面 26.8s 成功过）。章节页都快，30s 不影响它们。
    fetcher = Fetcher(delay=args.delay, retries=8, timeout=30)
    try:
        nid = resolve_id(args.novel or args.name, fetcher)
    except ResolveError as e:
        print(e)
        return 2

    novel = fetch_novel(nid, fetcher)
    print(f"小说：{novel.title}  作者：{novel.author}  (id={nid})")

    # 正文按【参考无关】方式还原真序：纯 requests 抓每页 #TextContent，复用站点/社区公开的
    # Fisher-Yates 逆置换把「每页前 20 段之后被洗牌的后缀」还原（见 fetcher.get_page_body），
    # downloader 再按文本去重克隆段。参考书【不参与下载/比较】——它只作为外部核对手段，
    # 绝不进入项目协助下载（用户要求：参考书只是比对爬取结果，不能作为项目内依赖，更不能跨卷）。
    # 若确需用正版 EPUB 做最终顺序校正，请在下载完成后【显式】使用 reorder_epub 后处理工具。
    aligner = None
    print("正文顺序：纯 requests + Fisher-Yates 反洗牌（参考无关），按文本去重克隆段。")
    print("提示：参考书不参与下载；如需最终顺序校正，请用 reorder_epub（显式 --reference，仅外部核对用）。")

    catalog_html = fetcher.get_html(f"https://www.linovelib.com/novel/{nid}/catalog")
    volumes = parse_catalog(catalog_html, nid)
    if not volumes:
        print("未能从目录页解析到任何卷/章节，已停止。")
        return 1

    # 选卷（支持逗号与 '-' 区间，如 "1-3,5"）
    if args.volumes:
        try:
            selected = _expand_volume_spec(args.volumes)
        except ValueError as e:
            print(e)
            return 2
        try:
            volumes = choose_volumes(volumes, selected)
        except ValueError as e:
            print(e)
            return 2
    elif args.volumes_short == "all":
        pass
    elif args.no_interactive:
        pass
    else:
        try:
            sel = _interactive_choose(volumes)
        except ValueError as e:
            print(e)
            return 2
        if sel != "all":
            try:
                volumes = choose_volumes(volumes, sel)
            except ValueError as e:
                print(e)
                return 2

    print(f"将下载 {len(volumes)} 卷：{', '.join(v.title for v in volumes)}")

    # 目录页可能漏掉个别章节（如败北女角第 4 卷的「～第一败～」cid 181030 不在
    # catalog），改用卷页的章节列表作为权威来源；顺带在该次请求里拿卷封面。
    # 卷页(vol_XXX.html)对重复 GET 会间歇性超时/限流，故先读磁盘缓存；无缓存才请求，
    # 成功后写回缓存。这样即使网络抽风，也用上次拿到的卷页正常跑完。
    vol_cache_dir = CACHE_DIR
    for vol in volumes:
        if not vol.vid:
            continue
        try:
            cache = vol_cache_dir / f"vol_{vol.vid}_page.html"
            if cache.exists():
                vol_html = cache.read_text(encoding="utf-8", errors="replace")
            else:
                vol_html = fetcher.get_html(
                    f"https://www.linovelib.com/novel/{nid}/vol_{vol.vid}.html")
                vol_cache_dir.mkdir(parents=True, exist_ok=True)
                cache.write_text(vol_html, encoding="utf-8")
            vchs = parse_volume_chapters(vol_html, nid)
            if vchs:
                vol.chapters = vchs
            vol.cover_url = parse_volume_page(vol_html)
        except Exception:
            pass

    # 封面：每卷用自己的【卷页 og:image】封面（cover/{nid}/{imageid}.jpg），这才是该卷
    # 真正的封面；小说页 og:image 常是 booklist 缩略图（xxx s.jpg），只在拿不到卷封面时
    # 兜底。逐卷合成各带本卷封面；整本合并用第一卷封面。按 URL 缓存避免重复下载。
    cover_cache = {}

    def _cover_bytes(url):
        if not url:
            return None
        if url in cover_cache:
            return cover_cache[url]
        data = None
        try:
            d = fetcher.get_bytes(url)
            if fetcher.is_valid_image(d):
                data = d
        except Exception:
            data = None
        cover_cache[url] = data
        return data

    def _vol_cover(vol):
        # 该卷卷封面优先；取不到再用小说页封面兜底。
        return _cover_bytes(vol.cover_url) or _cover_bytes(novel.cover_url)

    novel.volumes = volumes

    # 输出策略：默认【每下一卷就立即合成该卷】(边下边出，某卷卡住/失败不影响已完成的卷)。
    # 多卷时不再询问；只有显式 --merge 才额外补一份整本合并 EPUB。
    # --out 给了单一目标文件时不逐卷合成，全部下完再合成一个(单卷=该卷，多卷=合并本)。
    written = []

    def _build(sub, out, cover):
        try:
            p = build_epub(sub, out, cover)
            written.append(p)
            print(f"已生成：{p}")
        except Exception as e:
            print(f"生成 EPUB 失败：{out}（{e}）")

    tmpdir = CACHE_DIR
    tmpdir.mkdir(exist_ok=True)
    _sweep_temp(CACHE_DIR)  # 清掉被强行中断而残留的 .epub.tmp
    failed = []
    title_safe = _sanitize(novel.title)
    # 默认输出目录 download/<小说标题>/；--out 时 folder 为 None（不逐卷合成）。
    folder = None if args.out else (DEFAULT_DOWNLOAD_DIR / title_safe)
    if folder is not None:
        folder.mkdir(parents=True, exist_ok=True)
        _sweep_temp(folder)

    for vi, vol in enumerate(volumes, start=1):
        # 进度按【当前卷】显示：只显示本卷内的 X/Y（每卷各自计数），
        # 不再显示 1/316 这种跨卷总数——那样看不出进度发生在哪一卷。
        vol_label = _volume_suffix([vol], novel.title).strip() or f"第{vi}卷"
        vol_total = len(vol.chapters)
        for ci, ch in enumerate(vol.chapters, start=1):
            try:
                download_chapter(ch, nid, fetcher, tmpdir)
                print(f"  [OK] {vol_label} 章节 {ci}/{vol_total} 完成：{ch.title if ch.title else ch.id}")
            except Exception as e:
                failed.append((ch.id, ch.title))
                print(f"  [ERR] {vol_label} 章节 {ci}/{vol_total} 失败：{ch.title or ch.id}（{e}）")
        if folder is not None:
            # 该卷已下完 → 立即合成该卷 EPUB（不等其余卷），封面用该卷自己的。
            out = folder / f"{title_safe}{_volume_suffix([vol], novel.title)}.epub"
            _build(_novel_subset(novel, [vol], f"vol{vi}"), out, _vol_cover(vol))

    # 单文件 --out：全部下完再合成一个（单卷=该卷，多卷=合并本；尊重用户指定单一目标文件）。
    if args.out:
        sub = _novel_subset(novel, volumes, "book" if len(volumes) == 1 else "merged")
        _build(sub, args.out, _vol_cover(volumes[0]) if volumes else None)

    # 多卷 & 默认目录：不再询问。仅当显式 --merge 时才额外补一份整本合并 EPUB。
    if folder is not None and len(volumes) > 1 and args.merge:
        out = folder / f"{title_safe}{_volume_suffix(volumes, novel.title)}.epub"
        _build(_novel_subset(novel, volumes, "merged"), out,
               _vol_cover(volumes[0]) if volumes else None)

    if not written:
        return 1

    if failed:
        print("以下章节未能下载：")
        for cid, title in failed:
            print(f"  {cid} {title}")
    return 0


def _interactive_choose(volumes):
    print("可用卷：")
    for i, v in enumerate(volumes, start=1):
        print(f"  [{i}] {v.title}  （{len(v.chapters)} 章）")
    raw = input("输入要下载的卷号，逗号分隔（支持 '1-3,5' 区间）；输入 all 下载全部：[all] ").strip()
    if not raw or raw.lower() == "all":
        return "all"
    return _expand_volume_spec(raw)


if __name__ == "__main__":
    sys.exit(main())
