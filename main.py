import sys
import re
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

    # 选卷
    if args.volumes:
        selected = [int(x) for x in args.volumes.split(",") if x.strip()]
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
        sel = _interactive_choose(volumes)
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

    tmpdir = CACHE_DIR
    tmpdir.mkdir(exist_ok=True)
    failed = []
    total_ch = sum(len(v.chapters) for v in volumes)
    done = 0
    for vol in volumes:
        for ch in vol.chapters:
            done += 1
            try:
                download_chapter(ch, nid, fetcher, tmpdir)
                print(f"  [OK] 章节 {done}/{total_ch} 完成：{ch.title if ch.title else ch.id}")
            except Exception as e:
                failed.append((ch.id, ch.title))
                print(f"  [ERR] 章节 {done}/{total_ch} 失败：{ch.title or ch.id}（{e}）")

    # 封面：优先用所选第一卷的独立封面（卷页 og:image，如 img3.readpai.com/cover/
    # {nid}/{imageid}.jpg），因为它才是真正的卷封面；小说页的 og:image 常是
    # booklist 缩略图（xxx s.jpg），用作封面会张冠李戴。拿不到时退回小说页封面。
    cover_url = ""
    if volumes and volumes[0].cover_url:
        cover_url = volumes[0].cover_url
    elif novel.cover_url:
        cover_url = novel.cover_url

    cover_data = None
    if cover_url:
        try:
            cover_data = fetcher.get_bytes(cover_url)
            if not fetcher.is_valid_image(cover_data):
                cover_data = None
        except Exception:
            cover_data = None

    novel.volumes = volumes

    # 默认输出到项目目录下的 download/<小说标题>/ 子文件夹（相对路径），一个小说
    # 一个文件夹归类；显式指定 --out 时尊重用户填写的路径。
    if args.out:
        out = args.out
    else:
        title_safe = _sanitize(novel.title)
        folder = DEFAULT_DOWNLOAD_DIR / title_safe
        folder.mkdir(parents=True, exist_ok=True)  # build_epub 的临时文件和最终
        # EPUB 都写在输出目录，里所以必须先建好。
        # 用「爬取到的书名 + 卷数」命名（如「败北女角太多了！ 第4卷.epub」）；不选卷时
        # 回退为仅书名，卷数也会随所选卷自动拼接（单卷/连续区间/离散）。
        out = folder / f"{title_safe}{_volume_suffix(volumes, novel.title)}.epub"
    try:
        written = build_epub(novel, out, cover_data)
    except Exception as e:
        print(f"生成 EPUB 失败：{e}")
        return 1
    print(f"已生成：{written}")

    if failed:
        print("以下章节未能下载：")
        for cid, title in failed:
            print(f"  {cid} {title}")
    return 0


def _interactive_choose(volumes):
    print("可用卷：")
    for i, v in enumerate(volumes, start=1):
        print(f"  [{i}] {v.title}  （{len(v.chapters)} 章）")
    raw = input("输入要下载的卷号，逗号分隔；输入 all 下载全部：[all] ").strip()
    if not raw or raw.lower() == "all":
        return "all"
    return [int(x) for x in raw.split(",") if x.strip()]


if __name__ == "__main__":
    sys.exit(main())
