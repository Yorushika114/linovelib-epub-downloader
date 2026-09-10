# 免安装分发包：内置 Python 运行时与自包含 WPF

日期：2026-09-10
状态：设计待审阅

## 1. 目标

让整个项目**拷贝到任意 Windows 10/11 机器上双击即用**，目标机器无需安装
Python、无需安装 .NET SDK 或 Runtime、无需联网装依赖。

同时满足一条硬约束：**分发包与源码版行为必须完全一致**——同一套数据目录、
同一套桥接协议、同一套路径推导，零分叉。这条约束来自本项目的真实教训：
旧便携发布方案因使用独立桥接程序和数据目录，导致「BAT 与 EXE 的数据表现
不一致」，最终被整体移除（见 `docs/local/项目设计思路.md` 第 94 行）。

## 2. 现状与约束

### 现有启动链

```text
轻小说下载器.exe          源码启动器（.NET 应用，SelfContained=false）
  -> 向上查找含 main.py + wpf_bridge.py 的项目根
  -> dotnet build WPF 源码          ← 需要 .NET SDK
  -> LinovelibDesktop.exe
  -> spawn python wpf_bridge.py    ← 需要 Python
```

### 关键约束（已实测确认）

1. **`ProjectPaths.FindPython()` 已优先查找项目内 `.venv`**
   （`wpf/LinovelibDesktop/Services/ProjectPaths.cs:21`）：
   先找 `<root>/.venv/Scripts/python.exe`，不存在才回退 `"python"`。
   → 这是现成的接缝：内置运行时放在约定位置即可，**C# 侧零改动**。

2. **正文抓取用系统 Edge**（`linovelib/render.py:92`、`comic/fetcher.py:102,144`
   均为 `channel="msedge"`），playwright 只是驱动它。
   → **不需要**打包 1.4GB 的 playwright 浏览器。

3. **venv 不可跨机器搬运**（实测）：`pyvenv.cfg` 硬编码 `home = D:\anaconda`，
   且 venv 的 `Lib/` 只含 `site-packages`，**不含标准库**（stdlib 仍从基础解释器
   读取）。基础解释器还是 Anaconda，非标准 CPython。故 venv 无法满足分发需求。

4. **依赖编译扩展情况**（实测）：`lxml`(6 个 .pyd)、`PIL`(7 个 .pyd) 含编译扩展；
   `playwright`、`bs4`、`ebooklib`、`requests` 为纯 Python。
   → 全部依赖均有 win_amd64 wheel，嵌入式中可直接 pip 安装。

5. **WPF 自带单实例逻辑**（`App.xaml.cs:10-48`：命名 Mutex
   `Local\LinovelibEpubDownloader` + 激活已有窗口），源码启动器的激活职责冗余。

## 3. 方案选择

| | A. 嵌入式 Python（**选定**） | B. PyInstaller 冻结 | C. venv + 安装脚本 |
|---|---|---|---|
| 免装环境 | ✅ | ✅ | ❌ 仍需装 Python |
| 零分叉 | ✅ 同目录/同桥接 | ❌ 需重写路径推导 | ✅ |
| 体积 | Python 174MB + .NET 162MB | ~250MB+ | 最小 |
| 可读可改 | ✅ 源码保留 | ❌ 冻结难调试 | ✅ |

选定 A。B 被否决的核心原因：打包后 `sys.executable` 指向 exe 自身，路径推导、
桥接启动、数据目录都需重写——**等于重造当年被移除的那个分叉**。

## 4. 分发包结构

```text
轻小说下载器/                          ← 整个文件夹拷贝即用
├─ 轻小说下载器.exe                     ← = WPF 自包含发布产物（见 §5）
├─ runtime/
│   └─ python/                         ← 嵌入式 Python 3.10 + 全部依赖（174MB）
│       ├─ python.exe                  ← 解释器
│       ├─ python310.zip               ← 标准库
│       ├─ python310._pth              ← 路径配置（见 §6）
│       └─ Lib/site-packages/          ← 依赖
├─ linovelib/  comic/  main.py  wpf_bridge.py  launcher.py
├─ download.bat                        ← 构建脚本生成（见下）
├─ wpf/LinovelibDesktop/               ← 源码保留（可读可改）
└─ download/  _tmp_dl/                 ← 数据目录，与源码版完全相同
```

一处**有意的文件级差异**：分发版的 `download.bat` 由构建脚本重新生成，指向
`%~dp0runtime\python\python.exe`（回退 `python`），而非直接拷贝仓库根的同名文件——
后者调用裸 `python`，拷到无 Python 的机器上必然失败。这是「零分叉」约束下唯一的
例外，且只涉及**启动入口的措辞**，不涉及数据目录、桥接协议或路径推导。WPF 入口
（`轻小说下载器.exe`）不走 bat，故不受影响。

体积构成：嵌入式 Python 运行时 174MB（其中 playwright 自带 Node 驱动 107MB）
+ WPF 自包含 162MB ≈ **336MB**。

**数据目录不发生任何变化**：仍是 `PROJECT_ROOT/download/小说/`、
`download/漫画/`、`_tmp_dl/`（`linovelib/paths.py` 从 `__file__` 推导，
拷贝后自然指向新位置）。这是「零分叉」的具体含义。

## 5. .NET 侧：自包含发布

目标机器无 .NET，故分发版**不使用源码启动器**（它本身是 .NET 程序，
`SelfContained=false`，无 .NET 时连它都起不来）。

```bash
dotnet publish wpf/LinovelibDesktop/LinovelibDesktop.csproj \
  -c Release -r win-x64 --self-contained true -o <分发目录>
```

实测产物 162MB / 238 个 DLL（含 .NET 运行时本身），`LinovelibDesktop.exe`
可直接运行，不再现场 `dotnet build`，启动也更快。

**源码启动器 `wpf/LinovelibSourceLauncher/` 保留在开发仓库**，供开发者使用；
它不进分发包。开发者在源码仓库中的日常用法（`python launcher.py`、
`python main.py`、双击源码启动器）**均不受影响**——本次改动只新增分发路径，
不移除任何现有入口。

功能对等性已核实：启动器提供的「找项目根」（`ProjectPaths.FindRoot`，WPF 内已调用）
与「单实例/激活已有窗口」（`App.xaml.cs` 命名 Mutex + `ShowWindow`）在 WPF 中均已存在，
故去掉启动器不丢功能。

## 6. 嵌入式 Python 配置

### 6.1 `python310._pth`（关键配置）

python.org 的 embeddable 包默认**不启用 site**，故 pip 与 `site-packages` 均不可用。
需改写为：

```text
python310.zip
.
Lib\site-packages

import site
```

- `python310.zip`：标准库
- `Lib\site-packages`：第三方依赖（必须显式列出，embeddable 不自动扫描）
- 末行 `import site`：启用 site 机制（默认被注释掉）

### 6.2 安装依赖

```bash
PLAYWRIGHT_SKIP_BROWSER_DOWNLOAD=1 python.exe -m pip install -r requirements.txt
```

`PLAYWRIGHT_SKIP_BROWSER_DOWNLOAD=1` 是必须的：避免拖入 1.4GB 浏览器，
代码走系统 Edge。

### 6.3 路径接线

`ProjectPaths.FindPython()` 现在找 `<root>/.venv/Scripts/python.exe`。
内置运行时位于 `<root>/runtime/python/python.exe`，故需**一处最小改动**：
在该方法中增加对 `runtime/python/python.exe` 的探测（优先于 `.venv`，
以便替换运行时不依赖 venv 命名）。

```csharp
public static string FindPython(string root)
{
    var bundled = Path.Combine(root, "runtime", "python", "python.exe");
    if (File.Exists(bundled)) return bundled;
    var localPython = Path.Combine(root, ".venv", "Scripts", "python.exe");
    return File.Exists(localPython) ? localPython : "python";
}
```

回退链 `runtime/python` → `.venv` → 系统 `python` 保证开发机与分发版
共用同一份代码路径。

## 7. 构建脚本

新增 `tools/build_dist.ps1`，steps：

1. `dotnet publish` WPF 自包含 → 目标目录
2. 下载 python.org embeddable 3.10（版本与 `runtime/` 记录对齐）
3. 解压、改写 `_pth`、bootstrap pip
4. `PLAYWRIGHT_SKIP_BROWSER_DOWNLOAD=1` 安装 `requirements.txt`
5. 拷贝源码（`linovelib/`、`comic/`、`main.py`、`wpf_bridge.py`、`README.md`）
6. 输出体积报告

脚本须**幂等**：可重复执行，已存在的 `runtime/` 可复用或按 `--force` 重建（脚本内为
`-Force` 开关）。

**脚本必须以 UTF-8 with BOM 保存**。Windows PowerShell 5.1 对无 BOM 的 `.ps1` 按系统
ANSI 代码页解码，脚本内的中文注释与提示会乱码，进而报出一连串 `Unexpected token` /
`Missing closing '}'` 语法错误，脚本根本无法启动（首次执行即踩此坑）。
`tests/test_bundled_runtime.py::test_build_script_has_utf8_bom` 锁定该约定。

## 8. 版本一致性

嵌入式 Python 固定为 **3.10.11**（原型验证所用版本），写在
`tools/build_dist.ps1` 顶部的常量中，并在构建输出中打印实际版本。

说明：开发机当前为 3.10.18（Anaconda）。两者同属 3.10 系列，ABI 兼容，
实测依赖均可正常安装运行。此处不以「与开发机完全同版」为目标——
embeddable 包只提供到 3.10.11，且真正需要保证的是**依赖可用**与
**数据目录一致**，而非解释器补丁号一致。若将来项目改用 3.11/3.12，
需同步更新该常量。

## 9. .gitignore

分发包产物**不得入库**（体积巨大）。新增忽略规则：

```gitignore
runtime/
wpf/LinovelibDesktop/publish/
dist/
```

注意：当前 `.gitignore` **无** `venv`/`runtime`/`publish` 规则，必须补上，
否则 174MB 运行时可能被误提交。

## 10. 验证方式

设计已在原型中端到端验证（非推测）：

- 下载 embeddable 3.10.11 → 启用 site → bootstrap pip → 安装全部依赖 ✅
- **用嵌入式 Python 运行项目真实搜索**：
  `败北女角太多了` → `3095 | exact: True` ✅
  （正是此前因 302 重定向而失败的查询）
- 未拖入任何浏览器目录（确认走系统 Edge）✅
- WPF 自包含发布：162MB / 238 DLL，exe 可独立运行 ✅

**实施后的验收标准**：

1. 在**未安装 Python 与 .NET** 的 Windows 机器（或新建虚拟机）上，
   拷贝整个文件夹后双击 exe，窗口正常启动。
2. 按书名搜索可用（验证桥接 + 嵌入式 Python + 系统 Edge 全链路）。
3. 下载一部小说，EPUB 落到 `download/小说/<书名>/`，
   与源码版运行结果路径完全一致。
4. 漫画路径同样可用。

第 1 条是本次的核心验收项——它同时覆盖了此前暴露的两个问题
（「exe 打不开」= 缺 .NET；「搜索不到」= 缺 playwright 依赖）。

## 11. 明确不做

- 不打包 playwright 浏览器（用系统 Edge，省 1.4GB）
- 不引入 PyInstaller/Nuitka 冻结（会重造分叉）
- 不改变数据目录结构（保持零分叉）
- 不把分发包产物提交进 Git
- 不改动下载/解析/EPUB 业务逻辑

## 12. 风险

| 风险 | 说明 | 缓解 |
|---|---|---|
| 体积较大（~336MB） | 免装的必然代价 | 构建脚本输出体积报告；文档说明构成 |
| 嵌入式 Python 缺部分标准库模块 | embeddable 不含 tkinter、test 等 | 本项目为无 GUI 的 CLI/桥接，用不到；已在原型验证全链路 |
| `._pth` 配置错误致 pip 失效 | 漏写 `Lib\site-packages` 或 `import site` | §6.1 明确列出；构建脚本按固定模板生成 |
| 目标机会话缺 Edge | 极少数精简系统 | 已有清晰报错路径；文档说明需 Windows 自带 Edge |
| 运行时被误提交 | `.gitignore` 当前无相关规则 | §9 必须先补规则再构建 |
