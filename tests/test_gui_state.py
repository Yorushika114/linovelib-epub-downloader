import importlib.util
import importlib

from linovelib.events import DownloadEvent


def test_gui_state_module_is_available():
    assert importlib.util.find_spec("linovelib.gui_state") is not None


def test_state_tracks_pending_started_finished_and_failure():
    state_module = importlib.import_module("linovelib.gui_state")
    state = state_module.GuiDownloadState()

    state.apply(DownloadEvent("download_started", total=2))
    state.apply(DownloadEvent("chapter_pending", chapter_id="1", chapter_title="第一章"))
    state.apply(DownloadEvent("chapter_pending", chapter_id="2", chapter_title="第二章"))
    state.apply(DownloadEvent("chapter_started", chapter_id="1", chapter_title="第一章"))
    state.apply(DownloadEvent("chapter_finished", chapter_id="1", completed=1, total=2))
    state.apply(DownloadEvent("chapter_failed", chapter_id="2", message="timeout"))

    assert state.rows["1"].status == "已完成"
    assert state.rows["2"].status == "失败"
    assert (state.completed, state.total) == (1, 2)
    assert state.status_text == "第二章下载失败：timeout"
