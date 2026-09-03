import re
from bs4 import BeautifulSoup
from .catalog import parse_novel_page
from .models import Novel

SEARCH_URL = "https://www.linovelib.com/S6/"
# 小说落地页 /novel/{nid}.html 偶发被 Cloudflare 机器人挑战(403)拦下，但同样编号的
# 目录页/卷页/章节页全部正常。此时改从目录页解析最小元数据（书名/作者）照常下载。
CATALOG_URL = "https://www.linovelib.com/novel/{nid}/catalog"


class ResolveError(Exception):
    pass


def resolve_id(identifier, fetcher):
    identifier = (identifier or "").strip()
    if identifier.isdigit():
        return identifier
    found = search_by_name(identifier, fetcher)
    if found:
        return found
    raise ResolveError(
        f"未找到名为「{identifier}」的小说，请改用编号，例如 --novel 3095"
    )


def search_by_name(name, fetcher):
    try:
        html = fetcher.get_html(SEARCH_URL, method="POST",
                                data={"searchkey": name, "t_frmsearch": "1"})
    except Exception:
        return ""
    m = re.search(r"/novel/(\d+)\.html", html)
    return m.group(1) if m else ""


def fetch_novel(nid, fetcher):
    try:
        html = fetcher.get_html(f"https://www.linovelib.com/novel/{nid}.html")
        novel = parse_novel_page(html, nid)
        if novel.title:
            return novel
    except Exception:
        pass
    # 落地页被 Cloudflare 挑战拦下(403)，或解析不出书名 → 退回目录页拿最小元数据。
    # 封面由各卷页 og:image 提供（卷页未被拦），小说页封面仅兜底、缺失无碍；
    # 若目录页也失败则在此自然抛出，不静默返回残缺数据。
    return _novel_from_catalog(nid, fetcher)


def _novel_from_catalog(nid, fetcher):
    html = fetcher.get_html(CATALOG_URL.format(nid=nid))
    soup = BeautifulSoup(html, "lxml")
    h1 = soup.find("h1")
    title = h1.get_text(strip=True) if h1 else _title_from_page(soup)
    return Novel(id=str(nid), title=title or str(nid),
                 author=_author_from_page(soup), cover_url="")


def _title_from_page(soup):
    # 目录页 <title> 形如「书包名小说,免费阅读_作者作品_目录页_哩哩轻小说」，
    # 取「小说」之前的文字作为书名，避免把「小说」二字也带进书名。
    t = soup.title.get_text(strip=True) if soup.title else ""
    return re.split(r"小说", t, 1)[0].strip() if "小说" in t else t


def _author_from_page(soup):
    # 作者通常藏在 <title> 的「_作者作品」段，如「_鸭志田一作品」；取不到则空串，
    # 空作者不影响下载/合成（仅少写 EPUB creator 元数据）。
    t = soup.title.get_text(strip=True) if soup.title else ""
    m = re.search(r"_([^_]+)作品", t)
    return m.group(1) if m else ""
