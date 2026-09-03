# WPF Desktop UI Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the Tkinter launch path with a crisp .NET 8 WPF downloader UI while retaining Python as the download engine.

**Architecture:** A Python bridge translates existing `DownloadEvent` instances to line-delimited JSON and accepts a stdin cancel signal. A WPF process service starts that bridge and maps events onto a three-column, high-DPI view. `start_gui.bat` starts the WPF application from relative project paths.

**Tech Stack:** .NET 8 WPF, C# 12, Python 3.10+, pytest, standard-library JSON/subprocess/threading.

## Global Constraints

- Do not modify `main.py`, `download.bat`, download modules, or unrelated untracked files.
- Do not use absolute project paths in runtime code or batch files.
- Preserve safe cancellation at the Python chapter boundary.
- Use no NuGet packages and no background artwork in the new main layout.
- The WPF UI must remain readable at Windows high-DPI scales.

---

### Task 1: Add the Python JSON event bridge

**Files:**
- Create: `wpf_bridge.py`
- Create: `tests/test_wpf_bridge.py`

**Interfaces:**
- Consumes: normal downloader CLI options and one stdin command, `cancel`.
- Produces: one UTF-8 JSON object per `DownloadEvent` on stdout; downloader console output on stderr; process exit code from `main.main`.

- [x] **Step 1: Write the failing bridge serialization and cancellation tests**

```python
def test_event_json_is_one_line_and_preserves_chinese_text():
    assert json.loads(event_to_json(DownloadEvent("finished", message="下载结束。")))["message"] == "下载结束。"

def test_cancel_reader_sets_event_when_cancel_line_arrives():
    cancelled = threading.Event()
    read_cancel_commands(io.StringIO("cancel\\n"), cancelled)
    assert cancelled.is_set()
```

- [x] **Step 2: Run `python -m pytest tests/test_wpf_bridge.py -q`; expect an import failure because `wpf_bridge` does not exist.**

- [x] **Step 3: Implement `event_to_json`, `read_cancel_commands`, and `run` in `wpf_bridge.py`.**

```python
def run(argv: list[str] | None = None) -> int:
    cancel_event = threading.Event()
    threading.Thread(target=read_cancel_commands, args=(sys.stdin, cancel_event), daemon=True).start()
    with contextlib.redirect_stdout(sys.stderr):
        return main.main(argv, observer=emit_json_event, cancel_event=cancel_event)
```

- [x] **Step 4: Re-run the focused Python tests; expect all pass.**

### Task 2: Create the dependency-free WPF project and process bridge

**Files:**
- Create: `wpf/LinovelibDesktop/LinovelibDesktop.csproj`
- Create: `wpf/LinovelibDesktop/App.xaml`
- Create: `wpf/LinovelibDesktop/App.xaml.cs`
- Create: `wpf/LinovelibDesktop/Models/DownloadEventDto.cs`
- Create: `wpf/LinovelibDesktop/Services/ProjectPaths.cs`
- Create: `wpf/LinovelibDesktop/Services/DownloaderBridge.cs`

**Interfaces:**
- `ProjectPaths.FindRoot()` returns the nearest ancestor containing `main.py` and `wpf_bridge.py`.
- `DownloaderBridge.StartAsync(DownloadRequest, Action<DownloadEventDto>, Action<string>, CancellationToken)` streams bridge records and returns the downloader exit code.
- `DownloaderBridge.RequestCancel()` writes `cancel` to the bridge's standard input.

- [x] **Step 1: Create the SDK-style WPF project targeting `net8.0-windows`, with `UseWPF`, nullable references, and per-monitor DPI awareness.**

- [x] **Step 2: Implement project-root and Python interpreter discovery using relative filesystem traversal; no literal drive path may occur.**

- [x] **Step 3: Implement JSON-line stdout parsing, stderr forwarding, and `cancel` stdin forwarding in `DownloaderBridge`.**

- [x] **Step 4: Run `dotnet build wpf/LinovelibDesktop/LinovelibDesktop.csproj`; expect zero errors.**

### Task 3: Build the high-DPI reference-inspired WPF interface

**Files:**
- Create: `wpf/LinovelibDesktop/MainWindow.xaml`
- Create: `wpf/LinovelibDesktop/MainWindow.xaml.cs`

**Interfaces:**
- Inputs: novel id, volumes, delay, optional output EPUB path.
- Outputs: disabled/enabled start/cancel controls, progress value/text, chapter `ObservableCollection`, and append-only log text.

- [x] **Step 1: Define XAML resources for the pale navigation rail, white sidebar, green selection/action color, rounded cards, and Segoe UI/Microsoft YaHei UI typography.**

- [x] **Step 2: Compose the 56px navigation rail, 260px task sidebar, and white main work area; omit the old full-window background image.**

- [x] **Step 3: Add form validation, folder picker, progress, chapter DataGrid, and a dark but high-contrast log panel.**

- [x] **Step 4: Wire `DownloaderBridge` events to the Dispatcher, update rows/progress/output/log, and send safe cancellation on the cancel action.**

- [x] **Step 5: Build the WPF project again; expect zero errors.**

### Task 4: Route the GUI launcher to WPF and verify the artifact

**Files:**
- Modify: `start_gui.bat`
- Modify: `tests/test_gui_batch_launcher.py`

**Interfaces:**
- `start_gui.bat` starts `dotnet run --project wpf\\LinovelibDesktop\\LinovelibDesktop.csproj` from `%~dp0`.

- [x] **Step 1: Update the launcher test to require the WPF project command and continue prohibit drive-qualified paths.**

- [x] **Step 2: Run the test and confirm it fails against the old Python launcher.**

- [x] **Step 3: Replace the old Python entry point in `start_gui.bat` with a `dotnet` availability check and the relative WPF project invocation.**

- [x] **Step 4: Run the focused Python tests, launcher test, `dotnet build`, and `git diff --check`; expect all targeted checks to pass.**

- [x] **Step 5: Commit the implementation and plan with `feat: replace Tkinter launcher with WPF UI`.**
