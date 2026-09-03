import io
import json
import threading

from linovelib.events import DownloadEvent
from wpf_bridge import event_to_json, read_cancel_commands


def test_event_json_is_one_line_and_preserves_chinese_text():
    payload = json.loads(event_to_json(DownloadEvent(
        "chapter_pending", volume_title="第一卷", chapter_id="42", chapter_title="序章"
    )))

    assert payload["kind"] == "chapter_pending"
    assert payload["volumeTitle"] == "第一卷"
    assert payload["chapterId"] == "42"
    assert payload["chapterTitle"] == "序章"
    assert "volume_title" not in payload
    assert "chapter_id" not in payload


def test_cancel_reader_sets_event_when_cancel_line_arrives():
    cancelled = threading.Event()

    read_cancel_commands(io.StringIO("ignore\ncancel\n"), cancelled)

    assert cancelled.is_set()
