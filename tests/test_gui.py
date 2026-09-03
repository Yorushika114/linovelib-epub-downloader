import importlib.util
import importlib
import queue
import tkinter as tk

from linovelib.events import DownloadEvent


def test_gui_module_is_available():
    assert importlib.util.find_spec("linovelib.gui") is not None


def test_build_download_argv_uses_user_inputs():
    gui = importlib.import_module("linovelib.gui")
    assert gui.build_download_argv("3095", "1-3", "2", "output/book.epub") == [
        "--novel", "3095", "--volumes", "1-3", "--delay", "2",
        "--out", "output/book.epub",
    ]


def test_build_download_argv_uses_all_short_flag():
    gui = importlib.import_module("linovelib.gui")
    assert gui.build_download_argv("3095", "all", "", "") == [
        "--novel", "3095", "--vol", "all",
    ]


def test_run_download_forwards_events_and_completion():
    gui = importlib.import_module("linovelib.gui")
    received = queue.Queue()
    observed = []

    def fake_main(argv, *, observer, cancel_event):
        observed.extend(argv)
        observer(DownloadEvent("chapter_finished", chapter_id="1", completed=1, total=1))
        return 0

    result = gui.run_download(fake_main, ["--novel", "3095"], received, object())

    assert result == 0
    assert observed == ["--novel", "3095"]
    assert received.get_nowait().kind == "chapter_finished"
    assert received.get_nowait().kind == "worker_finished"


def test_run_download_reports_unhandled_worker_error():
    gui = importlib.import_module("linovelib.gui")
    received = queue.Queue()

    def failing_main(argv, *, observer, cancel_event):
        raise RuntimeError("network unavailable")

    result = gui.run_download(failing_main, ["--novel", "3095"], received, object())

    assert result == 1
    assert received.get_nowait().kind == "worker_failed"
    assert received.get_nowait().kind == "worker_finished"


def test_download_app_keeps_default_volume_value_once():
    gui = importlib.import_module("linovelib.gui")
    root = tk.Tk()
    root.withdraw()
    try:
        app = gui.DownloadApp(root)
        assert app.volumes.get() == "all"
    finally:
        root.destroy()
