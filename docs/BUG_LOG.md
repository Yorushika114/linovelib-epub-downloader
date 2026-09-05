# Bug 日志（v1.0.0 → 至今）

> 本项目是 linovelib 轻小说 EPUB 下载爬虫（Python CLI + Playwright 渲染 + WPF 桌面端）。
> 本文记录从 v1.0.0 发布至今遇到并处理过的 bug，按【版本】与【类别】归档。
>
> - 爬虫核心类（正文乱序 / 反爬 / 封禁 / EPUB 生成）是贯穿整个 1.0.x 的"知识点"，也最值得回看，放在最前。
> - WPF 桌面端与书名搜索类 bug 都能在 git 提交史里定位到具体版本。
> - 状态标记：✅ 已解决 / 🔧 已规避（改行为，非根治）/ 📌 已知限制。

---

## 〇、速览表

| 版本 | 类别 | 问题 | 状态 | 正文说明 |
|------|------|------|------|----------|
| 1.0.x 贯穿 | 爬虫 | 正文乱序（服务器确定性洗牌 + 客户端 JS 重排） | ✅ | §2.1 |
| 1.0.x 贯穿 | 爬虫 | 正文三来源互相矛盾，无单一参考无关真序 | ✅ | §2.2 |
| 1.0.x 贯穿 | 爬虫 | 中尾块残留错序（种子起点算错） | ✅ | §2.3 |
| 1.0.x 贯穿 | 爬虫 | 短章 / 尾声 / 插画章整章丢失 | ✅ | §2.4 |
| 1.0.x 贯穿 | 爬虫 | 生成 EPUB 报 WinError32（两类占用） | ✅ | §2.5 |
| 1.0.x 贯穿 | 爬虫 | 图片防盗链 403、正文被截断"內容加載失敗…" | ✅ | §2.6 |
| 1.0.x 贯穿 | 爬虫 | 个别整本被 Cloudflare 按 URL 封禁 | ✅ | §2.7 |
| 1.0.0 | 爬虫 | EPUB 临时文件互相污染 / 分卷封面错配 | ✅ | 该版基座提交修复 |
| 1.0.1 | 交互 | 合并询问过于强制，`--merge` 改为显式 opt-in | ✅ | v1.0.1 |
| 1.0.2 | 下载 | 目标 EPUB 已存在仍重复下载 | ✅ | v1.0.2 |
| 1.0.2 | 爬虫 | 章末整幅插画页（0 段 + 图）被误判为空页丢弃 | ✅ | v1.0.2 |
| 1.0.3–1.0.6 | WPF | 窗口启动、all→flag 映射、字段保留、日志去重、筛选等 | ✅ | §4 |
| 1.0.4 | 搜索 | 书名搜索（`--name`/Box）不可靠解析 | ✅ | §3.1 |
| 1.0.x 贯穿 | 搜索 | 站点搜索 `/S6/?searchkey=` 对脚本化请求吐空壳 | ✅ | §3.2 |
| 1.0.6 之后 | 搜索 | **按书名搜到无结果后无法继续搜索 / 卡死** | ✅ | §3.3（本会话修复） |

---

## 一、阅读指引

- **正文类（§2）** 源于两台反爬叠加：服务器对「纯 HTTP 请求」下发的是**确定性洗牌**的乱序正文，
  真序只活在客户端 JS / 浏览器实时 DOM 里。看懂这条主线，"正文乱序三来源""中尾块错序"就都顺了。
- 深挖源码见 `docs/local/项目设计思路.md`（gitignore，不进仓库），以及开发过程中的 memory 记录。

---

## 二、核心爬虫类（贯穿 1.0.x，反爬 / 乱序 / 封禁）

### 2.1 正文乱序 —— 服务器确定性洗牌 + 客户端 JS 重排

**现象**：纯 `requests` 抓到的章节正文顺序错乱，比如"直勾勾→生日喔"被当成真序发给脚本，
而真实浏览器里是"直勾勾→你有点冷漠喔"；且段落数变少。

**根因（两层反爬叠加）**：
1. **服务器确定性洗牌**：对纯请求下发乱序 R——每页 `#TextContent` **前 20 段天然有序**（所以开头本来就是真序），
   其后缀被 Fisher-Yates 打乱。随机源是 LCG `seed=(seed*9302+49397)%233280`，
   `swap_idx=int(seed/233280*(i+1))`，**种子起点由章节 id 推导**。乱序是确定性伪随机重排，只有猜对种子才能逆置换还原。
2. **真序只活在客户端 JS / 实时 DOM**：对真浏览器，`chapterlog.js` 在客户端把整章重排成真序并插入克隆段；
   但 `page.html`（outerHTML 快照）里 `#TextContent` 仍是乱序 R。且 scroll 懒加载 + `pctheme`（`Math.random` 给段落随机
   class + 克隆插入 + 隐藏）会把**开头**打乱 → 浏览器路径拿到的是"错头 + 真尾"。
   **raw HTML 里没有任何内嵌真序标记**，无法静态反推。

**结论 / 交付**：正文字体统一走 **raw + 逐页 Fisher-Yates 逆置换 + 文本去重**（参考无关），不再用浏览器抓正文。
关键算法：
- `linovelib/fetcher.py::unscramble_paginated`：固定前 20 段真序，对后缀做逆置换。
- `linovelib/fetcher.py::get_page_body`：raw `get_html` → 查截断标记重试 → 抽 `#TextContent` `<p>` → 逐页逆置换 → 抽插图。
- `linovelib/downloader.py`：页内 `_dedup_keep_order` 去克隆段；章合并后**章内再去重**（页交界末段 = 下页首段会重复）。
- 参考书只在用户**显式**运行 `reorder_epub --reference <path>` 时用于下载后单文件校正，**不自动、不跨卷、不进下载路径**。

### 2.2 正文"三来源互相矛盾"，无单一参考无关真序

**现象**：对同一章（如第 4 卷 ch3，cid 181030）分别跑纯 requests、真浏览器、raw HTML，三段"序"互相矛盾，
哪个都不能独立拿到整本真序，容易让人不停改参数瞎试。

**实测结论**（对照正版参考书真序，勿再质疑）：
| 来源 | 行为 | 能否独立拿真序 |
|------|------|----------------|
| 纯 requests（raw） | 每页前 20 段真序，后缀被洗牌 | ✅ 逐页逆置换后整章真序 |
| 真浏览器实时 DOM | 尾部整块真序，但 scroll 驱动懒重排把开头打乱 | ❌ 别采此路径做正文 |
| raw HTML | 无任何内嵌真序标记，`pctheme` 只是客户端 `Math.random` 克隆+隐藏，**从不重排原始段** | ❌ 无法静态反推 |

### 2.3 中尾块残留错序 —— 逆置换种子起点算错

**现象**：逆置换后，正文**开头与结尾对**，但中尾一个连续块还是错的（"开口→原来如此→拍手→做出了断→咦为什么→
亡灵退场→如果没办法→结业式→随波逐流"这段错序）。

**根因**：旧代码硬编码种子 `cid*127+235(+page)`，与真实种子 `cid*126+232`（**与页码无关**）只差一个常数。
LCG 流在差值处**于一个连续块错序、其余仍对齐**——这正是"中尾块局部错序"的症状。
**不是 LCG 常量过时**（9302/49397/233280、`fixedLength=20` 都是对的），是**种子起点算错**。

**结论 / 交付**：用参考书真序当 oracle，对 ch3 的 6 个页（1/2/3/5/7/13）盲测候选公式，`cid*126+232`（no-page）一律碾压。
已改 `unscramble_paginated` 为 `seed=int(chapter_id)*126+232`，弃用 `with_page`。端到端核验：ch3 末段 8 个关键标记句索引严格递增。

### 2.4 短章 / 尾声 / 插画章整章丢失

**现象**：尾声（如"秘密"）、序章、插画章段落极少甚至只有图，被误判为空页反复重试后返回空 → 整章丢失。

**根因**：`get_page_body` 曾以"段落数 < 10"判空重试，短章被判为无效。

**结论 / 交付**：改为**只有"无段落且无插图"才算空页才重试**，短章合法保留。⚠️ 现在别再用段落数判断空页。

### 2.5 生成 EPUB 报 WinError32（PermissionError）

**现象**：写 `download/<标题>/<标题>.epub` 时抛 `WinError 32` 失败。

**根因**：两类占用：
- ① 临时文件 `.epub.tmp` 被杀毒/Defender 实时扫描短暂锁定；
- ② 目标 EPUB 已被阅读器 / Calibre 打开（写共享被拒），无法覆盖。

**结论 / 交付**：
- ① `write_epub` 对 AV 短暂扫描**重试 8 次**（间隔 0.5s）；
- ② finalize 遇 `PermissionError` 改写到**同名 + 空格 + 序号**（`书名. 1.epub`），`build_epub` **返回实际写出的路径**，
  main.py 打印真实路径。
- 占用时不要 `rm`（会被报 Device busy），改用 `_alternate_path` 逻辑。

### 2.6 图片防盗链 403 & 正文被截断

- **图片 CDN（img3.readpai.com）防盗链**：缺失 Referer 返回 403 → `IMAGE_HEADERS` 只带 `UA + Referer`。
- **正文被截断**：站点对"极简 UA + Referer"的脚本式请求会扣留长文，在章末追加"**內容加載失敗！請刷新或更換瀏覽器**"并截断正文 →
  补全 `Accept` / `Accept-Language` / `Sec-Fetch-*` 的头（`BROWSER_HEADERS`），并在 `get_page_body` 里检测该标记重试。

### 2.7 个别整本被 Cloudflare 按 URL 封禁

**现象**：个别书（如无职转生主篇 id=2013）的 `/novel/{nid}/` 下**每一页**（落地页 / 目录 / 卷页 / 章节页）都返回
HTTP 403 + 静态 "**Attention Required! | Cloudflare**" 拦截页，`requests`（完整头 + retries=8）与真浏览器（含已暖机
持有 `cf_clearance` 的同会话）**都拿不到**。同系列其它书（如 4325 蛇足篇）不受影响——是**按 URL 规则**的防火墙封禁，
且大概率因书名（无职转生是重授权 / DMCA 作品）触发，**重试 + 换头 + 换浏览器都无解**。

**结论 / 交付**（🔧 规避非根治）：
- `Fetcher.get` 用 `_is_cloudflare_block(resp)`（403 + "Attention Required" + "cloudflare"）识别，抛 `CloudflareBlockedError`
  ——它是普通 `Exception` 而非 `RequestException`，**逃出重试循环快速失败**（重试赢不了防火墙规则）。
- `main.py` 在 `fetch_novel` 与目录页直取两处捕获，打印可执行提示"换用未被屏蔽的编号/卷"，并以非零码退出。
- 这是本工具唯一现实的"一本书根本下不了"场景，正确行为是**清晰提示**而非裸 `403 Client Error` 堆栈 + 刷屏失败章节。

---

## 三、书名搜索

### 3.1 书名搜索不可靠（v1.0.4）

`--name` 与输入框按书名解析不可靠，曾仅靠站外引擎。已使 name 解析**可靠**：先权威搜索，Bing/DDG 兜底，
多候选时按交互/TTY 决定（`test_readme`/`test_resolver` 覆盖）。提交 `5a53255`。

### 3.2 站点搜索 `/S6/?searchkey=` 对脚本化请求永远吐空壳

**现象**：直接用 `requests` 请求站点自带搜索接口，返回空 `<body>`，解析不出任何结果。

**根因**：站点搜索页是**客户端渲染 + 需 Cloudflare cookie**；本会话未先访问首页拿 cookie 时，搜索页常是空壳。

**结论 / 交付**：必须用**真实浏览器**（`RenderFetcher.search_html`）先**暖机首页**再跳转搜索，才能拿到结果。
权威来源层级：`RenderFetcher.search_html`（本站浏览器自搜，参考无关）→ 脚本化 `/S6/`（廉价尝试）→ Bing → DDG。
站外引擎（Bing 常静默放宽 site:、DDG 限流、Sogou/360 对 site: 无结果）**只作兜底**，时好时坏。

### 3.3 【本会话修复】按书名搜到无结果后无法继续搜索 / 卡死

**现象**：按书名搜索一个不存在的书名，返回"未找到"后，**第二次搜索（或后续任意搜索）会卡死**，
`SearchButton` 一直禁用，无法继续搜索。

**根因（两层）**：
1. **权威搜索结果未被当作终局**：`_search_hits` 里，即使**权威浏览器搜索成功返回空**（站点明确"查无此书"），
   仍继续 fallthrough 到脚本化 `/S6/` + Bing + DDG 三个站外探测。国内网络下 Bing/DDG 每个可能挂起 60–120s，
   一次"找不到书名"的搜索会累加到数分钟，期间按钮持续禁用。
2. **搜索路径没有硬时限**：`RenderFetcher.search_html` 的搜索页 `goto(..., wait_until="networkidle", timeout=90000)`
   **未包 try/except**，networkidle 不触发会阻塞 90s 再抛；`wpf_bridge.py` 结束时 `browser.close()` 若卡在 Edge/Playwright
   关闭，Python 进程不退出 → C# `WaitForExitAsync` 永挂 → 按钮永禁用。第二次搜索再叠加一轮浏览器生命周期 + 3 个外部请求，
   若 Edge 关闭挂起则**永不返回**。

**修复（双保险）**：
1. `linovelib/resolver.py::_search_hits`：**权威浏览器搜索成功（哪怕返回空列表）即视为最终结论并立即终止**，
   不再落到站外引擎。仅当 `browser is None` 或浏览器**抛错**（无 playwright / 启动失败 / 搜索页被 Cloudflare 挑战）
   时才静默退回站外引擎兜底。新增 `_looks_like_cf_challenge`：搜索页命中 Cloudflare 硬拦截页时抛
   `CloudflareBlockedError` 回退，避免把"被拦"误报成"未找到"。✅
2. `wpf/LinovelibDesktop/Services/DownloaderBridge.cs::ResolveAsync`：给整个解析加 **60s 硬时限**，
   超时 `process.Kill(entireProcessTree: true)` 连 Playwright/Edge 子进程一起终结并返回空 →
   `MainWindow` 的 `finally` 必然恢复 `SearchButton`，界面不再永久卡死。✅

**验证**：新增两测 `test_browser_authoritative_empty_stops_fallthrough`（权威空结果不触发外部请求）、
`test_browser_cf_challenge_falls_back_to_external`（被挑战回退），`tests/test_resolver.py` 17 passed；全量
`pytest --ignore tests/test_bilibili_bangumi_rank.py` 88 passed；`dotnet build` 0 错误 0 警告。

---

## 四、WPF 桌面端（随提交史，版本明确）

> 这些是从 git 提交史梳理的桌面端问题；版本标注为该修复**合并**的版本（`v1.0.x` 之后的迭代）。

| 版本 | 提交 | 问题 | 修复 |
|------|------|------|------|
| v1.0.3 | `5afe4c9` | WPF 窗口启动异常 | 恢复 WPF 窗口启动 |
| v1.0.3 | `42ecdbc` | 卷号映射错误 | WPF "all" 正确映射到 CLI flag |
| v1.0.3 | `3d88bc9` | 章节字段丢失 | 保留 WPF 事件里的章节字段 |
| v1.0.3 | `1f32c46` | 已存在卷出现在章节队列 | 隐藏已有卷 |
| v1.0.3 | `157e7d8` | 生成日志重复输出 | WPF 生成输出日志去重 |
| v1.0.3 | `9e65cc6` | 未列全剩余章节 | 下载前列出全部剩余章节 |
| v1.0.3 | `17dfa29` | 无法安全中断下载 | 支持安全取消 |
| v1.0.3 | `f25b008` | 内置 WPF launcher 启动失败 | batch 启动内置 WPF |
| v1.0.4 | `d553e85` | 无书名候选选择 / 下载进度 | 书名候选选择 + 进度展示 |
| v1.0.5 | `27ec014` | WPF launcher 启动回归 | 恢复 WPF launcher 启动 |
| v1.0.5 | `270c5d8` | 头像图标资源未打包 | 打包头像图标资源 |
| v1.0.5 | `689f4df` | 搜索候选不同步 | 保持 WPF 搜索候选同步 |
| v1.0.5 | `7312789` | 无法按状态筛章节 | 章节队列按状态筛选 |
| v1.0.6 | `c895cff` | WPF bridge 事件泄露到日志 | 路由 WPF bridge 事件，不泄露到日志区 |
| v1.0.6 | `236c94c` | 无下载目录入口 / 无背景 | 打开下载目录按钮 + 插画背景 |

---

## 五、遗留 / 已知限制

- **§3.3 的复现**：本仓库开发者环境网络可快速访问 Bing/DDG，无法复现"卡死"；
  已在沙箱用 UIA 驱动 GUI 走完两次"不存在书名"搜索并确认按钮保持响应（因网络快）。卡死触发依赖慢网络/Edge 关闭挂起，
  Fix 1（不再相信慢站外引擎）+ Fix 2（硬超时 Kill）从根上消除。
- **§2.7 Cloudflare 封禁**：是站点规则，**无法绕过**。只能提示用户换未被屏蔽的编号/卷（🔧，非根治）。
- **§2.3 参考书核对**：参考书与站点/EPUB 的章节命名可能错位，核对时用章节内独有场景句定位，勿按序号猜。

---

## 六、开发环境踩坑（写日志时一并记下，避免重踩）

- 中文 Windows / GBK：stdout 保持 GBK，只 `reconfigure(errors="replace")`，**不能设 UTF-8**；CJK 文件名 U+3099 打印前转 ASCII。
- Edge：`C:\Program Files\Microsoft\Edge\Application\msedge.exe`；DrissionPage `run_js` 需显式 `return`。
- `_split_header` 别把 soup 元素变量重赋值为列表；soup 元素变量名别与 split 结果同名。
- 全量 `pytest` 需 `--ignore tests/test_bilibili_bangumi_rank.py`（该测试已被回滚孤立，会破坏 collection）。

_本日志随版本持续维护；新 bug 修复后请同步追加一行到速览表并补一节正文说明。_
