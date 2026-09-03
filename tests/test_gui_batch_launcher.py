from pathlib import Path


def test_gui_batch_launcher_uses_only_portable_paths_and_preserves_exit_code():
    launcher = Path(__file__).parents[1] / "start_gui.bat"

    script = launcher.read_text(encoding="utf-8")

    assert 'cd /d "%~dp0"' in script
    assert 'if exist ".venv\\Scripts\\python.exe"' in script
    assert '"%PYTHON_EXE%" desktop_gui.py' in script
    assert 'exit /b %EXIT_CODE%' in script
    assert not any(f"{drive}:\\" in script for drive in "ABCDEFGHIJKLMNOPQRSTUVWXYZ")
