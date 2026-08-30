from bs4 import BeautifulSoup
from .models import Novel, Volume, Chapter

BASE = "https://www.linovelib.com"


def parse_novel_page(html, nid):
    soup = BeautifulSoup(html, "lxml")

    def meta(prop):
        tag = soup.find("meta", attrs={"property": prop})
        if tag and tag.get("content"):
            return tag["content"].strip()
        return ""

    title = meta("og:title") or meta("og:novel:book_name")
    author = meta("og:novel:author")
    cover = meta("og:image")
    return Novel(id=str(nid), title=title, author=author, cover_url=cover)


def parse_catalog(html, nid):
    soup = BeautifulSoup(html, "lxml")
    marker = f"novel/{nid}/"
    volumes = []
    current = None
    # find_all 按文档顺序返回；用 h2 标志当前卷，遇到的本章节链接归入它。
    # 注意：目录页里每个卷的 vol_ 链接会出现多次（上一卷/下一卷导航是空文本副本），
    # 因此不回填 vid，而是在下面用「标题匹配」二遍统计卷号。
    for el in soup.find_all(["h2", "a"]):
        if el.name == "h2":
            current = Volume(title=el.get_text(strip=True))
            volumes.append(current)
            continue
        href = el.get("href")
        if current is None or not href or marker not in href or ".html" not in href:
            continue
        href = href.strip()
        if not href.startswith("http"):
            href = BASE + (href if href.startswith("/") else "/" + href)
        # vol_xxx.html 是卷封面/信息页，不是章节，跳过
        if href.rsplit("/", 1)[-1].startswith("vol_"):
            continue
        cid = href.rsplit("/", 1)[-1].split(".html")[0].split("_")[0]
        current.chapters.append(Chapter(id=cid, url=href,
                                        title=el.get_text(strip=True)))

    # 卷号：目录页里真正的卷页「链接文字」与卷标题(h2)完全一致（如「败北女角太多了！ 1」），
    # 用它去匹配对应卷页链接的 id；空文字的导航副本忽略。按标题匹配而非 DOM 相邻，
    # 可避免因左右相邻的「上一卷/下一卷」链接导致错位。
    vid_by_title = {}
    for a in soup.find_all("a", href=True):
        href = a["href"]
        if "vol_" in href and ".html" in href:
            t = a.get_text(strip=True)
            if t:
                vid_by_title[t] = href.rsplit("/", 1)[-1].split(".html")[0].split("_")[-1]
    for v in volumes:
        v.vid = vid_by_title.get(v.title, "")
    return [v for v in volumes if v.chapters]


def parse_volume_page(html):
    """从卷页 /novel/{nid}/vol_{vid}.html 提取该卷独立封面（og:image）。

    小说页的 og:image（booklist 缩略图）常常不是卷封面，因此下载单卷时以卷页
    封面为准；解析不到则返回空串，由调用方回退到小说页封面。
    """
    soup = BeautifulSoup(html, "lxml")
    tag = soup.find("meta", attrs={"property": "og:image"})
    if tag and tag.get("content"):
        return tag["content"].strip()
    return ""


def parse_volume_chapters(html, nid):
    """从卷页 /novel/{nid}/vol_{vid}.html 解析该卷的章节列表（按文档顺序）。

    目录页偶尔会漏掉个别章节（如败北女角第 4 卷的「～第一败～」cid 181030 不在
    catalog 里），而卷页的章节列表齐全，故下载时以卷页为准。按文档顺序返回，
    过滤掉 vol_ 链接与重复项。
    """
    soup = BeautifulSoup(html, "lxml")
    marker = f"novel/{nid}/"
    chapters = []
    seen = set()
    for a in soup.find_all("a", href=True):
        href = a.get("href")
        if not href or marker not in href or ".html" not in href:
            continue
        href = href.strip()
        if not href.startswith("http"):
            href = BASE + (href if href.startswith("/") else "/" + href)
        if href.rsplit("/", 1)[-1].startswith("vol_"):
            continue
        cid = href.rsplit("/", 1)[-1].split(".html")[0].split("_")[0]
        if cid in seen:
            continue
        seen.add(cid)
        chapters.append(Chapter(id=cid, url=href,
                                title=a.get_text(strip=True) or f"章节{cid}"))
    return chapters
