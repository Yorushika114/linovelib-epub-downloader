import io
import json
import threading
from pathlib import Path

from linovelib.events import DownloadEvent
from wpf_bridge import event_to_json, read_cancel_commands


EVENT_PREFIX = "@@LINOVELIB_EVENT@@"


ROOT = Path(__file__).parents[1]
BRIDGE = (ROOT / "wpf" / "LinovelibDesktop" / "Services" / "DownloaderBridge.cs").read_text(encoding="utf-8")


def test_event_json_is_one_line_and_preserves_chinese_text():
    line = event_to_json(DownloadEvent(
        "chapter_pending", volume_title="第一卷", chapter_id="42", chapter_title="序章"
    ))
    assert line.startswith(EVENT_PREFIX)
    payload = json.loads(line.removeprefix(EVENT_PREFIX))

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


def test_wpf_never_displays_raw_download_event_json_as_a_log_line():
    """协议事件即使从错误流抵达，也必须优先用于更新界面而非污染日志。"""
    assert "RouteBridgeLine" in BRIDGE
    assert "ReadErrorsAsync(_process, onEvent, onLog)" in BRIDGE
    assert '"下载进度事件格式异常，已忽略。"' in BRIDGE
    assert "catch (JsonException) { onLog(line); }" not in BRIDGE


def test_wpf_routes_only_explicitly_prefixed_event_lines():
    """普通日志即使以 JSON 对象开头，也不能触发下载事件反序列化。"""
    assert 'private const string EventPrefix = "@@LINOVELIB_EVENT@@";' in BRIDGE
    route_body = BRIDGE.split("private static void RouteBridgeLine", 1)[1]
    assert "StartsWith(EventPrefix, StringComparison.Ordinal)" in route_body
    assert 'StartsWith("{", StringComparison.Ordinal)' not in route_body
