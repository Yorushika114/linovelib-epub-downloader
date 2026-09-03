from linovelib.resolver import resolve_id, search_by_name, _search_hits, _pick_hit


class FakeFetcher:
    def __init__(self, responses):
        self._responses = responses
        self.calls = []

    def get_html(self, url, method="GET", **kw):
        self.calls.append((url, method, kw))
        return self._responses.pop(0).decode("utf-8", "replace")


def test_resolve_numeric_passthrough_no_network():
    f = FakeFetcher([])
    assert resolve_id("3095", f) == "3095"
    assert f.calls == []


def test_search_by_name_extracts_id_from_results():
    html = '<a href="/novel/3095.html">败北女角太多了！</a>'.encode("utf-8")
    f = FakeFetcher([html])
    assert search_by_name("败北女角", f) == "3095"


def test_search_falls_back_to_bing_when_site_empty():
    site_empty = b"<html>no results</html>"
    bing_html = ('<li class="b_algo"><h2><a href="https://www.linovelib.com/novel/'
                 '3095.html">败北女角太多了</a></h2></li>').encode("utf-8")
    f = FakeFetcher([site_empty, bing_html])
    assert search_by_name("败北女角", f) == "3095"
    assert f.calls[0][0] == "https://www.linovelib.com/S6/"
    assert "cn.bing.com" in f.calls[1][0]


def test_search_falls_back_to_ddg_when_bing_empty():
    site_empty = b"<html>no results</html>"
    bing_empty = b"<html>no results</html>"
    ddg_html = ('<div class="result"><a class="result__a" href="'
                '//duckduckgo.com/l/?uddg=https%3A%2F%2Fwww.linovelib.com%2Fnovel%2F'
                '4325%2Ehtml">无职转生</a></div>').encode("utf-8")
    f = FakeFetcher([site_empty, bing_empty, ddg_html])
    assert search_by_name("无职转生", f) == "4325"
    assert "duckduckgo" in f.calls[2][0]


def test_search_empty_returns_blank():
    f = FakeFetcher([b"<html>no results</html>",
                     b"<html>no results</html>",
                     b"<html>no results</html>"])
    assert search_by_name("不存在", f) == ""


def test_multiple_candidates_prefers_exact_title(monkeypatch):
    # 非交互（TTY 不可用）时，多个候选应自动选「标题与书名精确吻合」那条。
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
    hits = _search_hits("x", FakeFetcher([]))  # 空列表，仅验证：单候选直接返回
    hit = _pick_hit("x", [h for h in hits] or [type("H", (), {"id": "1", "title": "a"})()])
    assert hit.id == "1"
