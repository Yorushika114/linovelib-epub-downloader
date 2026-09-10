"""搜索页 302 重定向到小说详情页时的候选合成（RenderFetcher._redirect_hit）。

站点在查询词能唯一定位一本书（如完整书名）时不渲染结果列表，而是把
/S6/?searchkey=… 直接重定向到 /novel/{id}.html。此时页面没有
div.search-result-list，按结果列表解析会得到 0 条，并被上层误判为
「查无此书」而不再兜底 —— 表现为「正常搜索做不到」。本测试锁定该落点
识别与标题提取，防止回归。
"""

from linovelib.render import RenderFetcher, _require_playwright


DETAIL_HTML = (
    '<!DOCTYPE html><html><head>'
    '<title>败北女角太多了_鸭志田一作品_小学馆_哩哩轻小说</title>'
    '</head><body>...</body></html>'
)


def test_redirect_to_detail_page_yields_single_hit():
    """被送到 /novel/{id}.html 时，合成 (编号, 标题)。"""
    hit = RenderFetcher._redirect_hit(
        "https://www.linovelib.com/novel/3095.html", DETAIL_HTML)
    assert hit is not None
    nid, title = hit
    assert nid == "3095"
    # 标题取 <title> 首段，供上层 is_exact_match 标记「书名吻合」。
    assert title == "败北女角太多了"


def test_search_url_when_no_redirect():
    """停在 /S6/ 结果页（含 0 条）不算命中，避免把「查无此书」误判成候选。"""
    assert RenderFetcher._redirect_hit(
        "https://www.linovelib.com/S6/?searchkey=%E5%88%80%E5%89%91",
        "<html>no results</html>") is None


def test_non_novel_redirect_ignored():
    """落到其它页面（非 /novel/{id}.html）不算命中。"""
    assert RenderFetcher._redirect_hit(
        "https://www.linovelib.com/top.html", DETAIL_HTML) is None


def test_title_entity_and_tag_cleanup():
    """<title> 里的实体与标签需清理为纯文本。"""
    html = ("<html><head><title>&lt;书名&gt; &amp; 副标题_作者作品"
            "</title></head></html>")
    nid, title = RenderFetcher._redirect_hit(
        "https://www.linovelib.com/novel/42.html", html)
    assert nid == "42"
    assert title == "<书名> & 副标题"


def test_require_playwright_raises_with_guidance(monkeypatch):
    """playwright 缺失时构造期即抛错，让调用方走站外引擎兜底。

    若不在构造期校验，RenderFetcher 会「构造成功、搜索时才炸」，
    调用方的 `browser = None` 兜底分支永不触发，缺依赖的机器表现为
    「搜索无结果」而不是「提示安装」。
    """
    import builtins

    real_import = builtins.__import__

    def fake_import(name, *args, **kwargs):
        if name == "playwright" or name.startswith("playwright."):
            raise ImportError("simulated")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", fake_import)
    try:
        _require_playwright()
    except ImportError as e:
        assert "playwright install" in str(e)
    else:
        raise AssertionError("缺少 playwright 时应抛出 ImportError")
