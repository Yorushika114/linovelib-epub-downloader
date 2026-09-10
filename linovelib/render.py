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

import re
from urllib.parse import quote
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


def _require_playwright():
    """构造期校验 playwright 是否可用，缺失时立刻抛带安装指引的 ImportError。

    底层 `import playwright` 原本只在首次渲染的 `_ensure_page()` 里发生（懒加载，
    避免纯编号下载被迫依赖浏览器）。但这样 `RenderFetcher(...)` 构造总会成功，
    调用方（main._resolve_identifier / wpf_bridge.run_resolve）用来判定「浏览器不可
    用 → 走站外引擎兜底」的 `browser = None` 分支永远不会触发：缺依赖时构造出的
    对象看似可用，直到搜索那一刻才抛 ImportError，被上层 try/except 吞掉后变成
    「搜索无结果」。新克隆的机器没执行 `playwright install` 时正是这个表现。
    这里把「有没有 playwright」提前到构造期判定，让降级路径按设计生效。
    """
    try:
        import playwright  # noqa: F401
    except ImportError as e:
        raise ImportError(
            "未安装 playwright，无法使用真实浏览器搜索/渲染。"
            "请执行：pip install -r requirements.txt && playwright install msedge"
        ) from e


class RenderFetcher:
    """用系统浏览器渲染正文页；图片仍走底层 requests 抓取器。"""

    def __init__(self, image_fetcher=None, headless=False, channel="msedge",
                 wait_after=2.5, timeout=90000):
        _require_playwright()
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

    def search_html(self, name, search_url=None):
        """渲染站点搜索结果页（书名→结果），返回序列化后的结果页 HTML。

        站点 /S6/?searchkey= 只会把结果返回给真实浏览器：对脚本化 requests 吐空壳，
        且未在本会话访问过首页（拿到 Cloudflare cookie）时，搜索页也常是空 <body></body>。
        所以必须先到首页暖机、再跳转搜索。

        导航必须用 networkidle 等待（让客户端 JS 把结果请求发出并落地），且【不要】再追加
        额外 sleep 或 wait_for_selector——实测一加等待就拿回空壳。返回页含 div.search-result-list
        结果项，由 resolver.parse_search_results 解析（本站自站搜索，参考无关）。

        【精确命中会 302 到小说页】：当查询词能唯一定位一本书时（如完整书名），站点
        不渲染结果列表，而是直接把 /S6/?searchkey=… 重定向到 /novel/{id}.html。此时页面
        里没有 search-result-list，按结果列表解析会得到「0 条」，并被上层误判成「查无此书」
        而不再兜底。故这里检测落点：若被送到小说详情页，就合成一条候选（编号取自 URL，
        标题取自页面 <title>），保持「搜到 1 条」的语义与结果列表路径一致。
        """
        page = self._ensure_page()
        try:
            page.goto("https://www.linovelib.com/",
                      wait_until="networkidle", timeout=self.timeout)
        except Exception:
            pass
        url = search_url or ("https://www.linovelib.com/S6/?searchkey=" + quote(name))
        page.goto(url, wait_until="networkidle", timeout=self.timeout)
        html = page.content()
        redirected = self._redirect_hit(page.url, html)
        if redirected is not None:
            # 把重定向落点合成为一条结果项，复用 _parse_search_results 的结果契约。
            nid, title = redirected
            return (f'<div class="search-result-list"><h2 class="tit">'
                    f'<a href="/novel/{nid}.html">{title}</a></h2></div>')
        return html

    @staticmethod
    def _redirect_hit(final_url, html):
        """搜索结果页被 302 送到小说详情页时，返回 (编号, 标题)；否则 None。

        只有落点形如 /novel/{id}.html 才算「精确命中直达」；停在 /S6/ 的结果页
        （含 0 条）一律返回 None，交由常规结果列表解析处理，避免把「查无此书」误判成命中。

        标题取详情页 <title> 的首段（站点格式「书名_作者作品_出版社_哩哩轻小说」），
        这样上层 is_exact_match 仍能标记「书名吻合」，与结果列表路径行为一致。
        """
        m = re.search(r"/novel/(\d+)\.html", final_url or "")
        if not m:
            return None
        t = re.search(r"<title[^>]*>(.*?)</title>", html or "",
                      re.S | re.I)
        raw = t.group(1) if t else ""
        title = raw.split("_", 1)[0].strip()
        # <title> 里可能的 HTML 实体/标签清掉，只留纯文本。
        title = re.sub(r"<[^>]+>", "", title)
        for ent, ch in (("&amp;", "&"), ("&lt;", "<"), ("&gt;", ">"),
                        ("&quot;", '"'), ("&#39;", "'"), ("&nbsp;", " ")):
            title = title.replace(ent, ch)
        return m.group(1), title.strip()

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
