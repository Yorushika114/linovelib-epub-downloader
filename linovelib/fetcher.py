import time
import io
import json
import re
import shutil
import requests
from urllib.parse import urlparse
from lxml import etree
from PIL import Image

CHROME_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
             "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36")

# 完整浏览器请求头：站点对「极简 UA+Referer」的脚本式请求会扣留长文，
# 在本章末尾追加「（內容加載失敗！請刷新或更換瀏覽器）」并截断正文。
# 补全 Accept / Accept-Language / Sec-Fetch-* 后可取回完整正文（见 154933 实测）。
# 注意：不设 Accept-Encoding，交给 requests 自会协商（含 br 时若缺 brotli 解码库会报错）。
BROWSER_HEADERS = {
    "User-Agent": CHROME_UA,
    "Accept": ("text/html,application/xhtml+xml,application/xml;q=0.9,"
               "image/avif,image/webp,*/*;q=0.8"),
    "Accept-Language": "zh-CN,zh;q=0.9",
    "Upgrade-Insecure-Requests": "1",
    "Sec-Fetch-Dest": "document",
    "Sec-Fetch-Mode": "navigate",
    "Sec-Fetch-Site": "same-origin",
    # 同源页面带 Referer 无害；个别页面同样需要它
    "Referer": "https://www.linovelib.com/",
}

# 图片 CDN（img3.readpai.com）带防盗链：缺失 Referer 会返回 403。
# 图片请求保持极简，只带 UA + Referer（Referer 为种子站），避免多余头干扰。
IMAGE_HEADERS = {
    "User-Agent": CHROME_UA,
    "Referer": "https://www.linovelib.com/",
}


# 浏览器位置不是项目资源：优先尊重调用方传入的路径，再从系统 PATH 发现常用浏览器。
# 不保留任何机器特定的安装绝对路径，项目移动或克隆后无需改源码。
DEFAULT_BROWSER_COMMANDS = ("msedge", "microsoft-edge", "google-chrome", "chrome", "chromium")

# 从实时 DOM 提取正文（真序）。必须在浏览器里跑（page.run_js），解析 page.html 快照会
# 拿到乱序 R。该脚本返回 JSON 字符串，含标题、按 DOM 顺序的段落文本、插图绝对 URL。
_BODY_JS = r"""
return (function () {
  var box = document.querySelector('#TextContent') || document.querySelector('.TextContent');
  if (!box) return null;
  var h1 = document.querySelector('h1');
  var title = h1 ? h1.textContent.trim() : '';
  var paras = [], imgs = [];
  var ps = box.querySelectorAll('p');
  for (var i = 0; i < ps.length; i++) {
    var t = ps[i].textContent.trim();
    if (!t) continue;
    // 克隆段（duplicate 文本）照常收集，交给 Python 端按文本去重。
    paras.push({text: t});
  }
  var ils = box.querySelectorAll('img');
  for (var i = 0; i < ils.length; i++) {
    var src = ils[i].getAttribute('data-src') || ils[i].getAttribute('src') || '';
    if (!src) continue;
    if (src.indexOf('//') === 0) src = 'https:' + src;
    imgs.push(src);
  }
  return JSON.stringify({title: title, paras: paras, imgs: imgs});
})();
"""


def _all_paragraphs(nodes):
    """从浏览器 DOM 快照取全部段落文本（含可见与 CSS 隐藏的克隆），维持 DOM 原始顺序。

    克隆段是否「可见」因站点版本而异：有时 pctheme 用 display:none 隐藏（此时
    计算样式可见性为 False），有时直接可见插入。无论哪种，克隆都是「与某真段文本完全
    相同」的多余重复，交给 downloader 的 _dedup_keep_order 按文本去重即可，这里不做
    可见性过滤——避免漏掉可见克隆（漏网会让正文出现重复回放）。
    """
    paragraphs = []
    for node in nodes:
        if not isinstance(node, dict):
            continue
        text = str(node.get("text") or "").strip()
        if text:
            paragraphs.append(text)
    return paragraphs


# ---------- 参考无关的正文字体（纯 requests + Fisher-Yates 反洗牌） ----------
#
# 反爬真因（2026-08-30 深度实测 + 参照 Novel-Scraper-Python / bilinovel-downloader 的公开逆向）：
#   linovelib 对【纯请求】下发的正文是乱序 R——每页 #TextContent 的前 TH=20 段天然有序
#   （所以开头本来就是真序），其后缀被 Fisher-Yates 洗牌，种子由章节 id 推导。页面里
#   真正的乱序只在「首 20 之后的段落后缀」，提前确认序由纯请求即可拿到。
#   旧浏览器路径反而因「连续两拍一致就提前返回」采到 chapterlog 重排前的乱序 DOM，把真序的
#   开头打乱了。故正文字体改走：raw 提取 + 逐页反洗牌（自算逆置换），无需浏览器、无需参考书。

_LINES_TRUNCATED = ("內容加載失敗", "内容加载失败", "請刷新或更換瀏覽器", "请刷新或更换浏览器")

# 单次重试的退避封顶。旧实现 time.sleep(2**attempt) 在 main(retries=8) 下累积到
# 1+2+4+…+128=255 秒：站点对高频访问会返回 429(Too Many Requests) 或 403(Cloudflare 挑战)，
# 一个被限流的页（正文页或插图）就能硬等 4 分多钟，看起来就像「卡住/假死」。把指数收敛到
# 几秒级，既保留对偶发网络错误/5xx 的重试，又不会让任何单页拖死整次下载。429 优先尊重
# 服务器 Retry-After（限流信号，不是瞬时错误，短等即恢复）。
_MAX_BACKOFF = 4
_429_WAIT = 3
_429_MAXWAIT = 30

# 站点对【个别整本小说】的 /novel/{nid}/ 页面统一返回 403 + Cloudflare 的静态
# 「Attention Required! | Cloudflare」拦截页——这是按 URL 规则的防火墙封禁（多为主流授权/
# 被要求下架的作品，如 无职转生 主篇 id=2013）。它不是临时网络错误：对同一本书的落地页、
# 目录页、卷页、章节页每一页都命中，重试（加头、换浏览器渲染、带 cf_clearance cookie）
# 都无法拿下——只有换一个未被屏蔽的编号/卷才是出路。抓取器识别到该拦截页即抛出专用异常，
# 让上层给出明确提示，而不是一路抛出「403 Client Error」原始异常。
class CloudflareBlockedError(Exception):
    """一个特定编号的整本小说页面被站点 Cloudflare 防火墙按 URL 规则封禁。"""


def _is_cloudflare_block(resp):
    """判断响应是否为 Cloudflare 的静态「Attention Required!」硬拦截页。

    只看响应状态/正文，不动网络。返回 True 表示这一整本的页面被站点封禁，应尽快
    停止并对用户给出明确提示（换用可下载的编号/卷），而非当作普通 403/坏页重试。
    """
    if getattr(resp, "status_code", None) != 403:
        return False
    try:
        text = getattr(resp, "text", "") or ""
    except Exception:
        return False
    return "Attention Required" in text and "cloudflare" in text.lower()


def _backoff(attempt, exc):
    """计算重试前的等待秒数（始终有界）。

    429：优先读服务器 Retry-After（封顶 _429_MAXWAIT），没有则固定 _429_WAIT；
    其余错误：指数退避但被 _MAX_BACKOFF 封顶。任何情况单次等待都不会到分钟级。
    """
    status = getattr(getattr(exc, "response", None), "status_code", None)
    if status == 429:
        try:
            ra = (exc.response.headers or {}).get("Retry-After")
            if ra and str(ra).isdigit():
                return min(int(ra), _429_MAXWAIT)
        except Exception:
            pass
        return _429_WAIT
    return min(2 ** attempt, _MAX_BACKOFF)


def extract_paragraphs_from_html(html):
    """从纯请求 HTML 里取 #TextContent 下所有 <p> 段（乱序 R，先保持原始顺序）。"""
    if not html:
        return []
    root = etree.HTML(html)
    box = root.xpath('//*[@id="TextContent"]')
    if not box:
        box = root.xpath('//*[contains(@class,"TextContent")]')
    if not box:
        return []
    paras = []
    for p in box[0].xpath(".//p"):
        t = p.xpath("string(.)").strip()
        if t:
            paras.append(t)
    return paras


def extract_images_from_html(html, base_url=None):
    """从正文页取内插图片绝对地址（data-src/src，补全协议/相对路径）。"""
    if not html:
        return []
    root = etree.HTML(html)
    box = root.xpath('//*[@id="TextContent"]')
    if not box:
        box = root.xpath('//*[contains(@class,"TextContent")]')
    if not box:
        return []
    imgs = []
    for img in box[0].xpath(".//img"):
        src = img.get("data-src") or img.get("src") or ""
        if not src:
            continue
        if src.startswith("//"):
            src = "https:" + src
        elif base_url and src.startswith("/"):
            from urllib.parse import urljoin
            src = urljoin(base_url, src)
        imgs.append(src)
    return imgs


def chapter_and_page(url):
    """从正文页 URL 解析 (chapter_id, page_index)。例 /3095/181030_13.html -> (181030, 13)。"""
    name = urlparse(url).path.rsplit("/", 1)[-1].split(".html")[0]
    m = re.match(r"^(\d+)(?:_(\d+))?$", name)
    if not m:
        return None, 1
    return int(m.group(1)), int(m.group(2) or 1)


def unscramble_paginated(paras, chapter_id, page_index, threshold=20, with_page=False):
    """对单页乱序 R 做 Fisher-Yates 逆置换，还原该页后缀真序。

    与站点/社区逆向一致的算法：
      seed = chapter_id*126+232  （本会话 2026-08-30 用参考书真序当 oracle 实证钉种：
        webcrack 解包章节共用 chapterlog.js 内为 f(Number(id),126,232)；对第4卷 ch3
        (cid 181030) 共 6 页(1/2/3/5/7/13)盲测，no-page 公式 LIS≈104/105/98/101/94/32，
        明显碾压 +page/+235/+127(<40)。seed 与页码无关，with_page 不再参与。）
      LCG: seed=(seed*9302+49397)%233280；swap_idx=int(seed/233280*(i+1))
    前 threshold（20）段固定不洗——这既是站点行为，也正是「真序开头」的来源。
    """
    if len(paras) <= threshold:
        return list(paras)
    fixed = list(paras[:threshold])
    scrambled = list(paras[threshold:])
    n = len(scrambled)
    seed = int(chapter_id) * 126 + 232  # 与页码无关（with_page 已弃用）
    indices = list(range(n))
    for i in range(n - 1, 0, -1):
        seed = (seed * 9302 + 49397) % 233280
        swap_idx = int(seed / 233280 * (i + 1))
        indices[i], indices[swap_idx] = indices[swap_idx], indices[i]
    inverse = [0] * n
    for original_pos, current_pos in enumerate(indices):
        inverse[current_pos] = original_pos
    return fixed + [scrambled[inverse[i]] for i in range(n)]


class Fetcher:
    """带 UA、重试、限速的 HTTP 抓取器。可注入 session 便于测试。

    另支持真浏览器（DrissionPage + Edge）抓正文页以拿到真序：懒加载单个常驻
    Chromium tab 跨章节复用，避免每页/每章重建浏览器。
    """

    def __init__(self, delay=0.4, retries=6, timeout=15, session=None, browser_path=None,
                 use_page_seed=False):
        self.delay = delay
        self.retries = retries
        self.timeout = timeout
        # session 为 None 时每次用全新连接（requests.request），避免服务器 keep-alive
        # 被复用而产生 ReadTimeout。测试可通过 session 注入 mock。
        self.session = session
        self.browser_path = browser_path
        # 正文反洗牌 seed 与页码无关：实测定为 cid*126+232（本会话用参考书真序 oracle 确认），
        # use_page_seed 已不再叠加页码，此处仅保留字段以兼容外部显式传参（传何值均无影响）。
        self.use_page_seed = use_page_seed
        self._page = None  # 懒加载；None 表示尚未启动浏览器

    # ---------- 真浏览器（正文页真序抓取） ----------
    def _resolve_edge(self):
        if self.browser_path:
            return self.browser_path
        for command in DEFAULT_BROWSER_COMMANDS:
            found = shutil.which(command)
            if found:
                return found
        return None

    def _get_browser_page(self):
        if self._page is None:
            from DrissionPage import ChromiumOptions, ChromiumPage
            path = self._resolve_edge()
            co = ChromiumOptions().set_browser_path(path).headless().set_argument("--disable-gpu")
            self._page = ChromiumPage(co)
            # 不要去拦截 chapterlog.js —— 它才是把实时 DOM 还原成真序的脚本。
            # 实测（2026-08-30，推翻旧「拦截=真序」的注释）：linovelib 对纯请求下发的是
            # **乱序**正文，chapterlog.js 在浏览器里把它还原成真序、并注入完整正文、并掺入
            # 文本相同的克隆段。若在此拦截 chapterlog，实时 DOM 就停留在服务器下发的乱序
            # 状态（36 段、随波逐流@23）；不拦则立即真序（56 段、随波逐流=末段、稳定不随时间
            # 恶化）。克隆段由 downloader 的 _dedup_keep_order 按文本去重。
        return self._page

    def get_page_html(self, url, browser=True):
        """抓正文页（HTML 快照）。browser=True 用真浏览器渲染；False 退回纯 requests。

        注意：pctheme 只把【真序】体现在实时 DOM 里，page.html（outerHTML 快照）
        里 #TextContent 仍是乱序 R。要拿真序必须用 get_page_body（从实时 DOM 读）。
        此方法仅作兜底/信息用，正文抓取请用 get_page_body。
        """
        if not browser:
            return self.get_html(url)
        page = self._get_browser_page()
        for _ in range(self.retries):
            page.get(url)
            try:
                page.wait.doc_loaded(5)
            except Exception:
                pass
            html = page.html or ""
            if "Access denied" in html and "Cloudflare" in html:
                continue
            return html
        return page.html or ""

    def _restart_browser(self):
        """断连/卡死时彻底重建浏览器。先尽力关掉旧 tab，再置 None 以便下一次懒重建。
        DrissionPage 崩掉后同进程重开会抛 AttributeError('_dl_mgr')，所以直接换全新对象。"""
        try:
            if self._page is not None:
                try:
                    self._page.quit()
                except Exception:
                    pass
        except Exception:
            pass
        finally:
            self._page = None

    def get_page_body(self, url):
        """抓正文页 —— 参考无关（纯 requests + 逐页 Fisher-Yates 反洗牌）还原真序。

        返回 (title, paras, image_urls)：
          title   —— h1 标题（可能为空）
          paras   —— 反洗牌后的正文段落（每页前 20 段天然真序，后缀已按章节 id 逆置换）
          image_urls —— 页内插图 URL（已补全为绝对地址）

        为何不用浏览器：实测（2026-08-30 深度对照参考书 + 社区公开逆向）linovelib 对纯请求
        下发的乱序 R 里，每页 #TextContent 的前 20 段天然有序（开头本来就是真序），其**后缀**
        才是被站点 Fisher-Yates 洗牌的乱序段。旧「真浏览器实时 DOM」路径反而因「连续两拍一致
        就提前返回」采到 chapterlog 重排前的乱序 DOM，连真序的开头都被打乱。故这里退回纯
        requests（更快、无浏览器依赖），并复用站点/社区一致的逆置换把每页后缀还原。
        seed 用本章节共用 chapterlog.js 的常量 cid*126+232，与页码无关（已实证钉种）。

        拿不到【完整】正文（站点偶发对脚本式请求截断，追加「內容加載失敗…」）时重试并明确报错，
        绝不静默写入残缺/乱序正文。
        """
        for _ in range(self.retries):
            html = self.get_html(url)
            if any(m in html for m in _LINES_TRUNCATED):
                time.sleep(self.delay)
                continue
            paras = extract_paragraphs_from_html(html)
            imgs = extract_images_from_html(html, base_url=url)
            # 只有当「无段落且无插图」时才判定为空页/被拦而重试；短章（尾声/插画章节
            # 段落极少甚至只有图）是合法内容，不能因段落数少就丢弃。
            if not paras and not imgs:
                time.sleep(self.delay)
                continue
            cid, page_idx = chapter_and_page(url)
            if cid is not None:
                paras = unscramble_paginated(paras, cid, page_idx,
                                             with_page=self.use_page_seed)
            title = ""
            try:
                h1 = etree.HTML(html).xpath("//h1")
                if h1:
                    title = h1[0].xpath("string(.)").strip()
            except Exception:
                pass
            return title, paras, imgs
        return "", [], []

    def _throttle(self):
        if self.delay:
            time.sleep(self.delay)

    def get(self, url, method="GET", browser=False, **kw):
        self._throttle()
        headers = dict(kw.pop("headers", {}))
        defaults = BROWSER_HEADERS if browser else IMAGE_HEADERS
        for k, v in defaults.items():
            headers.setdefault(k, v)
        # 每请求尽量用全新连接，避免服务器保持 keep-alive 而被复用产生 ReadTimeout
        headers.setdefault("Connection", "close")
        last = None
        for attempt in range(self.retries):
            try:
                if self.session is None:
                    resp = requests.request(method, url, timeout=self.timeout,
                                            headers=headers, **kw)
                else:
                    resp = self.session.request(method, url, timeout=self.timeout,
                                                headers=headers, **kw)
                if _is_cloudflare_block(resp):
                    # 整本小说的页面被站点防火墙按 URL 规则封禁：不是瞬时错误，重试/换头/唤
                    # 浏览器都无解。立即停止并对用户给出明确提示，而不是当作坏页反复重试——
                    # 否则一整个编号的所有页码都会各自重试 retries 次，白白拖延并刷屏。
                    raise CloudflareBlockedError(
                        f"{url} 被站点 Cloudflare 防火墙拦截（403 Attention Required）")
                resp.raise_for_status()
                return resp
            except requests.RequestException as e:
                last = e
                if attempt < self.retries - 1:
                    time.sleep(_backoff(attempt, e))
        raise last

    def get_html(self, url, **kw):
        # 页面请求用完整浏览器头，规避站点对脚本式请求的正文截断
        return self.get(url, browser=True, **kw).text

    def get_bytes(self, url, **kw):
        # 图片请求只带 UA + Referer（防盗链）
        return self.get(url, browser=False, **kw).content

    def is_valid_image(self, data):
        try:
            Image.open(io.BytesIO(data)).verify()
            return True
        except Exception:
            return False
