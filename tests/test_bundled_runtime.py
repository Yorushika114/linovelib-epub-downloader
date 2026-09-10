"""分发版内置 Python 运行时的接线约定。

分发版把嵌入式 Python 放在 runtime/python/，让目标机器免装 Python 即可运行。
本测试锁定「解释器解析顺序」与「运行时不入库」两条约定，防止：
- FindPython 丢掉 runtime 分支 → 分发版回落到系统 python 而失败；
- .gitignore 漏掉 runtime/ → 174MB 运行时被误提交。
"""

from pathlib import Path

ROOT = Path(__file__).parents[1]
PROJECT_PATHS = ROOT / "wpf" / "LinovelibDesktop" / "Services" / "ProjectPaths.cs"


def test_findpython_prefers_bundled_runtime():
    """FindPython 必须先探测 runtime/python，再回落 .venv / 系统 python。"""
    source = PROJECT_PATHS.read_text(encoding="utf-8")

    # 内置运行时路径：root/runtime/python/python.exe
    assert '"runtime"' in source
    assert '"python"' in source

    # 只比较实际的 Path.Combine 调用，避免命中文档注释里提到的 .venv 字样。
    bundled_at = source.index('Path.Combine(root, "runtime", "python", "python.exe")')
    venv_at = source.index('Path.Combine(root, ".venv"')
    # runtime 分支必须先于 .venv 分支出现，否则分发版会去用不存在的 venv。
    assert bundled_at < venv_at


def test_findpython_keeps_fallback_chain():
    """回退链完整：runtime → .venv → 系统 python，保证开发机行为不变。"""
    source = PROJECT_PATHS.read_text(encoding="utf-8")
    assert '.venv' in source
    assert '"python"' in source


def test_runtime_and_publish_are_gitignored():
    """构建产物不进版本库（174MB 运行时 + 162MB 自包含发布）。"""
    gitignore = (ROOT / ".gitignore").read_text(encoding="utf-8")
    assert "runtime/" in gitignore
    assert "dist/" in gitignore
    assert "publish/" in gitignore


def test_build_script_has_utf8_bom():
    """构建脚本必须带 UTF-8 BOM。

    Windows PowerShell 5.1 对无 BOM 的 .ps1 按系统 ANSI 代码页解码，脚本内的
    中文注释与提示会乱码，进而引发语法错误、脚本直接跑不起来。这个坑踩过一次
    （UnicodeEncodeError 式的解析失败），故用测试钉死。
    """
    raw = (ROOT / "tools" / "build_dist.ps1").read_bytes()
    assert raw.startswith(b"\xef\xbb\xbf"), (
        "tools/build_dist.ps1 缺少 UTF-8 BOM，Windows PowerShell 5.1 会按 ANSI "
        "解码导致中文乱码+语法错误。"
    )


def test_build_script_preserves_user_data_dirs():
    """重建时必须保留 download/ 与 _tmp_dl/。

    dist 里是用户实际下载的成品；清理旧发布产物时若把这两个目录一并删掉就是毁
    数据。同理 runtime/ 重建代价高（要重下嵌入式包并装全部依赖），也应保留复用。
    """
    source = (ROOT / "tools" / "build_dist.ps1").read_text(encoding="utf-8")
    keep = source.index("$KeepNames")
    keep_line = source[keep:source.index("\n", keep)]
    for name in ("runtime", "download", "_tmp_dl"):
        assert f"'{name}'" in keep_line, f"清理逻辑的保留名单缺少 {name}/"


def test_dist_bat_targets_bundled_runtime():
    """分发版 download.bat 必须指向内置运行时，而非裸 `python`。

    仓库根的 download.bat 用裸 python（源码开发者的机器上有），直接拷进分发版会
    在无 Python 的目标机器上失败——正是本方案要解决的问题。
    """
    source = (ROOT / "tools" / "build_dist.ps1").read_text(encoding="utf-8")
    # 生成的 bat 内容里要有内置解释器的相对路径。
    assert "%~dp0runtime\\python\\python.exe" in source


def test_pth_includes_project_root():
    """._pth 必须包含项目根（..\\..）。

    这是最隐蔽的一个坑：embeddable 的 ._pth 一旦存在，Python 就不再自动把脚本
    所在目录加进 sys.path。WPF 以 `python <root>\\wpf_bridge.py` 启动桥接，而
    wpf_bridge.py 要 `import main`——漏了这一行，依赖导入测试照样过，但应用一启动
    就 ModuleNotFoundError: No module named 'main'。

    相对路径的基准是 runtime\\python\\，故项目根是上两级（..\\..）。
    """
    source = (ROOT / "tools" / "build_dist.ps1").read_text(encoding="utf-8")
    pth_start = source.index("python310._pth")
    # 取 ._pth 写入语句的邻域，确认配置里含项目根与 import site。
    block = source[pth_start : pth_start + 900]
    assert "'..\\..'" in block or '"..\\.."' in block, (
        "._pth 缺少项目根 ..\\.. —— 桥接将无法 import main。"
    )
    assert "import site" in block


def test_build_script_runs_real_bridge_check():
    """构建脚本必须按应用真实方式验证桥接能启动。

    只验证 `import bs4, lxml, ...` 会漏掉 sys.path 类问题（依赖导入成功 ≠ 应用能
    启动）。故构建脚本调用 tools/verify_dist.py，由后者真正 import 桥接与 main。

    检查逻辑刻意放在 .py 而非内联 PowerShell：PS 5.1 会把原生命令的 stderr 包成
    NativeCommandError 而中止脚本（argparse 的 --help 正走 stderr），且子进程输出
    为 GBK，内联 `-match '中文'` 必然误判。这个坑踩过一次。
    """
    source = (ROOT / "tools" / "build_dist.ps1").read_text(encoding="utf-8")
    assert "verify_dist.py" in source, (
        "构建脚本未调用 tools/verify_dist.py —— 出厂前不会按真实方式验证桥接。"
    )


def test_verify_dist_script_exists_and_checks_bridge():
    """自检脚本必须真的去 import 桥接与 main，而不只是打印几句话。"""
    script = (ROOT / "tools" / "verify_dist.py").read_text(encoding="utf-8")
    for needle in ("import wpf_bridge", "import main", "import linovelib", "import comic"):
        assert needle in script, f"自检脚本缺少 {needle} —— 漏检项正是应用起不来的原因。"


def test_verify_dist_reports_paths_without_side_effects():
    """自检必须只读。

    它会在一台「用户其实在用」的机器上被运行；若顺手 mkdir 出 download/ 等目录，
    就是自检污染用户数据。故只校验路径名字，不落盘。
    """
    script = (ROOT / "tools" / "verify_dist.py").read_text(encoding="utf-8")
    assert "mkdir" not in script, "自检脚本不应创建任何目录。"
    assert "write_text" not in script and "open(" not in script, (
        "自检脚本不应写任何文件。"
    )


def test_verify_dist_bootstraps_project_root():
    """自检要能被任意解释器从任意 cwd 调用，故须自行把项目根塞进 sys.path。"""
    script = (ROOT / "tools" / "verify_dist.py").read_text(encoding="utf-8")
    assert "sys.path.insert" in script, (
        "自检脚本未引导 sys.path —— 从分发版外部调用时会 import 失败。"
    )


def test_verify_dist_is_shipped_in_dist():
    """自检脚本必须拷进分发版，且构建时从分发包内调用。

    根目录由 `__file__` 推导：若在仓库里跑 dist 的脚本拷贝，ROOT 会变成仓库根，
    于是校验的是仓库而非分发版——全绿也是假象（实测踩过，PROJECT_ROOT 打出了仓库
    路径）。所以脚本既要随包发布，调用路径也要指向分发包内那一份。
    """
    source = (ROOT / "tools" / "build_dist.ps1").read_text(encoding="utf-8")
    assert "verify_dist.py') $VerifyDst" in source or "verify_dist.py" in source.split(
        "端到端自检"
    )[1], "构建脚本未把 verify_dist.py 拷进分发版。"

    # 自检的调用必须是分发包内那份，不是仓库那份。取「端到端自检」之后的调用行。
    call_block = source.split("端到端自检")[1]
    assert "$OutDir 'tools\\verify_dist.py'" in call_block, (
        "构建脚本调用了仓库里的自检脚本而非分发包内的——会校验错目录还报成功。"
    )


def test_build_strips_root_pycache():
    """分发版根目录不得残留 __pycache__。

    构建脚本原先只在拷贝 linovelib/comic 时清理字节码，根目录那份（上次在 dist
    内跑 Python 留下的）会被 Copy-Item -Force 带着一起留在包里。它含开发机路径，
    且可能被优先加载，掩盖真正的源码问题。实测构建产物里确实存在该目录。
    """
    source = (ROOT / "tools" / "build_dist.ps1").read_text(encoding="utf-8")
    assert "__pycache__" in source
    # 清理必须覆盖分发版根目录，而不只是子包目录。
    assert "$OutDir\\__pycache__" in source, (
        "构建脚本未清理分发版根目录的 __pycache__。"
    )


def test_verify_dist_flags_missing_runtime():
    """自检必须能识别「这里不是分发版」。

    这是场景 A/B 的判据：仓库根没有 runtime/，而脚本按 __file__ 定位根目录。
    少了这一项，从仓库里跑就会静默地校验仓库并通过。
    """
    script = (ROOT / "tools" / "verify_dist.py").read_text(encoding="utf-8")
    assert "内置运行时存在" in script, (
        "自检未检查 runtime/python/python.exe 是否存在 —— 无法识别假通过场景。"
    )
    assert "not bundled.exists()" in script
