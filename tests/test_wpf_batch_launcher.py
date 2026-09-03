from pathlib import Path


def test_wpf_batch_launcher_builds_and_starts_wpf_without_gui_launcher():
    launcher = Path(__file__).parents[1] / "start_wpf_ui.bat"

    script = launcher.read_text(encoding="utf-8")

    assert 'cd /d "%~dp0"' in script
    assert 'where dotnet >nul 2>nul' in script
    assert 'dotnet build "wpf\\LinovelibDesktop\\LinovelibDesktop.csproj" --nologo --verbosity:minimal' in script
    assert 'start "" "wpf\\LinovelibDesktop\\bin\\Debug\\net8.0-windows\\LinovelibDesktop.exe"' in script
    assert 'start_gui.bat' not in script
    assert not any(f"{drive}:\\" in script for drive in "ABCDEFGHIJKLMNOPQRSTUVWXYZ")
