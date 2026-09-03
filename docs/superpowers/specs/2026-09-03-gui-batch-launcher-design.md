# GUI batch launcher design

## Goal

Add a separate Windows batch launcher for the existing Tkinter desktop interface without changing the current command-line launcher.

## Scope

- Create `start_gui.bat` in the project root.
- Preserve `download.bat` unchanged; it remains the command-line launcher.
- Resolve the project location from the batch file's own directory, so the launcher contains no absolute paths and works after the project folder is moved.
- Prefer `.venv\Scripts\python.exe` when it exists; otherwise use `python` from `PATH`.
- Start `desktop_gui.py` and leave the terminal open only when Python is unavailable or the GUI process exits with an error.

## Behavior

1. `start_gui.bat` changes to `%~dp0`, the directory containing the batch file.
2. It chooses `.venv\Scripts\python.exe` if present; otherwise it verifies that `python` is available on `PATH`.
3. It runs `desktop_gui.py` through the chosen interpreter, preserving its exit code.
4. A missing interpreter or nonzero exit code prints a concise error and pauses; a successful GUI exit closes the command window without an unnecessary pause.

## Validation

- Statically verify that the script contains no drive-qualified or hard-coded project path.
- Invoke the batch file with a deliberately unavailable interpreter in an isolated command environment, and verify its error branch returns nonzero without attempting the GUI.
- Review that `download.bat` remains unchanged.
