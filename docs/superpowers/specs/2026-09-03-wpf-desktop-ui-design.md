# WPF desktop UI design

## Goal

Replace the current visually dense Tkinter window with a high-DPI WPF desktop UI inspired by the supplied light, three-zone reference layout, while retaining the existing Python downloader as the only download implementation.

## Decision

Use a .NET 8 WPF application instead of further styling Tkinter. The observed Tk scaling is `1.332`, and Tkinter's bitmap-oriented control rendering cannot reliably match the reference's crisp high-DPI typography. The existing image is also functionally hidden because an opaque panel covers the Canvas; it will not be carried into the new design.

## Visual design

- A 56px pale navigation rail with simple monochrome glyph buttons; the download view is the only active item in this release.
- A 260px white task sidebar with an application title, a `下载任务` section, and a selected `新建下载` item in a soft gray rounded card.
- A white main work area with 28px Segoe UI/Microsoft YaHei UI title text, muted explanatory copy, a thin separator, and a compact card for the four download inputs.
- A green primary action, neutral cancel action, clear progress track, chapter-status table, and a high-contrast log panel. Text never appears on a photograph or translucent decorative layer.
- System DPI awareness is declared through WPF's `PerMonitorV2` process setting; UI dimensions use device-independent WPF units.

## Architecture

- Create `wpf/LinovelibDesktop`, a .NET 8 WPF application with no NuGet dependencies.
- Keep `main.py` and downloader modules responsible for crawling, event production, EPUB output, and safe cancellation.
- Add `wpf_bridge.py`, a Python command-line adapter. It invokes `main.main(..., observer=...)`, serializes each `DownloadEvent` as one UTF-8 JSON line on stdout, and listens for the single `cancel` command on stdin to set the existing cancellation event.
- The WPF process starts the bridge using the project-local `.venv\\Scripts\\python.exe` when available, otherwise `python` on PATH. It maps events to bound rows, progress, status, output path, and log messages; cancel sends `cancel` rather than killing the Python process.
- Update `start_gui.bat` to launch the published WPF executable when present and otherwise run the WPF project with `dotnet run`, while retaining an explicit error for missing .NET 8 SDK/runtime.

## Error handling

- The bridge reports uncaught errors as a final JSON `worker_failed` event and exits nonzero.
- Invalid numeric delay or an empty novel ID is rejected by the WPF UI before a process is started.
- Startup errors (no Python, no WPF build/runtime) appear in the log/status area and are also written to the launcher terminal.
- Standard error from the Python bridge is collected as diagnostic log entries; no error is silently discarded.

## Validation

- Python unit tests cover JSON event serialization and stdin cancellation forwarding without network access.
- WPF unit-level tests cover argument construction and event-to-view-model state mapping without a visible window.
- `dotnet build` compiles the WPF project.
- A Windows launch smoke test verifies the WPF app opens, its typography is sharp under the current display scale, background-free content is readable, and a safe cancel command reaches the bridge.
- Existing CLI behavior, `download.bat`, and the unrelated untracked files remain unchanged.
