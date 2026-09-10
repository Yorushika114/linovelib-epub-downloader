"""漫画搜索被 302 重定向到详情页时的候选合成（ComicFetcher.search 的落点识别）。

站点行为与小说站一致：当查询词能唯一定位一部漫画（如完整书名）时，
/search.html 不渲染「條記錄」结果列表，而是直接把页面 302 送到 /detail/{id}.html。

漫画侧此前**完全没有处理这种情况**，后果比小说侧更糟：
- `_result_state` 返回 [0, False]（没有「條記錄」模块）；
- `_wait_search_results` 因 saw_module=False 返回 None，被当成「搜索页根本没渲染」；
- `search()` 据此重建会话重试 3 轮，最终抛 ComicBlockedError，**把一次成功命中
  误报成「Cloudflare 自动化指纹被判定」**，还白白烧掉三轮会话重建。

实测（2026-09-10）：「刀劍神域 Progressive 黃金定律的卡農」提交后 URL 变为
https://www.bilimanga.net/detail/368.html。本测试锁定该落点识别，防止回归。
"""

from comic.fetcher import ComicFetcher, ComicFetcher as _F


# 结构照抄真实详情页（2026-09-10 实地探测）：作者在 h4.book-title，且被
# div.book-title-x > div.book-cell > a.book-layout 逐层包着——不是裸的 div。
DETAIL_HTML = (
    '<!DOCTYPE html><html><head>'
    '<title>刀劍神域 Progressive 黃金定律的卡農漫畫_Sword Art Online、'
    '黃金法則的卡農漫畫_霧月_嗶哩漫畫</title></head>'
    '<body><div class="book-detail"><h1>刀劍神域 Progressive 黃金定律的卡農</h1>'
    '<a class="book-layout"><div class="book-cell"><div class="book-title-x">'
    '<h4 class="book-title">霧月</h4></div></div></a></div></body></html>'
)


def test_redirect_to_detail_page_yields_single_hit():
    """被送到 /detail/{id}.html 时，合成一条命中（编号取自 URL，标题取自详情页）。"""
    hit = _F._redirect_hit("https://www.bilimanga.net/detail/368.html", DETAIL_HTML)
    assert hit is not None
    cid, title, author = hit
    assert cid == 368
    # 标题必须取页面 h1（= 漫画名），不能取 <title> 首段。
    # 站点 <title> 是「{书名}漫畫_{副标题}漫畫_{作者}_嗶哩漫畫」，按首段切分要额外
    # 剥掉「漫畫」后缀，h1 更直接、也更贴合「书名吻合」的判定语义。
    assert title == "刀劍神域 Progressive 黃金定律的卡農"
    assert author == "霧月"


def test_h1_preferred_for_title():
    """详情页 h1 才是漫画名；<title> 首段带「漫畫」后缀，不可直接用作书名。"""
    cid, title, _ = _F._redirect_hit(
        "https://www.bilimanga.net/detail/5.html",
        "<html><head><title>某漫畫_副標漫畫_作者_嗶哩漫畫</title></head>"
        "<body><h1>某</h1></body></html>")
    assert (cid, title) == (5, "某")


def test_title_falls_back_to_document_title_when_h1_missing():
    """h1 缺失时退回 <title>，并剥掉站点拼的「漫畫」后缀。"""
    hit = _F._redirect_hit(
        "https://www.bilimanga.net/detail/7.html",
        "<html><head><title>孤獨搖滾漫畫_はまじあき_嗶哩漫畫</title></head>"
        "<body>no h1</body></html>")
    assert hit is not None
    cid, title, _ = hit
    assert cid == 7
    assert title == "孤獨搖滾"


def test_search_url_not_treated_as_redirect():
    """停在 /search.html（含 0 条）不算命中，避免把「查无此书」误判成候选。"""
    assert _F._redirect_hit(
        "https://www.bilimanga.net/search.html",
        "<html>没有结果</html>") is None


def test_unrelated_redirect_ignored():
    """落到其它页面（非 /detail/{id}.html）不算命中。"""
    assert _F._redirect_hit(
        "https://www.bilimanga.net/", DETAIL_HTML) is None
    assert _F._redirect_hit(
        "https://www.bilimanga.net/read/368/catalog", DETAIL_HTML) is None
