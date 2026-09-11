import sys
import re
import pathlib
import dataclasses
import zipfile
from linovelib.fetcher import Fetcher, CloudflareBlockedError
from linovelib.resolver import resolve_id, fetch_novel, ResolveError
from linovelib.catalog import (parse_catalog, parse_volume_chapters,
                               parse_volume_page)
from linovelib.downloader import download_chapter
from linovelib.epub_builder import build_epub
from linovelib.cli import build_parsed_args, choose_volumes
from linovelib.events import DownloadEvent, emit
from linovelib.paths import CACHE_DIR, NOVEL_DIR
from linovelib import __version__

# 中文 Windows 的 stdout/stderr 默认是 GBK：章节标题或内容里一旦出现 GBK 编不了的字符
# （如 ♡、✓、→ 等）就会抛 UnicodeEncodeError 闪退，而且不知道会在哪一章触发。
# 这里**保留 GBK**（确保中文能正常显示），只把编不了的字符替换成 "?" 而非抛异常，
# 这样打印永不崩溃。注意不能把编码设成 UTF-8——那会让 GBK 控制台上的中文全变乱码。
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(errors="replace")
    except Exception:
        pass


# 文件名里不能出现的字符。Windows 上 ":" 尤其致命——它把「名字:后半.epub」解释成
# 「文件『名字』+ 隐藏数据流『后半.epub』」，后果有两个，都不显眼：
#   · 资源管理器里只看到一个 0 字节、没有扩展名的怪文件，用户以为这卷没下下来；
#   · Path.exists() 却会命中那条流、返回 True，「已存在就跳过」的闸门于是把这一卷
#     **永久跳过**，重跑多少次都拿不到书。
# 实测 download/小说/Re从零开始的异世界生活/ 下就躺着这样一条 40 MB 的隐藏流，
# 卷标签是 "Re:zeropedia 公式书"。
_ILLEGAL_FILENAME_CHARS = '<>:"/\\|?*\n\t'


def _strip_illegal(text):
    """去掉文件名非法字符。与 _sanitize 的区别是**不兜底**：空串就是空串。"""
    for c in _ILLEGAL_FILENAME_CHARS:
        text = text.replace(c, "")
    return text.strip()


def _sanitize(name):
    return _strip_illegal(name) or "novel"


def _volume_filename(title_safe, suffix):
    """拼「书名 + 卷序后缀」的 EPUB 文件名，非法字符在这里清掉。

    不把清洗放进 _volume_suffix：同一个后缀还要当界面上的卷标签用（见循环里的
    vol_label），在那里把 "Re:zeropedia 公式书" 显示成 "Rezeropedia 公式书" 是另一回事，
    不该为了迁就文件名牺牲显示。

    合法后缀下结果与旧写法逐字节一致（后缀本就以空格开头，strip 后再补回同一个空格），
    故已经下好的书不会被误判成新任务重下。
    """
    label = _strip_illegal(suffix)
    return f"{title_safe} {label}.epub" if label else f"{title_safe}.epub"


def _is_complete_epub(path):
    """已存在的目标是否是一本**完整**的 epub。

    合成中途被打断（关窗、崩溃、取消）会留下半截文件。只看 exists() 的话，这一卷会被
    当成「已经下过」永久跳过，用户始终拿不到书。ZIP 的中央目录记在文件末尾，被截断的
    文件里找不到它，is_zipfile 会返回 False。
    """
    try:
        return zipfile.is_zipfile(path)
    except OSError:
        return False


def _backfill_skipped_bodies(volumes, skipped_indexes, nid, fetcher, tmpdir):
    """给「逐卷 EPUB 已存在、本次因而被跳过」的卷补下正文，返回补完后仍为空的章节。

    --merge 的合并本是用内存里的 Chapter 对象合成的，而被跳过的卷本次进程一次
    download_chapter 都没跑过，chapter.html 还是空串。不补就合并，用户拿到的是一本
    每章只有标题、没有正文的书，退出码却是 0——正是本文件要根除的那种「静默产出残次品」。

    逐卷路径容忍个别章节下载失败（那是一次真实的下载错误，成品仍有价值），这里不同：
    这些正文**从来没被尝试下载过**，补不齐说明网络确实有问题。此时宁可不出合并本，
    也不出一本空书——逐卷 EPUB 都还在，用户重跑一次即可。
    """
    still_empty = []
    for vi, vol in enumerate(volumes, start=1):
        if vi not in skipped_indexes:
            continue
        for ch in vol.chapters:
            if ch.html:
                continue
            try:
                download_chapter(ch, nid, fetcher, tmpdir)
            except Exception as e:
                print(f"  [ERR] 合并本补下章节失败：{ch.title or ch.id}（{e}）")
            if not ch.html:
                still_empty.append(ch)
    return still_empty


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


def _cloudflare_block_msg(nid, detail=""):
    """把「整本小说被站点 Cloudflare 防火墙封禁」转成面向用户的明确提示。

    linovelib 对个别编号（多为被要求下架/限制的主流授权书，如 无职转生 主篇 id=2013）
    的 /novel/{nid}/ 全部页面统一返回 403 Attention Required：脚本、浏览器渲染、带
    cf_clearance 的同会话访问都拿不到。它不是网络波动，换头/重试/加等待无解，只能让用户
    换一个未被屏蔽的编号或卷。这里把原始「403 Client Error」异常翻译成可行动的说明。
    """
    lines = [
        f"这本小说(id={nid})的页面被站点防火墙拦下了（{detail or '403 Forbidden'}）。",
        "它对该编号的所有页面（落地页/目录页/卷页/章节页）都会返回 “Attention Required”。",
        "这是站点侧针对特定作品的封禁，脚本重试或换浏览器都无法绕过——只能换书下载。",
    ]
    return "\n".join(lines)


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


def _resolve_identifier(identifier, fetcher=None):
    """把编号或书名解析为【确定的小说编号】。

    - 纯编号直接返回，不启动浏览器；书名用本站浏览器搜索解析（多候选时让用户选择，
      交互发生在解析阶段，从而保证「书名先筛选、编号直达卷数」在解析后才轮到选卷）。
    - 无 playwright 时 route 站外引擎兜底（resolve_id 内部自行 try/except）。
    - 解析失败（未找到/站点拦截等）抛出 ResolveError，由调用方决定如何呈现。
    供 CLI 与交互启动器共用，保证两条入口的顺序一致。
    """
    identifier = (identifier or "").strip()
    if identifier.isdigit():
        return identifier
    if fetcher is None:
        fetcher = Fetcher()
    browser = None
    try:
        from linovelib.render import RenderFetcher
        browser = RenderFetcher(headless=True)
    except Exception:
        browser = None
    try:
        return resolve_id(identifier, fetcher, browser=browser)
    finally:
        if browser is not None:
            browser.close()


def main(argv=None, *, observer=None, cancel_event=None):
    print(f"轻小说下载器 v{__version__}")
    args = build_parsed_args(argv)

    def _cancel_if_requested():
        if cancel_event is not None and cancel_event.is_set():
            emit(observer, DownloadEvent(
                "cancelled", message="已在下载准备阶段安全取消。"))
            return True
        return False

    if _cancel_if_requested():
        return 130
    if not args.novel and not args.name:
        print("请提供 --novel <编号> 或 --name <书名>。")
        return 2

    # 检测到目标 EPUB 已存在且未 --force：所有下载方式的通用跳过。这里放在最前，
    # 单文件 --out 已完整时零网络请求直接返回；默认逐卷/合并本则在下方逐卷检查。
    # 判据与下方两处闸门一致，用的是「是不是一本完整的 epub」而非 exists()：半截文件
    # （合成中途被打断、或用户手放的同名占位）若被当成「下过了」，就会永久跳过。
    if args.out and not args.force:
        if _is_complete_epub(args.out):
            print(f"已存在，跳过：{args.out}")
            return 0
        if pathlib.Path(args.out).exists():
            print(f"已存在但不完整，重新生成：{args.out}")

    # 卷页(vol_XXX.html)偶发慢响应，默认 timeout=15 会频繁超时浪费重试；提到 30s
    # 让慢但正常的响应直接成功（页面 26.8s 成功过）。章节页都快，30s 不影响它们。
    fetcher = Fetcher(delay=args.delay, retries=8, timeout=30)

    # 书名→编号：本站 /S6/ 只对真实浏览器返回结果（脚本 requests 永久空壳；站外搜索引擎
    # 限流/索引缺失/放水 site: 不可靠）。所以按书名解析时用 RenderFetcher 渲染本站搜索——
    # 这是自站搜索、参考无关，绝非用参考书。纯编号直接透传，不启动浏览器；无 playwright 时
    # 退回站外引擎兜底（resolve_id 内部自行 try/except）。_resolve_identifier 封装该逻辑，
    # 供 CLI 与交互启动器共用，保证「书名先筛选、编号直达卷数」的顺序一致。
    identifier = args.novel or args.name
    try:
        nid = _resolve_identifier(identifier, fetcher)
    except ResolveError as e:
        print(e)
        return 2

    try:
        novel = fetch_novel(nid, fetcher)
    except CloudflareBlockedError as e:
        print(_cloudflare_block_msg(nid, str(e)))
        return 1
    print(f"小说：{novel.title}  作者：{novel.author}  (id={nid})")

    # 正文按【参考无关】方式还原真序：纯 requests 抓每页 #TextContent，复用站点/社区公开的
    # Fisher-Yates 逆置换把「每页前 20 段之后被洗牌的后缀」还原（见 fetcher.get_page_body），
    # downloader 再按文本去重克隆段。参考书【不参与下载/比较】——它只作为外部核对手段，
    # 绝不进入项目协助下载（用户要求：参考书只是比对爬取结果，不能作为项目内依赖，更不能跨卷）。
    # 若确需用正版 EPUB 做最终顺序校正，请在下载完成后【显式】使用 reorder_epub 后处理工具。
    aligner = None
    print("正文顺序：纯 requests + Fisher-Yates 反洗牌（参考无关），按文本去重克隆段。")
    print("提示：参考书不参与下载；如需最终顺序校正，请用 reorder_epub（显式 --reference，仅外部核对用）。")

    try:
        catalog_html = fetcher.get_html(f"https://www.linovelib.com/novel/{nid}/catalog")
    except CloudflareBlockedError as e:
        print(_cloudflare_block_msg(nid, str(e)))
        return 1
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
            emit(observer, DownloadEvent("epub_written", output_path=str(p),
                                         message=f"已生成：{p}"))
        except Exception as e:
            print(f"生成 EPUB 失败：{out}（{e}）")

    tmpdir = CACHE_DIR
    tmpdir.mkdir(exist_ok=True)
    _sweep_temp(CACHE_DIR)  # 清掉被强行中断而残留的 .epub.tmp
    failed = []
    title_safe = _sanitize(novel.title)
    # 默认输出目录 download/小说/<小说标题>/；--out 时 folder 为 None（不逐卷合成）。
    folder = None if args.out else (NOVEL_DIR / title_safe)
    if folder is not None:
        folder.mkdir(parents=True, exist_ok=True)
        _sweep_temp(folder)

    # 已下载过的目标 EPUB：检测到就跳过（所有下载方式都适用；--force 强制重新下载覆盖）。
    skipped = []

    def _skip(existing):
        skipped.append(existing)
        print(f"已存在，跳过：{existing}")

    # 在公布下载事件前先过滤已经生成 EPUB 的卷，避免界面出现永远停在“等待中”的旧章节。
    volume_outputs = {}
    skipped_volume_indexes = set()
    for vi, vol in enumerate(volumes, start=1):
        out = None
        if folder is not None:
            out = folder / _volume_filename(
                title_safe, _volume_suffix([vol], novel.title))
            if not args.force:
                if _is_complete_epub(out):
                    _skip(out)
                    skipped_volume_indexes.add(vi)
                elif out.exists():
                    # 上次合成到一半被打断留下的半截文件：不能当成「下过了」。
                    print(f"已存在但不完整，重新生成：{out}")
        volume_outputs[vi] = out

    total_chapters = sum(
        len(vol.chapters) for vi, vol in enumerate(volumes, start=1)
        if vi not in skipped_volume_indexes
    )
    completed_chapters = 0
    active_volume_count = len(volumes) - len(skipped_volume_indexes)
    emit(observer, DownloadEvent(
        "download_started", total=total_chapters,
        message=f"准备下载 {active_volume_count} 卷、{total_chapters} 章"))
    for vi, vol in enumerate(volumes, start=1):
        if vi in skipped_volume_indexes:
            continue
        for ch in vol.chapters:
            emit(observer, DownloadEvent(
                "chapter_pending", volume_index=vi, volume_title=vol.title,
                chapter_id=ch.id, chapter_title=ch.title,
                total=total_chapters))

    for vi, vol in enumerate(volumes, start=1):
        # 进度按【当前卷】显示：只显示本卷内的 X/Y（每卷各自计数），
        # 不再显示 1/316 这种跨卷总数——那样看不出进度发生在哪一卷。
        vol_label = _volume_suffix([vol], novel.title).strip() or f"第{vi}卷"
        out = volume_outputs[vi]
        if vi in skipped_volume_indexes:
            continue
        vol_total = len(vol.chapters)
        for ci, ch in enumerate(vol.chapters, start=1):
            emit(observer, DownloadEvent(
                "chapter_started", volume_index=vi, volume_title=vol.title,
                chapter_id=ch.id, chapter_title=ch.title,
                completed=completed_chapters, total=total_chapters))
            try:
                download_chapter(ch, nid, fetcher, tmpdir)
                completed_chapters += 1
                print(f"  [OK] {vol_label} 章节 {ci}/{vol_total} 完成：{ch.title if ch.title else ch.id}")
                emit(observer, DownloadEvent(
                    "chapter_finished", volume_index=vi, volume_title=vol.title,
                    chapter_id=ch.id, chapter_title=ch.title,
                    completed=completed_chapters, total=total_chapters))
            except Exception as e:
                failed.append((ch.id, ch.title))
                print(f"  [ERR] {vol_label} 章节 {ci}/{vol_total} 失败：{ch.title or ch.id}（{e}）")
                emit(observer, DownloadEvent(
                    "chapter_failed", volume_index=vi, volume_title=vol.title,
                    chapter_id=ch.id, chapter_title=ch.title,
                    completed=completed_chapters, total=total_chapters,
                    message=str(e)))
            # 在每次章节处理完后检查取消：让当前章读完再停（安全取消在章节边界生效）。
            if cancel_event is not None and cancel_event.is_set():
                emit(observer, DownloadEvent(
                    "cancelled", volume_index=vi, volume_title=vol.title,
                    completed=completed_chapters, total=total_chapters,
                    message="已在章节边界安全取消下载。"))
                return 130
        if out is not None:
            # 该卷已下完 → 立即合成该卷 EPUB（不等其余卷），封面用该卷自己的。
            _build(_novel_subset(novel, [vol], f"vol{vi}"), out, _vol_cover(vol))

    # 单文件 --out：全部下完再合成一个（单卷=该卷，多卷=合并本；尊重用户指定单一目标文件）。
    # 存在性在上方已提前处理（已存在 → 已 return 0），此处必定要构建（首次或 --force 覆盖）。
    if args.out:
        sub = _novel_subset(novel, volumes, "book" if len(volumes) == 1 else "merged")
        _build(sub, args.out, _vol_cover(volumes[0]) if volumes else None)

    # 多卷 & 默认目录：不再询问。仅当显式 --merge 时才额外补一份整本合并 EPUB。
    if folder is not None and len(volumes) > 1 and args.merge:
        out = folder / _volume_filename(
            title_safe, _volume_suffix(volumes, novel.title))
        if not args.force and _is_complete_epub(out):
            _skip(out)
        else:
            if out.exists() and not args.force:
                print(f"已存在但不完整，重新生成：{out}")
            # 合并本的正文来自内存里的 Chapter 对象，而被跳过的卷（逐卷 EPUB 已存在）
            # 本次一页都没抓过，chapter.html 还是空串。直接合并会静默产出一本每章只有
            # 标题的空书，还报成功——先把这些卷的正文补齐再合并。
            empty = _backfill_skipped_bodies(
                volumes, skipped_volume_indexes, nid, fetcher, tmpdir)
            if empty:
                print(f"合并本有 {len(empty)} 章正文为空（补下失败），已放弃生成合并本，"
                      f"以免产出一本只有标题的书；逐卷 EPUB 不受影响，可稍后重跑。")
                if failed:
                    print("以下章节未能下载：")
                    for cid, title in failed:
                        print(f"  {cid} {title}")
                return 1
            _build(_novel_subset(novel, volumes, "merged"), out,
                   _vol_cover(volumes[0]) if volumes else None)

    # 什么都没写、也没跳过任何已存在目标 → 视为无产出，返回非 0。
    #（若目标都已存在而全部跳过，则属正常「都有、无需再下」，返回 0。）
    if not written and not skipped:
        return 1

    if failed:
        print("以下章节未能下载：")
        for cid, title in failed:
            print(f"  {cid} {title}")
    emit(observer, DownloadEvent(
        "finished", completed=completed_chapters, total=total_chapters,
        message="下载结束。" if not failed else f"下载结束，{len(failed)} 章失败。"))
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
