"""分发版自检：把「拷到目标机器能不能用」变成一条可执行的命令。

设计 §10 的验收标准（在未装 Python/.NET 的机器上开箱即用）此前只能靠人工试。本脚本
把它落成检查项，构建脚本会调用它作为出厂闸门，用户在目标机器上也能自己跑一遍：

    runtime\\python\\python.exe tools\\verify_dist.py
    runtime\\python\\python.exe tools\\verify_dist.py --search 败北女角太多了

为什么用 Python 而不是在 build_dist.ps1 里内联 PowerShell：
  1. PowerShell 5.1 会把原生命令的 stderr 逐行包成 ErrorRecord（NativeCommandError），
     配合 $ErrorActionPreference='Stop' 会中止脚本——即便该命令退出码为 0。而 argparse
     把 --help 写到 stderr，正好踩中。
  2. 子进程输出在本机是 GBK 而非 UTF-8，任何 `-match '轻小说下载器'` 都是在拿乱码比对。
  3. 检查逻辑放在 .py 里可以单测（tests/test_bundled_runtime.py），内联 PowerShell 不行。

本脚本不依赖 pytest、不依赖网络（除非显式 --search）、不写任何文件。
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path


def _bootstrap_path() -> Path:
    """把分发版根目录放进 sys.path，并返回它。

    嵌入式的 ._pth 已含 `..\\..`（项目根），正常运行时这一步是冗余的；但本脚本也可能
    被开发机的系统 Python 从别处调用（例如从 dist 外部跑），那时代码要能找到 `main`。
    先算根目录再导入，两种环境都成立。

    **警告**：正因为注入了这个路径，本进程内的 `import main` 无论 ._pth 写成什么样都会
    成功——它测的是「源码在不在」，不是「应用能不能起来」。这条致命的检查必须放到
    子进程里做，见 check_app_launch()。
    """
    root = Path(__file__).resolve().parent.parent
    if str(root) not in sys.path:
        sys.path.insert(0, str(root))
    return root


ROOT = _bootstrap_path()


class Report:
    """收集检查结果，最后统一决定退出码。"""

    def __init__(self) -> None:
        self.failures: list[str] = []
        self.count = 0

    def check(self, label: str, ok: bool, detail: str = "") -> bool:
        self.count += 1
        mark = "OK  " if ok else "FAIL"
        line = f"  [{mark}] {label}"
        if detail:
            line += f"：{detail}"
        print(line)
        if not ok:
            self.failures.append(label)
        return ok


def _setup_stdout() -> None:
    """让中文在 cmd 窗口里正常显示，而不是 UnicodeEncodeError 或乱码。"""
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass


def check_interpreter(report: Report) -> None:
    """确认跑的是哪一个解释器——内置运行时还是开发机的 Python。"""
    print("\n解释器")
    bundled = ROOT / "runtime" / "python" / "python.exe"
    exe = Path(sys.executable).resolve()
    is_bundled = bundled.exists() and exe == bundled.resolve()
    report.check(
        "解释器来源",
        True,
        f"{exe}{'（内置运行时）' if is_bundled else '（非内置——开发环境属正常）'}",
    )
    report.check("Python 版本", sys.version_info[:2] == (3, 10),
                 f"{sys.version.split()[0]}（嵌入式为 3.10.x）")

    # 根目录由 __file__ 推导，所以本脚本**必须用它自己所在的那份拷贝**来跑。
    # 若在仓库里执行 dist/tools/verify_dist.py，ROOT 会变成仓库根，校验的是仓库
    # 而非分发版（实测踩过：PROJECT_ROOT 打出了仓库路径）。上面的「解释器来源」
    # 一行已如实指出这一点；此处再确认分发版根目录下确有运行时。
    if not bundled.exists():
        report.check(
            "内置运行时存在",
            False,
            f"{bundled} 不存在——本目录不是分发版，或者构建时 runtime/ 未生成。",
        )


def check_modules(report: Report) -> None:
    """项目自身模块必须能导入——这正是 ._pth 漏写 `..\\..` 时失败的地方。"""
    print("\n项目模块")
    try:
        import linovelib
        report.check("import linovelib", True, f"v{linovelib.__version__}")
    except Exception as exc:  # noqa: BLE001 - 自检要报告任何异常，不挑类型
        report.check("import linovelib", False, f"{type(exc).__name__}: {exc}")

    try:
        import comic
        report.check("import comic", True, getattr(comic, "__name__", "ok"))
    except Exception as exc:  # noqa: BLE001
        report.check("import comic", False, f"{type(exc).__name__}: {exc}")

    # main 是被 wpf_bridge 直接 import 的模块，桥接起不来最常见的原因就是它。
    try:
        import main
        report.check("import main（桥接的依赖）", callable(getattr(main, "main", None)),
                     "main() 可调用")
    except Exception as exc:  # noqa: BLE001
        report.check("import main（桥接的依赖）", False, f"{type(exc).__name__}: {exc}")

    # 导入桥接模块本身就是对「应用能否启动」的检验，且不必运行 argparse。
    try:
        import wpf_bridge
        report.check("import wpf_bridge", callable(getattr(wpf_bridge, "event_to_json", None)),
                     "事件编码器就位")
    except Exception as exc:  # noqa: BLE001
        report.check("import wpf_bridge", False, f"{type(exc).__name__}: {exc}")


def check_app_launch(report: Report) -> None:
    """按应用的**真实方式**启动一次桥接——必须在子进程里做。

    这是本脚本唯一能发现「._pth 漏写 `..\\..`」的检查，必须如实存在：

    嵌入式的 `._pth` 一旦存在，Python 就**不再**自动把脚本所在目录加进 `sys.path`。而
    WPF 是以 `python <root>\\wpf_bridge.py` 启动桥接的（DownloaderBridge.cs），
    `wpf_bridge.py` 又 `import main`。所以少了 `..\\..` 应用直接起不来。

    为什么不能在本进程里 import 了事：本模块的 `_bootstrap_path()` 已经把分发版根目录
    塞进了 `sys.path`，于是 `import main` 永远成功——**测不出这个故障**。实测：把 `..\\..`
    从 `._pth` 里剥掉后，真实启动 `wpf_bridge.py` 以
    `ModuleNotFoundError: No module named 'main'` 退出 1，而本脚本仍报 15/15 全绿。
    子进程不会继承父进程对 `sys.path` 的修改，路径模型才与真实运行时一致。

    用 `--help` 而非真跑一轮：argparse 会在解析完参数后立即退出，既能证明整条导入链
    （wpf_bridge → main → linovelib/comic）走通，又不会触发任何网络或下载行为。
    """
    print("\n应用启动（子进程，与 WPF 的调用方式一致）")
    bundled = ROOT / "runtime" / "python" / "python.exe"
    if not bundled.exists():
        # 开发机上没有内置运行时，无法按真实方式验证；check_interpreter 已单独报过
        # 「内置运行时存在」这一条失败，此处不重复计失败，只说明跳过原因。
        print(f"  [skip] 无内置运行时，改由当前解释器验证启动"
              f"（{Path(sys.executable).name}）")
        exe = Path(sys.executable)
        extra: list[str] = []
    else:
        exe = bundled
        # -B 不写字节码（免得把 __pycache__ 留进包里），-I 等价于 -P（勿把脚本目录
        # 预置进 sys.path），确保测的是 ._pth 的真实行为。
        extra = ["-B", "-I"]

    bridge = ROOT / "wpf_bridge.py"
    if not bridge.exists():
        report.check("桥接脚本存在", False, f"{bridge} 不存在")
        return

    try:
        proc = subprocess.run(
            [str(exe), *extra, str(bridge), "--help"],
            cwd=str(ROOT),
            capture_output=True,
            timeout=120,
        )
    except subprocess.TimeoutExpired:
        report.check("桥接可启动", False, "启动超时（120s）")
        return
    except Exception as exc:  # noqa: BLE001
        report.check("桥接可启动", False, f"{type(exc).__name__}: {exc}")
        return

    err = (proc.stderr or b"").decode("utf-8", "replace").strip()
    if not report.check("桥接可启动（python wpf_bridge.py --help）",
                        proc.returncode == 0,
                        f"退出码 {proc.returncode}"):
        # 只摘最后几行：ModuleNotFoundError 的真凶在栈尾，前面全是 Python 自己的帧。
        tail = "\n".join(err.splitlines()[-4:]) if err else "(无 stderr)"
        print(f"        {tail}")
        if "No module named 'main'" in err:
            print("        → ._pth 缺少项目根 `..\\..`，桥接无法 import main。")


def check_no_build_intermediates(report: Report) -> None:
    """分发版里不得夹带开发机的构建中间产物。

    `artifacts/` 是 MSBuild 的中间目录（.gitignore 已忽略），其中
    `*.FileListAbsolute.txt` 记录的正是**上一次构建的产物绝对路径**——夹进分发包就等于
    泄漏开发机的 `C:\\Users\\<用户名>\\...`。构建脚本原先只按 `\\(bin|obj)\\` 过滤，
    匹配不到 `wpf-release-verify-obj` 这类自定义中间目录名，实测漏了进来。
    """
    print("\n产物洁净度")
    strays: list[str] = []
    for path in ROOT.rglob("*"):
        if not path.is_dir():
            continue
        if path.name not in ("artifacts", "__pycache__"):
            continue
        rel = path.relative_to(ROOT)
        # runtime/ 下的字节码是 pip 装的第三方包自带的，属正常运行产物，不是夹带。
        # 要查的是**项目源码**的字节码——它按绝对路径生成，会把开发机路径带进包里。
        if rel.parts and rel.parts[0] == "runtime":
            continue
        strays.append(str(rel))
    report.check("无构建中间产物（artifacts/、项目 __pycache__/）", not strays,
                 f"发现 {len(strays)} 个：{strays[:3]}" if strays else "干净")


def check_paths(report: Report) -> None:
    """数据目录必须落在分发版自己身上，且与源码版同名（零分叉）。"""
    print("\n数据目录")
    try:
        from linovelib.paths import CACHE_DIR, NOVEL_DIR, PROJECT_ROOT
    except Exception as exc:  # noqa: BLE001
        report.check("读取路径配置", False, f"{type(exc).__name__}: {exc}")
        return

    report.check(
        "PROJECT_ROOT 指向分发版根目录",
        Path(PROJECT_ROOT).resolve() == ROOT,
        str(PROJECT_ROOT),
    )
    report.check("小说目录名", NOVEL_DIR.name == "小说", str(NOVEL_DIR))
    report.check("缓存目录名", CACHE_DIR.name == "_tmp_dl", str(CACHE_DIR))

    try:
        from comic.cli import MANGA_DIR
        report.check("漫画目录名", MANGA_DIR.name == "漫画", str(MANGA_DIR))
        report.check(
            "小说与漫画同处 download/ 下",
            NOVEL_DIR.parent == MANGA_DIR.parent,
            str(NOVEL_DIR.parent),
        )
    except Exception as exc:  # noqa: BLE001
        report.check("读取漫画路径配置", False, f"{type(exc).__name__}: {exc}")


def check_event_protocol(report: Report) -> None:
    """桥接协议的基本契约：事件能编码成单行 JSON，中文不被转义成 \\uXXXX。"""
    print("\n桥接协议")
    try:
        from linovelib.events import DownloadEvent
        from wpf_bridge import EVENT_PREFIX, event_to_json
    except Exception as exc:  # noqa: BLE001
        report.check("导入事件模块", False, f"{type(exc).__name__}: {exc}")
        return

    try:
        line = event_to_json(DownloadEvent("progress", message="测试进度"))
        report.check("事件带前缀", line.startswith(EVENT_PREFIX))
        report.check("事件为单行", "\n" not in line and "\r" not in line)
        report.check("中文可读（ensure_ascii=False）", "测试进度" in line)
    except Exception as exc:  # noqa: BLE001
        report.check("编码事件", False, f"{type(exc).__name__}: {exc}")


def check_browser(report: Report) -> None:
    """正文抓取靠系统 Edge。缺 Edge 的机器应在这里给出明确信号。"""
    print("\n系统 Edge（正文抓取依赖）")
    try:
        from linovelib.fetcher import Fetcher
    except Exception as exc:  # noqa: BLE001
        report.check("导入 fetcher", False, f"{type(exc).__name__}: {exc}")
        return

    try:
        resolved = Fetcher()._resolve_edge()
    except Exception as exc:  # noqa: BLE001
        report.check("探测 Edge 路径", False, f"{type(exc).__name__}: {exc}")
        return

    # _resolve_edge 返回 None 不代表不可用：DrissionPage 自己会去找 Edge（实测
    # shutil.which('msedge') 为 None 时它仍能正常启动）。这里只作提示，不算失败。
    report.check(
        "Edge 探测",
        True,
        resolved or "未由 PATH 探得，将由 DrissionPage 自行定位（实测可用）",
    )


def check_comic_search(report: Report, keyword: str) -> None:
    """漫画搜索实测：覆盖「唯一命中被 302 直达详情页」这条路径。

    站点在查询词能唯一定位一部漫画时不渲染结果列表，而是直接跳 /detail/{id}.html。
    漫画侧原先没有处理这种情况，会把一次成功命中误报成「Cloudflare 被拦」。
    """
    print(f"\n漫画搜索实测：「{keyword}」")
    try:
        from comic.fetcher import ComicFetcher
    except Exception as exc:  # noqa: BLE001
        report.check("导入漫画抓取器", False, f"{type(exc).__name__}: {exc}")
        return

    fetcher = ComicFetcher(delay=0.5, headless=True)
    try:
        hits = fetcher.search(keyword)
    except Exception as exc:  # noqa: BLE001
        report.check("漫画搜索", False, f"{type(exc).__name__}: {exc}")
        return
    finally:
        try:
            fetcher.close()
        except Exception:  # noqa: BLE001
            pass

    report.check("返回候选", bool(hits), f"{len(hits)} 条")
    if hits:
        top = hits[0]
        report.check("首条标题吻合", top.title.strip() == keyword.strip(),
                     f"{top.id} | {top.title}")


def check_search(report: Report, keyword: str) -> None:
    """全链路实测：嵌入式 Python + 桥接 + 系统 Edge + 站点搜索。"""
    print(f"\n搜索实测：「{keyword}」")
    try:
        from linovelib.fetcher import Fetcher
        from linovelib.render import RenderFetcher
        from linovelib.resolver import is_exact_match, search_hits
    except Exception as exc:  # noqa: BLE001
        report.check("导入搜索模块", False, f"{type(exc).__name__}: {exc}")
        return

    fetcher = Fetcher()
    browser = None
    try:
        browser = RenderFetcher(headless=True)
        hits = search_hits(keyword, fetcher, browser=browser)
    except Exception as exc:  # noqa: BLE001
        report.check("搜索", False, f"{type(exc).__name__}: {exc}")
        return
    finally:
        if browser is not None:
            try:
                browser.close()
            except Exception:  # noqa: BLE001 - 关闭失败不该掩盖真正的检查结果
                pass

    report.check("返回候选", bool(hits), f"{len(hits)} 条")
    if hits:
        top = hits[0]
        report.check("首条标题吻合", is_exact_match(keyword, top.title),
                     f"{top.id} | {top.title}")
        print(f"       全部候选：{[(h.id, h.title) for h in hits[:5]]}")


def main(argv: list[str] | None = None) -> int:
    _setup_stdout()
    parser = argparse.ArgumentParser(
        prog="verify_dist",
        description="分发版自检：确认内置运行时、项目模块、数据目录与桥接协议均正常。",
    )
    parser.add_argument(
        "--search", metavar="书名",
        help="额外做一次联网搜索实测（需要系统 Edge，较慢）。",
    )
    parser.add_argument(
        "--search-comic", metavar="漫画名",
        help="额外做一次漫画搜索实测，覆盖「唯一命中直达详情页」路径（需要系统 Edge）。",
    )
    args = parser.parse_args(argv)

    print("=" * 62)
    print(f"分发版自检　根目录：{ROOT}")
    print("=" * 62)

    report = Report()
    check_interpreter(report)
    check_modules(report)
    check_app_launch(report)
    check_no_build_intermediates(report)
    check_paths(report)
    check_event_protocol(report)
    check_browser(report)
    if args.search:
        check_search(report, args.search)
    if args.search_comic:
        check_comic_search(report, args.search_comic)

    print("\n" + "=" * 62)
    if report.failures:
        print(f"自检未通过：{len(report.failures)}/{report.count} 项失败")
        for name in report.failures:
            print(f"  - {name}")
        return 1
    print(f"自检通过：{report.count}/{report.count} 项全部正常")
    if not args.search:
        print("提示：加 --search 书名 可再做一次联网搜索全链路实测。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
