import io
import json
import threading

from linovelib.events import DownloadEvent
from wpf_bridge import event_to_json, read_cancel_commands


def test_event_json_is_one_line_and_preserves_chinese_text():
    payload = json.loads(event_to_json(DownloadEvent("finished", message="下载结束。")))

    assert payload["kind"] == "finished"
    assert payload["message"] == "下载结束。"


def test_cancel_reader_sets_event_when_cancel_line_arrives():
    cancelled = threading.Event()

    read_cancel_commands(io.StringIO("ignore\ncancel\n"), cancelled)

    assert cancelled.is_set()
