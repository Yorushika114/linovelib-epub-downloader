# GUI Batch Launcher Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Provide a portable Windows batch file that launches the Tkinter desktop downloader.

**Architecture:** `start_gui.bat` is a standalone root-level launcher. It changes into the directory containing itself, prefers the project-local virtual-environment interpreter, falls back to `python` from `PATH`, and forwards execution to `desktop_gui.py`; all directory resolution is relative to the batch file.

**Tech Stack:** Windows Command Prompt batch syntax, Python, Tkinter.

## Global Constraints

- Create `start_gui.bat` in the project root and do not modify `download.bat`.
- Do not use drive-qualified or hard-coded project paths.
- Prefer `.venv\Scripts\python.exe`; fall back to `python` from `PATH` only when that interpreter does not exist.
- Pause only after an error; preserve the Python process exit code.

---

### Task 1: Add and validate the portable GUI launcher

**Files:**
- Create: `start_gui.bat`
- Verify unchanged: `download.bat`

**Interfaces:**
- Consumes: `%~dp0`, `.venv\Scripts\python.exe`, `python` from `PATH`, and `desktop_gui.py`.
- Produces: process exit code `0` after a successful GUI exit; a nonzero code plus a visible error for missing Python or a failed GUI process.

- [x] **Step 1: Establish the unchanged CLI-launcher baseline**

Run:

```powershell
Get-FileHash -Algorithm SHA256 download.bat
```

Expected: capture the SHA-256 hash before adding the GUI launcher.

- [x] **Step 2: Create the batch launcher**

Create `start_gui.bat` with:

```bat
@echo off
setlocal
cd /d "%~dp0"

if exist ".venv\Scripts\python.exe" (
    set "PYTHON_EXE=.venv\Scripts\python.exe"
) else (
    where python >nul 2>nul
    if errorlevel 1 (
        echo [ERROR] Python was not found. Install Python or create .venv.
        pause
        exit /b 1
    )
    set "PYTHON_EXE=python"
)

"%PYTHON_EXE%" desktop_gui.py
set "EXIT_CODE=%ERRORLEVEL%"
if not "%EXIT_CODE%"=="0" (
    echo [ERROR] The GUI exited with code %EXIT_CODE%.
    pause
)
exit /b %EXIT_CODE%
```

- [x] **Step 3: Verify the relative-path contract**

Run:

```powershell
if (Select-String -Path start_gui.bat -Pattern '[A-Za-z]:\\') { exit 1 }
Get-Content -Raw start_gui.bat
```

Expected: exit code `0`; the script uses `%~dp0` and no drive-qualified path.

- [x] **Step 4: Verify the unavailable-Python error branch**

Run from a temporary sibling copy in which `desktop_gui.py` is replaced by a non-executable sentinel and `PATH` is restricted so that `where python` fails. Capture the return code with `cmd /c`; expect `1` and the `[ERROR] Python was not found` message. Do not invoke the real GUI during this check.

- [x] **Step 5: Verify existing launcher preservation**

Run:

```powershell
Get-FileHash -Algorithm SHA256 download.bat
git diff -- download.bat
```

Expected: the SHA-256 hash matches Step 1 and `git diff` produces no output.

- [x] **Step 6: Commit**

```bash
git add start_gui.bat docs/superpowers/plans/2026-09-03-gui-batch-launcher.md
git commit -m "feat: add GUI batch launcher"
```
