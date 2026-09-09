"""bilimanga.net（嗶哩漫畫）浏览器抓取器。

站点要点（均已实地验证，详见 docs 笔记）：
- 图片 CDN w.motiezw.com 被 Cloudflare 保护：curl/requests/page.request.get 全 403，
  只有「页面自身发起的 <img> 请求」能拿到 200。因此图片字节必须经 on_response 抓取。
- 跨域 <img> 直接画到 canvas 会 taint（CDN 无 CORS 头），故 avif->JPEG 一律走同一
  page 内创建的同源 blob: URL 再 drawImage，避免 SecurityError。
- 章节页是移动端 H5 阅读器：必须 is_mobile=True 且先访问一次首页「暖机」，
  否则服务端返回「章節不支持桌面電腦端瀏覽器顯示」空壳。暖机后再进章节，
  原始 HTML 自带全部 img[data-src]（图片 id 严格递增 => 未经乱序）。
- 书名搜索 = POST /search.html（填 #searchkey 提交），返回渲染后 DOM，
  结果含「條記錄」的 module -> ol.book-ol -> li.book-li -> a.book-layout。
- Cloudflare 对连续自动化请求会做的间歇性挑战/限速，表现为某次导航秒开、某次
  90s 超时。故导航失败时重建整个浏览器会话（新指纹）重试，通常可绕过。
"""

from __future__ import annotations

import base64
import sys
import time

from playwright.sync_api import sync_playwright

from .models import Comic, ComicHit, Chapter
from .parser import (BASE, parse_catalog, parse_chapter_image_urls,
                     ordered_image_urls, parse_search_results)

MOBILE_UA = ("Mozilla/5.0 (iPhone; CPU iPhone OS 16_0 like Mac OS X) "
             "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/16.0 "
             "Mobile/15E148 Safari/604.1")

VIEWPORT = {"width": 390, "height": 844}

# 同源 blob 解码：avif 字节 -> JPEG dataURL（blob: 同源，不被 taint）
_DECODE_BLOB = """async (b64) => {
  const bin = atob(b64); const bytes = new Uint8Array(bin.length);
  for (let i = 0; i < bin.length; i++) bytes[i] = bin.charCodeAt(i);
  const blob = new Blob([bytes], { type: 'image/avif' });
  const url = URL.createObjectURL(blob);
  try {
    const img = new Image();
    await new Promise((res, rej) => { img.onload = res; img.onerror = rej; img.src = url; });
    const cv = document.createElement('canvas');
    cv.width = img.naturalWidth; cv.height = img.naturalHeight;
    const cx = cv.getContext('2d');
    cx.fillStyle = '#fff'; cx.fillRect(0, 0, cv.width, cv.height);
    cx.drawImage(img, 0, 0);
    const durl = cv.toDataURL('image/jpeg', 0.92);
    const wh = [img.naturalWidth, img.naturalHeight];
    URL.revokeObjectURL(url);
    return { durl, w: wh[0], h: wh[1] };
  } catch (e) { URL.revokeObjectURL(url); return { err: String(e) }; }
}"""

# 触发脚本：为每个 URL 追加隐藏 <img>，强制浏览器去请求 CDN（字节由 on_response 拿到）
_INJECT = """(urls) => {
  const wrap = document.createElement('div');
  wrap.style.display = 'none';
  document.body.appendChild(wrap);
  for (const u of urls) {
    const img = document.createElement('img');
    img.style.display = 'none';
    img.src = u;
    wrap.appendChild(img);
  }
}"""

# 反自动化伪装（每个新上下文都要注入）：patch 掉 Playwright 的自动化指纹，
# 否则 bilimanga 的 search_guard / Cloudflare 会识别出无头/机器浏览器，
# 对「书名搜索」等敏感操作返回一段空白文档（HTTP 200、正文只有 <html><head></head><body></body></html>），
# 让搜索表现为「永远搜不到」。实测无头+有头均需此脚本才能渲染出结果。
_STEALTH_JS = """(() => {
  Object.defineProperty(navigator, 'webdriver', { get: () => undefined });
  window.chrome = { runtime: {} };
  Object.defineProperty(navigator, 'languages', { get: () => ['zh-CN', 'zh', 'en'] });
  Object.defineProperty(navigator, 'plugins', { get: () => [1, 2, 3, 4, 5] });
  Object.defineProperty(navigator, 'platform', { get: () => 'Win32' });
  const origQuery = window.navigator.permissions.query;
  window.navigator.permissions.query = (p) =>
    p.name === 'notifications' ? Promise.resolve({ state: Notification.permission }) : origQuery(p);
})()"""


class ComicError(Exception):
    """bilimanga 抓取通用异常。"""


class ComicBlockedError(ComicError):
    """被 Cloudflare 按书/URL 封禁：换一个漫画尝试。"""


class ComicFetcher:
    def __init__(self, delay: float = 0.5, timeout: int = 90, headless: bool = True):
        self.delay = delay
        # self.timeout 以「秒」计（供 Python 侧 deadline 用）；playwright 需要毫秒。
        self.timeout = timeout
        self._timeout_ms = int(timeout * 1000)
        self._headless = headless
        self._pw = sync_playwright().start()
        self._browser = self._pw.chromium.launch(
            channel="msedge", headless=headless,
            args=["--disable-gpu", "--no-sandbox", "--disable-blink-features=AutomationControlled"])
        self._ctx = self._browser.new_context(
            ignore_https_errors=True, locale="zh-CN", viewport=VIEWPORT,
            user_agent=MOBILE_UA, is_mobile=True, device_scale_factor=3, has_touch=True)
        self._ctx.add_init_script(_STEALTH_JS)
        self._page = self._ctx.new_page()
        self._warmed = False
        # 注册到 page 的事件处理器，重建会话后要重新挂上
        self._handlers: list[tuple[str, object]] = []
        # —— 限速快速失败参数 ——
        # 轻量页（暖机/搜索页/目录页）在「热会话」上易被 Cloudflare 按住连接、静默阻塞满
        # self.timeout(90s)，这正是复用会话连下第二本漫画时「看似卡死」的主因。给它们更短
        # 导航超时 + 结果渲染窗口，被限速就快速转「重建会话(新指纹)+退避重试」而不是干等。
        self._light_nav_ms = 20000            # 轻量页导航超时（毫秒）
        self._search_render_window = 15.0     # 提交搜索后等「條記錄」结果模块的最长秒数
        self._guard_settle_s = 2.5            # 提交搜索前静置秒数（等 search_guard 完成 redeum）
        self._search_cache: dict[str, list[ComicHit]] = {}  # 书名 -> 命中列表（会话级）

    # ---------- 会话/导航 ----------
    def _register(self, event: str, handler):
        self._handlers.append((event, handler))
        self._page.on(event, handler)

    def _unregister_all(self):
        for event, handler in self._handlers:
            try:
                self._page.remove_listener(event, handler)
            except Exception:
                pass
        self._handlers.clear()

    def _reset_session(self):
        """关掉旧浏览器，重建全新浏览器上下文（新指纹）以绕过 Cloudflare 挑战。

        只重放浏览器、不重启 playwright（同一进程内重启 sync_playwright 易出问题）。
        """
        try:
            self._browser.close()
        except Exception:
            pass
        self._browser = self._pw.chromium.launch(
            channel="msedge", headless=self._headless,
            args=["--disable-gpu", "--no-sandbox", "--disable-blink-features=AutomationControlled"])
        handlers = list(self._handlers)
        self._handlers = []
        self._ctx = self._browser.new_context(
            ignore_https_errors=True, locale="zh-CN", viewport=VIEWPORT,
            user_agent=MOBILE_UA, is_mobile=True, device_scale_factor=3, has_touch=True)
        self._ctx.add_init_script(_STEALTH_JS)
        self._page = self._ctx.new_page()
        for event, handler in handlers:
            self._register(event, handler)
        self._warmed = False

    def _goto_reliable(self, url: str, wait_until: str = "domcontentloaded", retries: int = 4,
                       timeout_ms: int | None = None, status: str | None = None) -> bool:
        """导航并在瞬态超时（Cloudflare 偶发挑战/限速）时重建会话、延长退避重试。

        保持 listeners（存于 self._handlers）；成功后返回 True，需用 self._page 取页面。
        timeout_ms 覆盖导航超时（默认 self._timeout_ms=90s）。轻量页（搜索/目录/暖机）给 15s
        短超时：热会话上被 Cloudflare 按住连接时会静默阻塞满超时，那种「看似卡死」正源于
        默认 90s——短超时让限速快速被识别并转入「重建会话+退避重试」。status 非空时打印一行
        状态，避免限速期间用户误以为程序卡死。
        """
        tm = int(timeout_ms) if timeout_ms is not None else self._timeout_ms
        for attempt in range(retries):
            if status:
                self._status_line(f"{status}（最多 {tm // 1000} 秒）...")
            try:
                self._page.goto(url, wait_until=wait_until, timeout=tm)
                if status:
                    self._status_line("", clear=True)
                return True
            except Exception:
                if attempt == retries - 1:
                    try:
                        self._page.goto(url, wait_until="commit", timeout=tm)
                        if status:
                            self._status_line("", clear=True)
                        return True
                    except Exception:
                        break
                # 时间窗口式限速：重置会话 + 递增等待（5/12/22s）让 Cloudflare 放行
                self._reset_session()
                if status:
                    self._status_line(f"{status} → 被限速，重建会话重试（等 {5 + attempt * 8} 秒）...")
                time.sleep(5 + attempt * 8)
                if status:
                    self._status_line("", clear=True)
        if status:
            self._status_line("", clear=True)
        raise ComicError(f"多次尝试后仍无法导航：{url}")

    def _ensure(self):
        if not self._warmed:
            self._warmup()

    def _warmup(self):
        self._goto_reliable(BASE + "/", timeout_ms=self._light_nav_ms)
        self._page.wait_for_timeout(2500)
        self._warmed = True

    def _scroll_bottom(self):
        for _ in range(8):
            self._page.evaluate("() => window.scrollBy(0, document.body.scrollHeight)")
            self._page.wait_for_timeout(250)

    def _is_block_page(self, html: str) -> bool:
        """Cloudflare 软封页识别：WAF「Attention Required」页或 JS 挑战「Just a moment」。

        这类页面是服务端返回的 200 正常响应，正文却是封禁/挑战页。真实浏览器不会被拦，
        只有带自动化指纹的无头会话才会命中（所以浏览器能看、脚本不能）。命中时应重建
        会话（新指纹）重试，而不是立即判死——1270 能下说明这类拦截是间歇性/指纹相关的。

        注意：别用 `"captcha" in html`（正文匹配）。漫画阅读器正文可能顺带带这个字符串
        （实测某章 is_block(page)=True 但 urls=26），会误判成软封、无谓重建会话。改为
        依赖标题关键词 + 正文精确的「Attention Required」短语。
        """
        if "Attention Required" in html:   # Cloudflare 阻塞页正文
            return True
        try:
            title = (self._page.title() or "").lower()
        except Exception:
            title = ""
        return any(k in title for k in ("attention required", "just a moment",
                                        "cloudflare", "captcha"))

    def _is_desktop_shell(self, html: str) -> bool:
        return "桌面電腦" in html or "不支持桌面電腦" in html

    def _status_line(self, text: str, clear: bool = False) -> None:
        """在真终端用 \\r 原地刷新/清除一行状态；重定向(非 tty)时整行打印。

        tty 下每次调用覆盖当前行（数字实时浮动）；clear=True 只清空当前行，
        供状态结束后让位给后续输出（如搜索结果候选列表）。非 tty 无法原地刷新，
        直接 print 一行。
        """
        tty = sys.stdout.isatty()
        if clear:
            if tty:
                sys.stdout.write("\r\033[K")
                sys.stdout.flush()
            return
        if tty:
            sys.stdout.write("\r" + text + "\033[K")
            sys.stdout.flush()
        else:
            print(text)

    def _navigate_clean(self, url: str, wait_until: str = "domcontentloaded",
                        settle: int = 1500, retries: int = 3, prepare=None,
                        timeout_ms: int | None = None, status: str | None = None) -> str:
        """导航到 url 并确保拿到的是正常内容而非 Cloudflare 软封页。

        被拦时重建整个浏览器会话（新指纹）并退避重试；重试耗尽仍被拦才抛 ComicBlockedError。
        prepare 为可选回调，在取页面内容前执行（如目录页需滑动到底触发懒加载）。
        timeout_ms/status 透传给 _goto_reliable：让轻量页导航短超时 + 可见状态，限速快速失败。
        返回处理后的页面 HTML。
        """
        for attempt in range(retries):
            self._ensure()  # 每个尝试前都保证暖机，避免 reset 后用冷会话直连被拦
            try:
                self._goto_reliable(url, wait_until=wait_until, retries=2,
                                    timeout_ms=timeout_ms, status=status)
            except ComicError:
                # 导航在限速下重建会话仍失败：当作软封处理，退避后由外层再试一轮
                self._reset_session()
                time.sleep(3 + attempt * 6)
                continue
            page = self._page
            page.wait_for_timeout(settle)
            if prepare is not None:
                try:
                    prepare()
                except Exception:
                    pass
            html = page.content()
            if self._is_block_page(html):
                self._reset_session()
                time.sleep(3 + attempt * 6)
                continue
            if self._is_desktop_shell(html):
                self._warmed = False
                self._ensure()
                continue
            return html
        raise ComicBlockedError(
            "Cloudflare Attention Required：该书/该 URL 连多个会话仍被拦——自动化指纹被判定。"
            "可换一本漫画，或稍后/换网络再试；若浏览器能正常看，说明只有无头会话被拦。")

    # ---------- 书名搜索 ----------
    @staticmethod
    def _result_state(page) -> list:
        """返回 [命中条数, 是否渲染出「條記錄」搜索模块]。

        count>0 = 真搜到结果。present=True = 搜索完成页已渲染（即使 0 条，也说明这次
        搜索是「真的没有结果」，而非被 Cloudflare 挑战页卡住没渲染）。这两个信号用来
        区分「真正 0 结果」和「搜索页根本没渲染（限速/挑战）」，避免把后者误报成
        「未搜到…相关结果」。
        """
        return page.evaluate(
            """() => {
              const hs = [...document.querySelectorAll('h3.module-title')]
                  .filter(h => h.textContent.includes('條記錄'));
              let n = 0, present = false;
              for (const h of hs) {
                const m = h.closest('.module');
                if (m) { present = true; n += m.querySelectorAll('ol.book-ol li').length; }
              }
              return [n, present];
            }""")

    def search(self, keyword: str) -> list[ComicHit]:
        # 结果缓存：同一关键词搜过就直接回，不重新去撞限速（限速窗口内连搜会触发 Cloudflare）
        if keyword in self._search_cache:
            return self._search_cache[keyword]
        last_exc: Exception | None = None
        for attempt in range(3):
            try:
                self._ensure()
                # 搜索页用短导航超时（15s）：热会话上被限速时不静默阻塞 90s，快速转重建会话
                self._goto_reliable(BASE + "/search.html", retries=2,
                                    timeout_ms=self._light_nav_ms,
                                    status=f"  [search] 打开搜索页({attempt + 1}/3)")
                page = self._page
                if self._is_block_page(page.content()):
                    self._reset_session()
                    time.sleep(3 + attempt * 6)
                    continue
                # 就绪轮询：等搜索框出现（替代固定 1.5s 硬等）；5s 没出现按挑战/限速处理
                try:
                    page.wait_for_selector("#searchkey, input[name=searchkey]", timeout=5000)
                except Exception:
                    pass
                box = page.query_selector("#searchkey") or page.query_selector("input[name=searchkey]")
                if not box:
                    self._reset_session()
                    time.sleep(2 + attempt * 4)
                    continue
                # 静置让 search_guard 完成 css/js/redeem 并设置 jieqiSearchJs cookie；
                # 否则提交太早、服务器按「无守卫指纹」的自动请求返回空白结果页（HTTP 200 空壳）。
                # 实测 2.5s（覆盖 redeem 的 120/800/2000ms XHR）稳定渲染出结果。
                page.wait_for_timeout(int(self._guard_settle_s * 1000))
                box.fill(keyword)
                page.keyboard.press("Enter")
                # 结果窗口：真搜索 1~3s 内就渲染出「條記錄」模块；把它从 self.timeout(90s)
                # 缩到 15s——模块始终没渲染即判为限速/挑战，快速重建会话重试，而非干等 90s。
                count = self._wait_search_results(page)
                if count is None:   # 被限速：模块一直没渲染出来
                    self._reset_session()
                    time.sleep(2 + attempt * 4)
                    continue
                hits = parse_search_results(page.content())
                self._search_cache[keyword] = hits
                return hits
            except ComicError as e:
                # 导航层限速（_goto_reliable 耗尽）：重建会话再由外层重试
                last_exc = e
                self._reset_session()
                time.sleep(2 + attempt * 4)
        raise ComicBlockedError(
            "Cloudflare Attention Required：搜索页连多个会话仍没渲染出结果——自动化指纹被判定，"
            "或站点对连续搜索暂时限速。可稍后重试，或直接用漫画编号。") from last_exc

    def _wait_search_results(self, page) -> int | None:
        """提交搜索后等待「條記錄」结果模块渲染，返回命中条数（0 = 真的没有结果）。

        真搜索通常 1~3 秒即渲染；把窗口从 self.timeout(90s) 缩到 _search_render_window(15s)，
        避免复用热会话连搜第二本时被 Cloudflare 限速、静默干等整分钟。若窗口内模块始终
        没渲染（说明被限速/挑战卡住），返回 None 供调用方重建会话重试。
        返回 0 = 模块渲染了但 0 条（真无结果，不再重试）。
        """
        start = time.time()
        deadline = start + self._search_render_window
        tty = sys.stdout.isatty()
        last_shown = -1.0
        saw_module = False
        while time.time() < deadline:
            # 提交表单会触发导航；导航期间 evaluate 抛「context destroyed」属正常竞态，
            # 吞掉它、继续以 400ms 轮询，直到结果「條記錄」模块渲染出来。
            try:
                count, mod = self._result_state(page)
                if mod:
                    saw_module = True
                if count > 0:
                    self._status_line("", clear=True)
                    return count
            except Exception:
                pass
            page.wait_for_timeout(400)
            now = time.time() - start
            # tty 下让「…Xs」在单行上每秒原地刷新；非 tty 每~10 秒整行打一行，避免刷屏
            if tty:
                if int(now) != int(last_shown):
                    last_shown = now
                    self._status_line(f"  [search] 等待搜索结果（限速则短等后重试）... {int(now)}s")
            elif int(now) != int(last_shown) and int(now) % 10 == 0:
                last_shown = now
                self._status_line(f"  [search] 等待搜索结果（限速则短等后重试）... {int(now)}s")
        self._status_line("", clear=True)
        return 0 if saw_module else None

    # ---------- 目录 ----------
    def get_catalog(self, comic_id: int) -> Comic:
        html = self._navigate_clean(f"{BASE}/read/{comic_id}/catalog",
                                    prepare=self._scroll_bottom,
                                    timeout_ms=self._light_nav_ms,
                                    status="  [catalog] 打开目录页")
        return parse_catalog(html, comic_id)

    # ---------- 章节图片 ----------
    def fetch_chapter_images(self, comic_id: int, chapter: Chapter,
                             progress=None) -> tuple[list[bytes], bool]:
        """抓取一章全部图片，返回 (JPEG 字节列表, 是否单调有序)。

        顺序 = data-src 在原始 HTML 里的出现顺序；再经 ordered_image_urls 校验是否递增。
        若命中 Cloudflare 软封页则重建会话（新指纹）重试一次。
        progress 为可选回调 progress(done:int, total:int)，随图片逐张下载更新（用于进度条）。
        """
        for attempt in range(3):
            self._ensure()
            captured: dict[str, bytes] = {}
            raw_html: list[bytes] = []

            def on_r(r):
                try:
                    if "motiezw.com" in r.url and "image" in (r.headers or {}).get("content-type", ""):
                        captured[r.url] = r.body()
                except Exception:
                    pass

            def on_doc(r):
                try:
                    if r.request.resource_type == "document" and "read" in r.url:
                        raw_html.append(r.body())
                except Exception:
                    pass

            # 注册（存到 self._handlers，供重建会话后重挂）
            self._register("response", on_r)
            self._register("response", on_doc)
            try:
                self._goto_reliable(chapter.url)
                page = self._page
                page.wait_for_timeout(4000)   # 等阅读器初始化、顶部图片懒加载
                html = (raw_html[0].decode("utf-8", "replace") if raw_html else page.content())
                if self._is_block_page(html):
                    self._reset_session()
                    time.sleep(3 + attempt * 6)
                    continue
                if self._is_desktop_shell(html):
                    self._warmed = False
                    self._ensure()
                    continue
                urls = parse_chapter_image_urls(html)
                if not urls:
                    # 兜底：raw_html[0] 可能是跳转/占位 doc（图片未渲染），用 live page 再取一次
                    urls = parse_chapter_image_urls(page.content())
                urls, monotonic = ordered_image_urls(urls)
                if not urls:
                    # 空 = 本章可能无图，或被 Cloudflare 限速导致图片未渲染。前者少见，
                    # 后者常见且瞬态——故重建会话（新指纹）重试，而不是立刻判死。否则
                    # 一章偶发为空会把整卷日志打断、丢掉整卷 PDF（实测 51325 反复出现）。
                    self._reset_session()
                    time.sleep(2 + attempt * 4)
                    continue

                # 强制触发全部图片请求（字节由 on_response 捕获）
                page.evaluate(_INJECT, urls)

                # 等待图片被 on_response 捕获。真实图片会在注入后数秒内批量进入；
                # 若约 25s 内没有任何新图且已有内容，判定剩余数据是预加载/占位（不会加载），
                # 提前收尾——否则会为这些不存在的图干等满 120s（obs: 368 某些章干等满超时）。
                # 保守取 25s：既避开无谓等待，又不至于把实际上会慢到的真实图片误剪掉。
                last = -1
                stall = 0
                deadline = time.time() + 120
                while len(captured) < len(urls) and time.time() < deadline:
                    page.wait_for_timeout(500)
                    if progress:
                        progress(len(captured), len(urls))
                    if len(captured) != last:
                        last = len(captured)
                        stall = 0
                    else:
                        stall += 1
                        if captured and stall >= 50:  # ~25s 无新图且已有内容 -> 收尾
                            break
                if progress:
                    progress(len(captured), len(urls))

                jpegs = self._decode_many(captured, urls)
                time.sleep(self.delay)
                return jpegs, monotonic
            finally:
                self._unregister_all()
        raise ComicError(
            f"章节 {chapter.id} 多次尝试仍取不到图片 URL（可能被 Cloudflare 反复限速，"
            f"或本章确无图）。")

    def _decode_many(self, captured: dict[str, bytes], urls: list[str]) -> list[bytes]:
        """按 urls 顺序把每张 avif 解码成 JPEG 字节；缺失的跳过。"""
        jpegs: list[bytes] = []
        for u in urls:
            data = captured.get(u)
            if data is None:
                continue
            jpg = self._blob_decode(data)
            if jpg:
                jpegs.append(jpg)
        return jpegs

    def _blob_decode(self, avif_bytes: bytes) -> bytes | None:
        b64 = base64.b64encode(avif_bytes).decode("ascii")
        r = self._page.evaluate(_DECODE_BLOB, b64)
        if "err" in r:
            return None
        try:
            return base64.b64decode(r["durl"].split(",", 1)[1])
        except Exception:
            return None

    def close(self):
        try:
            self._browser.close()
        finally:
            self._pw.stop()
