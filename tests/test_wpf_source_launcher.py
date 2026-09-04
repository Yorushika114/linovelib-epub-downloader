from pathlib import Path


ROOT = Path(__file__).parents[1]
LAUNCHER_DIR = ROOT / "wpf" / "LinovelibSourceLauncher"


def test_source_launcher_replaces_batch_file_and_builds_the_project_ui():
    project = LAUNCHER_DIR / "LinovelibSourceLauncher.csproj"
    program = LAUNCHER_DIR / "Program.cs"

    assert project.is_file()
    assert program.is_file()
    assert not (ROOT / "start_wpf_ui.bat").exists()

    project_source = project.read_text(encoding="utf-8")
    assert "<AssemblyName>轻小说下载器</AssemblyName>" in project_source

    source = program.read_text(encoding="utf-8")
    assert '"dotnet"' in source
    assert '"LinovelibDesktop.csproj"' in source
    assert '"wpf_bridge.py"' in source
    assert '"LinovelibDesktop.exe"' in source
    assert "Process.GetProcessesByName" in source
