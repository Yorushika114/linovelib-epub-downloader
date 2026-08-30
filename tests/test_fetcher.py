import io
import requests
import pytest
from PIL import Image
from linovelib.fetcher import Fetcher


class FakeResp:
    def __init__(self, content=b"<html>ok</html>", status=200):
        self.content = content
        self.status_code = status
        self.text = content.decode("utf-8", "replace")

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError("boom")


class FakeSession:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = 0
        self.last_headers = {}

    def request(self, method, url, timeout=None, **kw):
        self.calls += 1
        self.last_headers = kw.get("headers", {})
        r = self.responses.pop(0)
        if isinstance(r, Exception):
            raise r
        return r


def test_get_html_returns_text():
    f = Fetcher(session=FakeSession([FakeResp()]))
    assert f.get_html("http://x") == "<html>ok</html>"


def test_get_html_retries_then_succeeds():
    f = Fetcher(delay=0, retries=3, session=FakeSession(
        [requests.ConnectionError("x"), FakeResp()]))
    assert f.get_html("http://x") == "<html>ok</html>"


def test_get_html_raises_after_retries_exhausted():
    f = Fetcher(delay=0, retries=2, session=FakeSession(
        [requests.ConnectionError("x"), requests.ConnectionError("x")]))
    with pytest.raises(requests.ConnectionError):
        f.get_html("http://x")


def test_is_valid_image():
    buf = io.BytesIO()
    Image.new("RGB", (10, 10)).save(buf, format="JPEG")
    assert Fetcher(delay=0).is_valid_image(buf.getvalue()) is True
    assert Fetcher(delay=0).is_valid_image(b"not an image") is False


def test_get_sets_referer_for_hotlink_cdn():
    s = FakeSession([FakeResp()])
    Fetcher(delay=0, session=s).get_html("http://x")
    assert s.last_headers["Referer"] == "https://www.linovelib.com/"


def test_get_html_sends_full_browser_headers():
    # 补全浏览器头以规避站点对脚本式请求的正文截断
    s = FakeSession([FakeResp()])
    Fetcher(delay=0, session=s).get_html("http://x")
    h = s.last_headers
    assert "Accept" in h
    assert "Sec-Fetch-Dest" in h
    assert "Sec-Fetch-Mode" in h
    assert "zh-CN" in h.get("Accept-Language", "")
    assert "Accept-Encoding" not in h  # 留给 requests 自行协商


def test_get_bytes_keeps_referer_without_browser_headers():
    # 图片请求保持极简：带防盗链 Referer，不带浏览器头
    s = FakeSession([FakeResp()])
    Fetcher(delay=0, session=s).get_bytes("http://img/1.jpg")
    h = s.last_headers
    assert h["Referer"] == "https://www.linovelib.com/"
    assert "Accept" not in h
    assert "Sec-Fetch-Dest" not in h


def test_get_page_body_extracts_current_html_without_network_access():
    html = ("<html><body><h1>序章</h1><div id='TextContent'>"
            "<p>正确首段。</p><p>正确后段。</p>"
            "<img data-src='/images/one.jpg'/></div></body></html>").encode("utf-8")
    f = Fetcher(delay=0, retries=1, session=FakeSession([FakeResp(html)]))

    title, paragraphs, images = f.get_page_body("https://example.invalid/123.html")

    assert title == "序章"
    assert paragraphs == ["正确首段。", "正确后段。"]
    assert images == ["https://example.invalid/images/one.jpg"]
