import re
from .catalog import parse_novel_page
from .models import Novel

SEARCH_URL = "https://www.linovelib.com/S6/"


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
    html = fetcher.get_html(f"https://www.linovelib.com/novel/{nid}.html")
    return parse_novel_page(html, nid)
