from linovelib.resolver import resolve_id, search_by_name


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


def test_search_empty_returns_blank():
    f = FakeFetcher([b"<html>no results</html>"])
    assert search_by_name("不存在", f) == ""
