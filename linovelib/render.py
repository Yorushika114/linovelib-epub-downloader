"""真实浏览器渲染抓取器。

linovelib 会对「非浏览器请求」返回降级/打乱的内容：段数变少、正文顺序被改
（例如「直勾勾→生日喔」被当成真序发给请求脚本，而真实浏览器看到的是
「直勾勾→你有点冷漠喔」）。用 requests/BeautifulSoup 直接读服务器 HTML 会拿到
被防爬处理过的假序假内容。

解决办法：用真实浏览器（系统 Edge）渲染页面、让站点自身的 JS 完整执行后，
再取 #TextContent 的可见段落。这就和用户在浏览器里看到的一模一样。

本类实现与 `Fetcher` 相同的接口（get_html / get_bytes / is_valid_image / retries），
从而可以原样替换进 downloader / main，而无需改动那些模块。
"""

from .fetcher import Fetcher

# 渲染后清除正文里 display:none 的重复/克隆段（防爬注入的噪声），
# 让 parse_chapter_page 只拿到用户可见的真实段落。
_STRIP_HIDDEN = """
() => {
  const box = document.getElementById('TextContent');
  if (!box) return;
  for (const p of Array.from(box.querySelectorAll('p'))) {
    const st = getComputedStyle(p);
    if (st.display === 'none' || p.offsetParent === null) {
      p.remove();
    }
  }
}
"""

# 等待正文化就绪：真正在阅读器里出现 #TextContent 且有非空段落。
_READY = """
() => {
  const box = document.getElementById('TextContent');
  if (!box) return false;
  const ps = box.querySelectorAll('p');
  return ps.length > 0;
}
"""

# 判断 pctheme.js 是否已把正文重排完成。站点防爬：服务器返回的是「打乱顺序」的
# 假内容（如直勾勾→生日喔），pctheme.js 在页面加载后按真实阅读顺序重排正文，
# 并会克隆少数走位的段落——克隆会在 DOM 里制造「重复文本」。所以一旦正文里出现
# 重复文本，就说明重排至少已开始/进行中（随后还要等 DOM 稳定，见 get_html）。
_CLONES_PRESENT = """
() => {
  const box = document.getElementById('TextContent');
  if (!box) return false;
  const texts = Array.from(box.querySelectorAll('p'))
        .map(p => (p.textContent || '').trim()).filter(x => x);
  if (!texts.length) return false;
  return new Set(texts).size < texts.length;
}
"""

# 当前正文 <p> 的数量，用于判断 DOM 是否已稳定（pctheme 克隆全部注入完毕后，
# 计数不再变化；若在「部分克隆」的中间态抓取，去重结果会随加载时机而飘）。
_COUNT = """
() => {
  const box = document.getElementById('TextContent');
  return box ? box.querySelectorAll('p').length : 0;
}
"""


class RenderFetcher:
    """用系统浏览器渲染正文页；图片仍走底层 requests 抓取器。"""

    def __init__(self, image_fetcher=None, headless=False, channel="msedge",
                 wait_after=2.5, timeout=90000):
        self.img = image_fetcher or Fetcher()
        self.retries = getattr(self.img, "retries", 6)
        self.headless = headless
        self.channel = channel
        self.wait_after = wait_after
        self.timeout = timeout
        self._pw = None
        self._browser = None
        self._page = None

    def _ensure_page(self):
        if self._page is None:
            from playwright.sync_api import sync_playwright
            self._pw = sync_playwright().start()
            # 复用系统 Edge/Chrome，避免下载 Chromium；headed 更稳，能过 Cloudflare。
            self._browser = self._pw.chromium.launch(channel=self.channel,
                                                     headless=self.headless)
            context = self._browser.new_context(ignore_https_errors=True,
                                                locale="zh-CN",
                                                viewport={"width": 1280, "height": 900})
            context.set_default_timeout(self.timeout)
            self._page = context.new_page()
            self._page.set_default_timeout(self.timeout)
        return self._page

    def get_html(self, url, method="GET", **kw):
        page = self._ensure_page()
        page.goto(url, wait_until="commit", timeout=self.timeout)
        # 容忍 Cloudflare 挑战：轮询等待正文容器出现。
        for _ in range(20):
            try:
                if page.evaluate(_READY):
                    break
            except Exception:
                pass
            page.wait_for_timeout(1000)
        # 等待 pctheme.js 重排正文：先等「克隆文本出现」（说明重排已启动），再等
        # <p> 计数彻底稳定（说明 pctheme 全部克隆注入完毕）。只在「部分克隆」的
        # 中间态抓取，去重结果会随加载时机飘移。提前抓取则拿到服务器返回的乱序
        # 假内容（直勾勾→生日喔，而非直勾勾→你有点冷漠喔）。
        for _ in range(28):
            try:
                if page.evaluate(_CLONES_PRESENT):
                    break
            except Exception:
                pass
            page.wait_for_timeout(300)
        last, stable = -1, 0
        for _ in range(30):  # 最多约 12s；已稳定多次读数后提前结束
            try:
                n = page.evaluate(_COUNT)
            except Exception:
                n = 0
            if n == last and n > 0:
                stable += 1
                if stable >= 4:
                    break
            else:
                stable = 0
            last = n
            page.wait_for_timeout(400)
        page.wait_for_timeout(int(self.wait_after * 1000))  # 再静置，留一点给懒加载图
        # 去掉隐藏克隆段，保证只保留用户可见的真实段落。
        page.evaluate(_STRIP_HIDDEN)
        return page.content()

    def get_bytes(self, url, **kw):
        return self.img.get_bytes(url, **kw)

    def is_valid_image(self, data):
        return self.img.is_valid_image(data)

    def close(self):
        try:
            if self._browser:
                self._browser.close()
        except Exception:
            pass
        try:
            if self._pw:
                self._pw.stop()
        except Exception:
            pass
