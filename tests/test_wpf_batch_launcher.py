from pathlib import Path


def test_wpf_batch_launcher_delegates_to_the_verified_wpf_startup_script():
    launcher = Path(__file__).parents[1] / "start_wpf_ui.bat"

    script = launcher.read_text(encoding="utf-8")

    assert 'cd /d "%~dp0"' in script
    assert 'call start_gui.bat' in script
    assert 'exit /b %ERRORLEVEL%' in script
    assert not any(f"{drive}:\\" in script for drive in "ABCDEFGHIJKLMNOPQRSTUVWXYZ")
