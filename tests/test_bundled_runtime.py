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
