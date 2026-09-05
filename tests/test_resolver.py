import pytest
from linovelib.resolver import (resolve_id, search_by_name, _search_hits,
                                _pick_hit, _parse_search_results,
                                _browser_site_hits, fetch_novel,
                                search_hits, is_exact_match)
from linovelib.fetcher import CloudflareBlockedError


class FakeFetcher:
    def __init__(self, responses):
        self._responses = responses
        self.calls = []

    def get_html(self, url, method="GET", **kw):
        self.calls.append((url, method, kw))
        return self._responses.pop(0).decode("utf-8", "replace")


class FakeBrowser:
    """模拟 RenderFetcher.search_html：返回渲染后的搜索结果页 HTML。"""
    def __init__(self, html):
        self.html = html

    def search_html(self, name):
        return self.html


# 与真实站点搜索结果页结构一致：div.search-result-list（含 h2.tit 标题），
# 另附带「推荐」轮播——该轮播不应被当成候选。
SEARCH_HTML = '''
<div class="search-result-list clearfix">
  <div class="imgbox fl se-result-book"><a href="/novel/2013.html"><img src="..."/></a></div>
  <div class="fl se-result-infos">
    <h2 class="tit"><a href="/novel/2013.html"><span class="hot">无职转生</span> ～到了异世界就拿出真本事～</a></h2>
    <div class="bookinfo"><a href="#">理不尽な孙の手</a><em>|</em><span>角川文库</span></div>
  </div>
</div>
<div class="search-result-list clearfix">
  <div class="imgbox fl se-result-book"><a href="/novel/4325.html"><img src="..."/></a></div>
  <div class="fl se-result-infos">
    <h2 class="tit"><a href="/novel/4325.html">无职转生 ～蛇足篇～</a></h2>
  </div>
</div>
<!-- 侧栏推荐轮播：与搜索结果无关，不能当成候选 -->
<a href="/novel/2.html">果然我的青春恋爱喜剧搞错了</a>
<a href="/novel/3.html">在地下城寻求邂逅是否搞错了什么</a>
'''


def test_resolve_numeric_passthrough_no_network():
    f = FakeFetcher([])
    assert resolve_id("2013", f) == "2013"
    assert f.calls == []


def test_search_by_name_extracts_id_from_results():
    # 站点接口（脚本端）仅作廉价尝试：任一 /novel/{id}.html 锚点即可给出候选 id。
    html = '<a href="/novel/2013.html">无职转生</a>'.encode("utf-8")
    f = FakeFetcher([html])
    assert search_by_name("无职转生", f) == "2013"


def test_browser_site_search_is_primary_and_scoped():
    # 浏览器文件：应先走 _browser_site_hits 拿到权威结果，且只认 search-result-list，
    # 不把「推荐」轮播（id 2 / 3）当成候选——轮播会干扰选书。
    b = FakeBrowser(SEARCH_HTML)
    assert search_by_name("无职转生", FakeFetcher([]), browser=b) == "2013"


def test_parse_search_results_scopes_to_result_list():
    hits = _parse_search_results(SEARCH_HTML)
    ids = [h.id for h in hits]
    assert ids == ["2013", "4325"]
    # 标题取自 h2.tit a（含高亮 span），而非空的封面链接。
    assert hits[0].title == "无职转生 ～到了异世界就拿出真本事～"


def test_browser_site_hits_uses_browser_and_parses():
    b = FakeBrowser(SEARCH_HTML)
    hits = _browser_site_hits("无职转生", b)
    assert [h.id for h in hits] == ["2013", "4325"]


def test_search_falls_back_to_bing_when_browser_absent_or_empty():
    site_empty = b"<html>no results</html>"
    bing_html = ('<li class="b_algo"><h2><a href="https://www.linovelib.com/novel/'
                 '2013.html">无职转生</a></h2></li>').encode("utf-8")
    f = FakeFetcher([site_empty, bing_html])
    assert search_by_name("无职转生", f) == "2013"

    # 站点接口与 Bing 都空 → 回退 DuckDuckGo。
    ddg_html = ('<div class="result"><a class="result__a" href="'
                '//duckduckgo.com/l/?uddg=https%3A%2F%2Fwww.linovelib.com%2Fnovel%2F'
                '4325%2Ehtml">无职转生 蛇足篇</a></div>').encode("utf-8")
    f2 = FakeFetcher([site_empty, site_empty, ddg_html])
    assert search_by_name("无职转生", f2) == "4325"


def test_search_empty_returns_blank():
    f = FakeFetcher([b"<html>no results</html>",
                     b"<html>no results</html>",
                     b"<html>no results</html>"])
    assert search_by_name("不存在", f) == ""


def test_browser_broken_falls_back_to_engine():
    # 浏览器缺席/抛错时（如无 playwright），应静默退回站外引擎，不抛出。
    class BadBrowser:
        def search_html(self, name):
            raise RuntimeError("no playwright")

    bing_html = ('<li class="b_algo"><h2><a href="https://www.linovelib.com/novel/'
                 '2013.html">无职转生</a></h2></li>').encode("utf-8")
    f = FakeFetcher([bing_html])
    assert search_by_name("无职转生", f, browser=BadBrowser()) == "2013"


def test_browser_authoritative_empty_stops_fallthrough():
    # 浏览器站点搜索【成功返回空】（站点明确「查无此书」）即权威结论并立即终止，
    # 不再回落站外引擎——否则用户在国内网络下会被 Bing/DDG 长时间拖住，表现为
    # 「按书名搜索无结果后无法继续搜索/卡死」。
    class EmptySearchHtml:
        def search_html(self, name):
            return "<html><body>没有找到相关作品</body></html>"

    f = FakeFetcher([])  # 若错误回落到外部引擎，get_html 会因弹空列表抛 IndexError
    assert _search_hits("不存在的书名", f, browser=EmptySearchHtml()) == []
    assert f.calls == []  # 权威空结果不应触发任何外部请求


def test_browser_cf_challenge_falls_back_to_external():
    # 浏览器返回 Cloudflare 硬拦截页（Attention Required）不是「查无此书」：
    # 应把它当作「无法判定」，静默回落站外引擎兜底，而不是误报未找到。
    class CfChallengeBrowser:
        def search_html(self, name):
            return "<html><title>Attention Required! | Cloudflare</title></html>"

    f = FakeFetcher([('<a href="/novel/2013.html">无职转生</a>').encode("utf-8")])
    hits = _search_hits("无职转生", f, browser=CfChallengeBrowser())
    assert [h.id for h in hits] == ["2013"]


def test_multiple_candidates_prefers_exact_title(monkeypatch):
    monkeypatch.setattr("sys.stdin.isatty", lambda: False)
    html = ('<a href="/novel/3095.html">败北女角太多了</a>'
            '<a href="/novel/9999.html">败北女角太多了 续集</a>').encode("utf-8")
    f = FakeFetcher([html])
    assert search_by_name("败北女角太多了", f) == "3095"


def test_multiple_candidates_asks_via_tty(monkeypatch):
    monkeypatch.setattr("sys.stdin.isatty", lambda: True)
    monkeypatch.setattr("builtins.input", lambda prompt="": "2")
    html = ('<a href="/novel/3095.html">败北女角太多了 上</a>'
            '<a href="/novel/9999.html">败北女角太多了 下</a>').encode("utf-8")
    f = FakeFetcher([html])
    assert search_by_name("败北女角", f) == "9999"


def test_pick_hit_single_returns_that_hit():
    hit = type("H", (), {"id": "1", "title": "a"})()
    assert _pick_hit("x", [hit]).id == "1"


def test_search_hits_returns_deduped_candidates_without_picking():
    # 前端（WPF）需要的是候选列表本身，而非自动选取的结果——search_hits 不做选取。
    hits = search_hits("无职转生", FakeFetcher([]), browser=FakeBrowser(SEARCH_HTML))
    assert [h.id for h in hits] == ["2013", "4325"]


def test_is_exact_match_normalizes_and_ignores_brackets():
    assert is_exact_match("无职转生 ～蛇足篇～", "无职转生 ～蛇足篇～") is True
    assert is_exact_match("无职转生", "无职转生 ～到了异世界就拿出真本事～") is False


def test_ask_choose_falls_back_when_stdin_unreadable(monkeypatch):
    # 多候选、且 stdin 被当作交互但实际读取失败时（脚本/管道/EOF），
    # 应退回默认候选而非抛 EOFError 崩溃。
    monkeypatch.setattr("sys.stdin.isatty", lambda: True)
    monkeypatch.setattr("builtins.input", lambda prompt="": (_ for _ in ()).throw(EOFError))
    hits = [type("H", (), {"id": "3095", "title": "败北女角太多了 上"})(),
            type("H", (), {"id": "9999", "title": "败北女角太多了 下"})()]
    assert _pick_hit("败北女角", hits).id == "3095"


def test_fetch_novel_propagates_cloudflare_blocked():
    # 落地页与目录页都被站点 Cloudflare 封禁（403 Attention Required）：fetch_novel 应把
    # 专用异常抛给上层，而不是当普通 403 继续重试、刷屏。
    class BlockFetcher:
        def get_html(self, url, **kw):
            raise CloudflareBlockedError(f"{url} blocked")
    with pytest.raises(CloudflareBlockedError):
        fetch_novel("2013", BlockFetcher())
