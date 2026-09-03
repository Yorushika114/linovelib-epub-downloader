# Windows GUI Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a responsive Windows GUI that runs the existing downloader and exposes per-chapter progress and safe cancellation.

**Architecture:** A small event model decouples `main.py` from presentation. Existing orchestration publishes optional structured events and checks an optional cancellation event between chapters. A Tkinter application runs orchestration on a worker thread, transfers events through a queue, and updates testable UI state before rendering it.

**Tech Stack:** Python 3.10+, Tkinter/ttk, Pillow, standard-library `threading` and `queue`, pytest.

## Global Constraints

- Preserve CLI and `launcher.py` behaviour when no event observer or cancellation signal is provided.
- Use only offline automated tests; tests must not make network requests or create a visible Tk window.
- Package a generated original anime-style reader background under `assets/`, never a copied character image.
- Stop only between chapters and do not forcibly terminate a request or overwrite an existing EPUB.

---

### Task 1: Structured download events and cancellation

**Files:**
- Create: `linovelib/events.py`
- Modify: `main.py:131-330`
- Test: `tests/test_events.py`

**Interfaces:**
- Produces `DownloadEvent(kind: str, volume_index: int | None = None, volume_title: str = "", chapter_id: str = "", chapter_title: str = "", completed: int = 0, total: int = 0, message: str = "", output_path: str = "")`.
- Produces `emit(observer, event)` which ignores a missing observer.
- Extends `main(argv=None, *, observer=None, cancel_event=None) -> int`.

- [ ] **Step 1: Write the failing test**

```python
from linovelib.events import DownloadEvent, emit

def test_emit_delivers_event_to_observer():
    events = []
    event = DownloadEvent("chapter_finished", chapter_id="42", completed=2, total=3)
    emit(events.append, event)
    assert events == [event]

def test_emit_allows_absent_observer():
    emit(None, DownloadEvent("finished"))
```

- [ ] **Step 2: Run it to verify it fails**

Run: `python -m pytest tests/test_events.py -q`

Expected: FAIL because `linovelib.events` does not exist.

- [ ] **Step 3: Implement the minimal event model and orchestration hooks**

```python
@dataclass(frozen=True)
class DownloadEvent:
    kind: str
    volume_index: int | None = None
    volume_title: str = ""
    chapter_id: str = ""
    chapter_title: str = ""
    completed: int = 0
    total: int = 0
    message: str = ""
    output_path: str = ""
```

Publish `download_started`, `chapter_pending`, `chapter_started`, `chapter_finished`, `chapter_failed`, `epub_written`, `cancelled`, and `finished` events in `main()`. Check `cancel_event.is_set()` immediately before each chapter; emit `cancelled` and return `130` before building an incomplete current-volume EPUB.

- [ ] **Step 4: Run focused tests**

Run: `python -m pytest tests/test_events.py tests/test_cli.py -q`

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add linovelib/events.py main.py tests/test_events.py
git commit -m "feat: expose downloader progress events"
```

### Task 2: Testable GUI event state

**Files:**
- Create: `linovelib/gui_state.py`
- Test: `tests/test_gui_state.py`

**Interfaces:**
- Consumes `DownloadEvent` from Task 1.
- Produces `GuiDownloadState.apply(event: DownloadEvent) -> None`, `rows`, `completed`, `total`, `status_text`, and `finished`.

- [ ] **Step 1: Write failing state tests**

```python
def test_state_tracks_pending_started_finished_and_failure():
    state = GuiDownloadState()
    state.apply(DownloadEvent("download_started", total=2))
    state.apply(DownloadEvent("chapter_pending", chapter_id="1", chapter_title="第一章"))
    state.apply(DownloadEvent("chapter_started", chapter_id="1"))
    state.apply(DownloadEvent("chapter_finished", chapter_id="1", completed=1, total=2))
    state.apply(DownloadEvent("chapter_failed", chapter_id="2", message="timeout"))
    assert state.rows["1"].status == "已完成"
    assert state.rows["2"].status == "失败"
    assert (state.completed, state.total) == (1, 2)
```

- [ ] **Step 2: Run it to verify it fails**

Run: `python -m pytest tests/test_gui_state.py -q`

Expected: FAIL because `GuiDownloadState` does not exist.

- [ ] **Step 3: Implement only presentation-independent state translation**

Store ordered `ChapterRow` data by chapter ID, map each event kind to the Chinese display status, and retain the most recent status/log text. Do not import Tkinter in this module.

- [ ] **Step 4: Run focused test**

Run: `python -m pytest tests/test_gui_state.py -q`

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add linovelib/gui_state.py tests/test_gui_state.py
git commit -m "feat: add GUI download state model"
```

### Task 3: Tkinter desktop application and artwork

**Files:**
- Create: `linovelib/gui.py`
- Create: `desktop_gui.py`
- Create: `assets/lightnovel-reader-background.png`
- Modify: `README.md`
- Test: `tests/test_gui.py`

**Interfaces:**
- Consumes `main.main`, `GuiDownloadState`, and `DownloadEvent`.
- Produces `DownloadApp` with `start_download()`, `cancel_download()`, and queue-driven `_drain_events()`.
- Produces `python desktop_gui.py` Windows entry point.

- [ ] **Step 1: Write failing GUI wiring tests**

```python
def test_build_download_argv_uses_user_inputs():
    assert build_download_argv("3095", "1-3", "2", "output/book.epub") == [
        "--novel", "3095", "--volumes", "1-3", "--delay", "2", "--out", "output/book.epub"
    ]

def test_build_download_argv_uses_all_short_flag():
    assert "--vol" in build_download_argv("3095", "all", "", "")
```

- [ ] **Step 2: Run it to verify it fails**

Run: `python -m pytest tests/test_gui.py -q`

Expected: FAIL because `linovelib.gui` does not exist.

- [ ] **Step 3: Implement the application**

Create a dark translucent ttk layout over a Pillow-loaded original artwork. Add fields, Start/Cancel buttons, a determinate progress bar, current-status text, a chapter `Treeview`, a scrolling event log, and output-path display. Start `main()` in a daemon worker thread; send observer events into `queue.Queue`; poll the queue using `root.after(80, ...)`; update widgets only on the main thread.

- [ ] **Step 4: Add usage documentation and focused tests**

Document `python desktop_gui.py` in the README, including Python's optional Tcl/Tk component. Test argument construction without constructing `Tk()`.

- [ ] **Step 5: Run focused tests and a no-window import smoke check**

Run: `python -m pytest tests/test_gui.py tests/test_gui_state.py -q`

Expected: PASS.

Run: `python -c "from linovelib.gui import build_download_argv; print(build_download_argv('3095','1','0.4',''))"`

Expected: prints a valid CLI argument list.

- [ ] **Step 6: Commit**

```bash
git add linovelib/gui.py linovelib/gui_state.py desktop_gui.py assets README.md tests/test_gui.py
git commit -m "feat: add Windows downloader GUI"
```

### Task 4: Full regression and visual verification

**Files:**
- Modify only if a test or visual defect is found.

- [ ] **Step 1: Run the full offline suite**

Run: `python -m pytest -q`

Expected: PASS with no network access.

- [ ] **Step 2: Launch the GUI manually**

Run: `python desktop_gui.py`

Expected: a responsive Windows window with readable text above the background; enter a novel ID only after confirming controls render correctly.

- [ ] **Step 3: Verify visual requirements**

Confirm the background does not obscure form text, the progress bar is visible at 0%, the table can display each requested status, and Cancel changes the status without freezing the window.

- [ ] **Step 4: Commit any correction**

```bash
git add main.py linovelib/gui.py linovelib/gui_state.py README.md tests/test_gui.py tests/test_gui_state.py
git commit -m "fix: polish Windows GUI"
```
