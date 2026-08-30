import pathlib
from linovelib.downloader import parse_chapter_page, iter_page_urls, download_chapter
from linovelib.models import Chapter


def _fixture(name):
    return (pathlib.Path(__file__).parent / "fixtures" / name).read_text(encoding="utf-8")


def test_parse_chapter_page_paragraphs_and_images():
    page = parse_chapter_page(_fixture("chapter_page.html"),
                              "https://www.linovelib.com/novel/3095/154932.html", 1)
    assert page.title == "序"
    assert page.paragraphs == ["第一段文字。", "第二段文字。", "第三段。"]
    assert page.image_urls == ["https://img3.readpai.com/3/3095/154932/1.jpg"]


def test_parse_chapter_page_second_page_title():
    page = parse_chapter_page(_fixture("chapter_page2.html"),
                              "https://www.linovelib.com/novel/3095/154932_2.html", 2)
    assert page.title == "序（2）"
    assert page.paragraphs == ["第二页内容。"]


def test_parse_chapter_page_lazyload_prefers_data_src():
    # 懒加载插图：src 是占位 SVG，真实 URL 在 data-src，必须取 data-src
    html = ("<!DOCTYPE html><html><head><meta charset='utf-8'></head><body>"
            "<div id='mlfy_main_text'><h1>插图</h1>"
            "<div id='TextContent'>"
            "<img class='imagecontent lazyload' src='/images/sloading.svg' "
            "data-src='https://img3.readpai.com/3/3095/181028/215401.jpg'/>"
            "</div></div></body></html>")
    page = parse_chapter_page(html,
                              "https://www.linovelib.com/novel/3095/181028.html", 1)
    assert page.image_urls == ["https://img3.readpai.com/3/3095/181028/215401.jpg"]


class PageNavFetcher:
    def __init__(self, pages):
        self.pages = pages  # {url: html}

    def get_html(self, url, method="GET", **kw):
        return self.pages[url]

    def get_bytes(self, url):
        return b"fakeimage"


def _mk_chapter_page_html(h1, paras, next_href=None):
    nav = f'<a href="{next_href}">下一页</a>' if next_href else ""
    paras_html = "".join(f"<p>{p}</p>" for p in paras)
    return (f"<!DOCTYPE html><html><head><meta charset='utf-8'></head><body>"
            f"<div id='mlfy_main_text'><h1>{h1}</h1>"
            f"<div id='TextContent' class='TextContent'>{paras_html}{nav}</div></div></body></html>")


def test_iter_page_urls_walks_next_links():
    p1 = _mk_chapter_page_html("序", ["p1"], next_href="/novel/3095/154932_2.html")
    p2 = _mk_chapter_page_html("序（2）", ["p2"], next_href="/novel/3095/154932_3.html")
    p3 = _mk_chapter_page_html("序（3）", ["p3"])
    f = PageNavFetcher({
        "https://www.linovelib.com/novel/3095/154932.html": p1,
        "https://www.linovelib.com/novel/3095/154932_2.html": p2,
        "https://www.linovelib.com/novel/3095/154932_3.html": p3,
    })
    urls = iter_page_urls("https://www.linovelib.com/novel/3095/154932.html", f)
    assert urls == ["https://www.linovelib.com/novel/3095/154932.html",
                    "https://www.linovelib.com/novel/3095/154932_2.html",
                    "https://www.linovelib.com/novel/3095/154932_3.html"]


def test_iter_page_urls_single_page():
    f = PageNavFetcher({"https://www.linovelib.com/novel/3095/154932.html": "<p>x</p>"})
    assert iter_page_urls("https://www.linovelib.com/novel/3095/154932.html", f) == \
        ["https://www.linovelib.com/novel/3095/154932.html"]


def test_iter_page_urls_does_not_follow_next_chapter():
    # 「下一页」指向下一章（不同 cid，154933）而非本章分页（154932_2），必须停在本章。
    p1 = _mk_chapter_page_html("第一章", ["p1"], next_href="/novel/3095/154933.html")
    f = PageNavFetcher({
        "https://www.linovelib.com/novel/3095/154932.html": p1,
        # 即便误入也会掉入"已访问"逻辑被挡住，这里直接断言只返回单页：
        "https://www.linovelib.com/novel/3095/154933.html": "<p>next</p>",
    })
    urls = iter_page_urls("https://www.linovelib.com/novel/3095/154932.html", f)
    assert urls == ["https://www.linovelib.com/novel/3095/154932.html"]

    # 相对路径（无斜杠前缀）同样应被识别为跨章。
    p2 = _mk_chapter_page_html("第一章", ["p1"], next_href="154933.html")
    f2 = PageNavFetcher({
        "https://www.linovelib.com/novel/3095/154932.html": p2,
    })
    assert iter_page_urls("https://www.linovelib.com/novel/3095/154932.html", f2) == \
        ["https://www.linovelib.com/novel/3095/154932.html"]


class SequencingFetcher:
    """按调用次序依次返回 get_html 结果，其余页返回最后一次；带重试属性。"""

    retries = 5

    def __init__(self, sequence):
        self.sequence = list(sequence)
        self.calls = 0

    def get_html(self, url, method="GET", **kw):
        idx = min(self.calls, len(self.sequence) - 1)
        self.calls += 1
        return self.sequence[idx]

    def get_bytes(self, url):
        return b"x"

    def is_valid_image(self, data):
        return True


def test_download_chapter_retries_truncated_page(tmp_path):
    # 站点对脚本请求扣留长文并追加「內容加載失敗！請刷新或更換瀏覽器」标记，
    # 第一次给截断页（带标记、无翻页），重试后才给真实正文。
    truncated = ("<!DOCTYPE html><html><head><meta charset='utf-8'></head><body>"
                 "<div id='mlfy_main_text'><h1>序</h1>"
                 "<div id='TextContent'><p>（內容加載失敗！請刷新或更換瀏覽器）</p></div>"
                 "</div></body></html>")
    good = _mk_chapter_page_html("序", ["真实正文。"])
    f = SequencingFetcher([truncated, good])
    ch = Chapter(id="154932", url="https://www.linovelib.com/novel/3095/154932.html",
                 title="序")
    download_chapter(ch, "3095", f, tmp_path)
    assert ch.html == "<p>真实正文。</p>"          # 取到的是重试后的真实内容
    assert "內容加載失敗" not in ch.html            # 截断标记未被写入
    assert len(ch.pages) == 1                       # 该页未被丢弃


def test_download_chapter_recovers_empty_challenge_page(tmp_path):
    # 风控拦下时整页没有正文容器 #TextContent（如 Cloudflare 挑战页），重试后恢复。
    challenge = ("<!DOCTYPE html><html><head><meta charset='utf-8'></head><body>"
                 "<meta http-equiv='refresh' content='0;url=/cdn-cgi/challenge-platform/'/>"
                 "Just a moment...</body></html>")
    good = _mk_chapter_page_html("序", ["恢复正文。"])
    f = SequencingFetcher([challenge, good])
    ch = Chapter(id="154932", url="https://www.linovelib.com/novel/3095/154932.html",
                 title="序")
    download_chapter(ch, "3095", f, tmp_path)
    assert ch.html == "<p>恢复正文。</p>"
    assert len(ch.pages) == 1


class BrowserPoisonedFetcher(SequencingFetcher):
    """模拟浏览器实时 DOM：真序正文里混入了与真段文本完全相同的克隆重复段。"""

    def get_page_body(self, url):
        return "序", ["正确首段。", "正确首段。", "正确后段。"], []


def test_download_chapter_uses_browser_dom_and_dedups_clones(tmp_path):
    # 修正后的架构：正文只能从【真浏览器实时 DOM】取（真序）。静态 HTML 未被脚本重排，
    # 正是站点下发的乱序/截断那份，绝不能回退。克隆（与真段文本完全相同的重复段）由
    # _dedup_keep_order 按文本去重，保留首次出现的真段。
    # 静态页给的是乱序（后半段。、正确首段。），浏览器 DOM 给出真序（含一个「正确首段。」
    # 克隆重复段）；断言输出等于浏览器真序去重后的结果，而非静态乱序。
    f = BrowserPoisonedFetcher([_mk_chapter_page_html("序", ["后半段。", "正确首段。"])])
    ch = Chapter(id="154932", url="https://www.linovelib.com/novel/3095/154932.html",
                 title="序")

    download_chapter(ch, "3095", f, tmp_path)

    assert ch.html == "<p>正确首段。</p><p>正确后段。</p>"
    assert len(ch.pages) == 1
