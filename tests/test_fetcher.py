import io
import requests
import pytest
from PIL import Image
from linovelib.fetcher import Fetcher, CloudflareBlockedError


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


# ---- 有界退避（429 / 封顶）----
def _http_error(status, retry_after=None):
    """构造携带响应状态码/头的 HTTPError（含 response 属性），供 _backoff 识别 429。"""
    class R:
        status_code = status
        headers = {"Retry-After": str(retry_after)} if retry_after else {}
    return requests.HTTPError("boom", response=R())


def test_backoff_429_respects_retry_after():
    from linovelib.fetcher import _backoff
    assert _backoff(0, _http_error(429, retry_after=5)) == 5


def test_backoff_429_caps_and_defaults():
    from linovelib.fetcher import _backoff, _429_MAXWAIT
    assert _backoff(0, _http_error(429, retry_after=999)) == _429_MAXWAIT  # 封顶
    assert _backoff(0, _http_error(429)) == 3  # 无 Retry-After -> 短等


def test_backoff_capped_exponential():
    from linovelib.fetcher import _backoff, _MAX_BACKOFF
    assert _backoff(0, _http_error(500)) == 1          # 2**0=1
    assert _backoff(10, _http_error(500)) == _MAX_BACKOFF  # 指数封顶
    assert _backoff(10, requests.ConnectionError("net")) == _MAX_BACKOFF  # 无响应也封顶


def test_get_retries_429_then_succeeds(monkeypatch):
    monkeypatch.setattr("linovelib.fetcher.time.sleep", lambda s: None)
    s = FakeSession([_http_error(429), FakeResp()])
    f = Fetcher(delay=0, retries=3, session=s)
    assert f.get_html("http://x") == "<html>ok</html>"
    assert s.calls == 2  # 一次 429，一次 200


# ---- Cloudflare 按 URL 封禁整本小说（403 Attention Required）----
_CF_BLOCK_HTML = ("<html><head><title>Attention Required! | Cloudflare</title>"
                  "</head><body><h1>Sorry, you have been blocked</h1></body></html>")
CF_BLOCK_TEXT = _CF_BLOCK_HTML.replace("<html>", "<html>\n<!DOCTYPE html>", 1)


def _cf_block_resp(status=403):
    return FakeResp(_CF_BLOCK_HTML.encode("utf-8"), status=status)


def test_is_cloudflare_block_detects_attention_required():
    from linovelib.fetcher import _is_cloudflare_block
    assert _is_cloudflare_block(_cf_block_resp()) is True


def test_is_cloudflare_block_rejects_normal_403():
    from linovelib.fetcher import _is_cloudflare_block
    # 普通 403（非 Cloudflare 拦截页）不应误判为整本封禁——仍走正常重试/报错路径。
    assert _is_cloudflare_block(FakeResp(b"forbidden", status=403)) is False


def test_get_html_raises_cloudflare_blocked_fast():
    # 整本被 Cloudflare 封禁：一把就抛 CloudflareBlockedError，不重试、不刷屏。
    s = FakeSession([_cf_block_resp(), FakeResp()])
    f = Fetcher(delay=0, retries=3, session=s)
    with pytest.raises(CloudflareBlockedError):
        f.get_html("http://x")
    assert s.calls == 1  # 命中即出，不再用剩余重试去试失败页
