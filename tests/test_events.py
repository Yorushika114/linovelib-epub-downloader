import importlib.util
import importlib

from linovelib.models import Chapter, Novel, Volume


def test_events_module_is_available():
    assert importlib.util.find_spec("linovelib.events") is not None


def test_emit_delivers_event_to_observer():
    events_module = importlib.import_module("linovelib.events")
    events = []
    event = events_module.DownloadEvent(
        "chapter_finished", chapter_id="42", completed=2, total=3)

    events_module.emit(events.append, event)

    assert events == [event]


def test_emit_allows_absent_observer():
    events_module = importlib.import_module("linovelib.events")
    events_module.emit(None, events_module.DownloadEvent("finished"))


def test_main_reports_completed_chapter_then_stops_at_next_boundary(monkeypatch, tmp_path):
    app = importlib.import_module("main")
    events = []
    chapters = [
        Chapter(id="1", url="https://example.test/1", title="第一章"),
        Chapter(id="2", url="https://example.test/2", title="第二章"),
    ]
    novel = Novel(id="99", title="测试书", author="测试作者")
    volume = Volume(title="测试书 1", chapters=chapters)

    class FakeFetcher:
        def __init__(self, **kwargs):
            pass

        def get_html(self, url):
            return "<html/>"

    class CancelBeforeSecondChapter:
        checks = 0

        def is_set(self):
            self.checks += 1
            return self.checks > 1

    monkeypatch.setattr(app, "Fetcher", FakeFetcher)
    monkeypatch.setattr(app, "resolve_id", lambda identifier, fetcher: "99")
    monkeypatch.setattr(app, "fetch_novel", lambda nid, fetcher: novel)
    monkeypatch.setattr(app, "parse_catalog", lambda html, nid: [volume])
    monkeypatch.setattr(app, "download_chapter", lambda *args: None)
    monkeypatch.setattr(app, "CACHE_DIR", tmp_path / "cache")

    result = app.main(["--novel", "99", "--volumes", "1"],
                      observer=events.append,
                      cancel_event=CancelBeforeSecondChapter())

    assert result == 130
    assert [event.kind for event in events] == [
        "download_started", "chapter_pending", "chapter_pending",
        "chapter_started", "chapter_finished", "cancelled",
    ]
    assert events[4].completed == 1
    assert events[4].total == 2


def test_main_reports_written_epub_path(monkeypatch, tmp_path):
    app = importlib.import_module("main")
    events = []
    novel = Novel(id="99", title="测试书", author="测试作者")
    volume = Volume(title="测试书 1", chapters=[
        Chapter(id="1", url="https://example.test/1", title="第一章"),
    ])

    class FakeFetcher:
        def __init__(self, **kwargs):
            pass

        def get_html(self, url):
            return "<html/>"

    output = tmp_path / "book.epub"
    monkeypatch.setattr(app, "Fetcher", FakeFetcher)
    monkeypatch.setattr(app, "resolve_id", lambda identifier, fetcher: "99")
    monkeypatch.setattr(app, "fetch_novel", lambda nid, fetcher: novel)
    monkeypatch.setattr(app, "parse_catalog", lambda html, nid: [volume])
    monkeypatch.setattr(app, "download_chapter", lambda *args: None)
    monkeypatch.setattr(app, "build_epub", lambda sub, out, cover: out)
    monkeypatch.setattr(app, "CACHE_DIR", tmp_path / "cache")

    result = app.main(["--novel", "99", "--volumes", "1", "--out", str(output)],
                      observer=events.append)

    assert result == 0
    assert any(event.kind == "epub_written" and event.output_path == str(output)
               for event in events)
