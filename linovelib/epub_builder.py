import os
import pathlib
import re
import tempfile
import time
import zipfile
from ebooklib import epub
from .paths import CACHE_DIR

# 轻小说版式：正文两端对齐、首行缩进两字、行距舒适、章节标题分页居中、
# 插图/封面居中等比缩放。目标品质对齐市面上规范 EPUB（如 calibre 生成版）。
STYLESHEET = """\
body {
  line-height: 1.7;
  text-align: justify;
  color: #111;
  margin: 0 2%;
  padding: 0;
}
h1.chapter-title {
  page-break-before: always;
  text-align: center;
  font-size: 1.6em;
  font-weight: bold;
  line-height: 1.4;
  margin: 0 0 1.5em 0;
}
h1.chapter-title:first-child {
  page-break-before: auto;
}
p {
  text-indent: 2em;
  margin: 0 0 0.4em 0;
  line-height: 1.7;
}
figure {
  margin: 1em 0;
  text-align: center;
}
figure.cover {
  margin: 0;
  padding: 0;
}
figure.cover img {
  width: 100%;
  max-width: 100%;
  height: auto;
  display: block;
  margin: 0;
}
figure.illust img,
p img {
  display: block;
  margin: 1em auto;
  max-width: 100%;
  height: auto;
}
"""


def build_epub(novel, out_path, cover_data=None):
    if not novel.volumes or not any(ch for v in novel.volumes for ch in v.chapters):
        raise ValueError("没有可写入的章节，请先绑定 novel.volumes")

    book = epub.EpubBook()
    book.set_identifier(f"linovelib-{novel.id}")
    book.set_title(novel.title)
    book.set_language("zh-CN")
    if novel.author:
        book.add_metadata("DC", "creator", novel.author)

    if cover_data:
        book.set_cover("cover.jpg", cover_data)

    # 样式表
    css = epub.EpubItem(uid="style", file_name="styles/style.css",
                        media_type="text/css", content=STYLESHEET.encode("utf-8"))
    book.add_item(css)

    # 去重注册所有图片资产
    seen = set()
    for vol in novel.volumes:
        for ch in vol.chapters:
            for asset in ch.image_assets:
                if asset.epub_path in seen:
                    continue
                seen.add(asset.epub_path)
                it = epub.EpubImage()
                it.file_name = asset.epub_path
                it.media_type = _guess_type(asset.epub_path)
                it.content = asset.data
                book.add_item(it)

    # 章节（每卷一节）
    toc = []
    flat_items = []
    title_map = {}
    if cover_data:
        title_map["cover.xhtml"] = "封面"
    for vi, vol in enumerate(novel.volumes, start=1):
        vol_items = []
        for ci, ch in enumerate(vol.chapters, start=1):
            file_name = f"vol{vi}_ch{ci}.xhtml"
            item = epub.EpubHtml(title=ch.title or f"章节{ci}",
                                 file_name=file_name, lang="zh-CN")
            item.content = (f'<h1 class="chapter-title">{ch.title}</h1>{ch.html}')
            book.add_item(item)
            vol_items.append(item)
            flat_items.append(item)
            title_map[file_name] = ch.title or f"章节{ci}"
        toc.append((epub.Section(vol.title or f"第{vi}卷"), vol_items))

    book.add_item(epub.EpubNcx())
    book.add_item(epub.EpubNav())
    book.toc = tuple(toc)
    book.spine = ["cover"] + flat_items if cover_data else flat_items

    # 先由 ebooklib 写出（保证 OPF/NCX/Nav/mimetype 正确），再对每个 XHTML
    # 注入 lang=zh-CN、<title> 与样式表链接，并包住封面图。ebooklib 的
    # EpubHtml 只会生成 `lang="en"` 与空 `<head/>`，无法直接控制 head。
    # ebooklib 先写到这里（缓存目录 _tmp_dl，已 gitignore），再交给 _finalize_xhtml 重写版式
    # 落到目标。两者可以在不同盘：_finalize_xhtml 自己会在**目标同目录**再开一个临时文件
    # 并 os.replace 过去，原子性由那一步保证，与本文件放哪儿无关。中断留下的残留都在
    # 缓存目录（本文件）或由 _finalize_xhtml 自行清掉（目标目录那个），不会污染成品目录。
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(CACHE_DIR), suffix=".epub.tmp")
    os.close(fd)
    try:
        # WinError 32（PermissionError）：Windows Defender/杀软会在新建的 .epub.tmp
        # 上做短暂实时扫描，把刚写的文件锁住一瞬导致写入失败。重试即可（1 秒内释放）。
        for attempt in range(8):
            try:
                epub.write_epub(tmp, book)
                break
            except PermissionError as e:
                if attempt == 7:
                    raise
                time.sleep(0.5)
        # 目标 .epub 可能被阅读器/calibre 打开（写共享被拒 → PermissionError，无法覆盖）。
        # 此时不要静默失败，改写到「同名 + 空格 + 序号」的替身文件，并明确告知用户。
        # 走带重试的版本：杀软在同一目录新建的 .epub.tmp 上也会锁一瞬，直接判「被占用」
        # 会凭空造出替身文件，用户下次重跑还找不到原文件名而整本重下。
        dest = out_path
        try:
            _finalize_xhtml_retrying(tmp, out_path, title_map)
        except PermissionError:
            dest = _alternate_path(out_path)
            _finalize_xhtml_retrying(tmp, dest, title_map)
            print(f"目标文件已被其他程序占用（可能是阅读器正在打开这个 epub），已另存为：{dest}")
        return dest
    finally:
        # 尽量删除临时文件；Windows Defender/杀软可能短暂锁定刚写好的 .epub.tmp
        # 导致删除时 PermissionError，重试几次再放弃（残留在缓存目录里，无害）。
        for _ in range(6):
            try:
                if os.path.exists(tmp):
                    os.remove(tmp)
                break
            except PermissionError:
                if _ == 5:
                    break
                time.sleep(0.25)
            except Exception:
                break


def _alternate_path(path):
    """目标被占用时，返回一个不会被占用的替身路径：`书名.epub` -> `书名. 1.epub`、
    `书名. 2.epub`……直到找到一个空位为止。

    判空用 os.path.exists 而不是自己 open 试：原先只 catch FileNotFoundError，而候选
    路径**本身被别的程序占用**时 open 抛的是 PermissionError，它会直接穿出这个循环，
    替身机制在自己该起作用的那种情况下失效。
    """
    p = pathlib.Path(path)
    for i in range(1, 1000):
        cand = p.with_name(f"{p.stem}. {i}{p.suffix}")
        if not os.path.exists(cand):
            return cand
    return p.with_name(f"{p.stem}. {int(time.time())}{p.suffix}")


def _finalize_xhtml_retrying(src, dst, title_map, attempts=8):
    """`_finalize_xhtml` 的带重试版本。

    这条路径上会新建两个文件，都可能被 Windows Defender 的实时扫描短暂锁住：缓存目录
    里那个（调用方已自行重试 8 次）和 **dst 同目录**下的这一个。后者原先没有重试，一次
    杀软瞬时锁就会被读成「目标 epub 正被阅读器占用」，于是白白改写成一个「书名. 1.epub」
    替身——用户下次重跑找不到原文件名，还会整本重下。

    真正的占用（阅读器确实开着这个文件）重试多少次都还是 PermissionError，所以这个循环
    不会掩盖「该走替身」的那种情况。
    """
    for attempt in range(attempts):
        try:
            _finalize_xhtml(src, dst, title_map)
            return
        except PermissionError:
            if attempt == attempts - 1:
                raise
            time.sleep(0.5)


def _finalize_xhtml(src, dst, title_map):
    """把临时 epub 按标准版式重写后**原子地**放到 dst。

    先写到 dst 同目录下的临时文件、再 os.replace 过去：同目录即同卷，replace 是原子的。
    直接往 dst 写的问题在于，合成中途被打断（关窗、崩溃、取消）会留下一个半截 epub，
    而「已存在就跳过」的闸门会把它当成下过的书永久跳过——用户重跑多少次都拿不到书。

    失败时清掉半截的临时文件再抛：调用方据此走「目标被占用 → 另存为替身」的分支。
    """
    dst = pathlib.Path(dst)
    fd, tmp = tempfile.mkstemp(dir=str(dst.parent), suffix=".epub.tmp")
    os.close(fd)
    try:
        with zipfile.ZipFile(src) as zin, \
                zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED) as zout:
            for item in zin.infolist():
                name = item.filename
                data = zin.read(name)
                if name.endswith(".xhtml"):
                    data = _inject_head(name, data, title_map)
                zout.writestr(item, data)
        os.replace(tmp, dst)
    except BaseException:
        try:
            os.remove(tmp)
        except OSError:
            pass
        raise


def _inject_head(name, content, title_map):
    txt = content.decode("utf-8")
    base = name.rsplit("/", 1)[-1]
    # 中文书统一 zh-CN
    txt = txt.replace('lang="en" xml:lang="en"', 'lang="zh-CN" xml:lang="zh-CN"')
    # 覆盖封面图：包一层 figure.cover
    if base == "cover.xhtml":
        txt = txt.replace('<img src="cover.jpg" alt="Cover"/>',
                          '<figure class="cover"><img src="cover.jpg" alt="Cover"/></figure>')
        # 覆盖默认的英文 <title>Cover</title>（若存在），换成中文
        txt = txt.replace("<title>Cover</title>", "<title>封面</title>")
    # 在 <head> 后插入字符集与样式表链接（ebooklib 生成的 head 形如
    # `<head>\n    <title>…</title>\n  </head>`，与简单的 `<head/>` 不同）。
    head_open = re.search(r"<head[^>]*>", txt)
    if head_open:
        pos = head_open.end()
        inject = ('\n    <meta charset="utf-8"/>'
                  '\n    <link rel="stylesheet" type="text/css" href="styles/style.css"/>')
        txt = txt[:pos] + inject + txt[pos:]
    return txt.encode("utf-8")


def _guess_type(path):
    if path.endswith(".png"):
        return "image/png"
    if path.endswith(".gif"):
        return "image/gif"
    if path.endswith(".webp"):
        return "image/webp"
    return "image/jpeg"
