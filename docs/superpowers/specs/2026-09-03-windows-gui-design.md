# Windows GUI Design

## Goal

Provide a native Windows desktop interface for the existing Linovelib-to-EPUB downloader, without changing its command-line workflow.

## User experience

- The user enters a novel ID, volume selection (such as `1-3,5` or `all`), request delay, and an optional EPUB output path.
- Clicking **Start download** runs the download in a background thread so the window remains responsive.
- A determinate progress bar reports completed chapters out of the selected chapters. A status label names the active volume and chapter.
- A chapter table updates each row through Pending, Downloading, Completed, Failed, and Cancelled states. Generated EPUB paths and failure details remain visible in the event log.
- **Cancel download** requests a safe stop between chapters. Successfully completed output is preserved; no process is forcibly terminated.

## Architecture

- Add a small `DownloadEvent` model and optional event callback/cancellation signal to the existing download orchestration. The CLI passes neither, retaining its current printed progress and behaviour.
- Create `desktop_gui.py` as the Windows launcher and focused `linovelib/gui.py` components for a Tkinter application, worker-thread-to-main-thread event queue, and state rendering.
- Use the existing `main.main()` pipeline indirectly through a callable download service so GUI events are structured rather than parsed from console output.
- Put a bundled, original anime-style light-novel-reader background asset under `assets/`; draw it with a translucent content panel so controls and chapter states remain legible.

## Constraints

- Python 3.10+ and the current dependency set only; Tkinter is supplied with standard Windows Python.
- Keep `main.py`, `launcher.py`, and `download.bat` command-line and interactive use compatible.
- Do not make the normal download path depend on a reference EPUB or a third-party browser.
- Background artwork must be an original generated image, not a copied character or publisher image.
- Tests must be offline and must not perform novel downloads or create a visible GUI window.

## Verification

- Unit-test download event ordering, cancellation at chapter boundaries, and that command-line calls continue to work without an observer.
- Unit-test GUI event-to-row-state translation without creating a Tk root window.
- Perform a local launch smoke check and inspect the rendered window manually for background contrast, progress visibility, and chapter-state readability.
