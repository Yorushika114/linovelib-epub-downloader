import importlib.util
import importlib
import zipfile

from linovelib.models import Chapter, Novel, Volume


def _write_fake_epub(path, payload=b"existing"):
    """写一个**结构合法**的最小 epub（实质是 zip）。

    「已经下过这一卷」在跳过闸门那里的判据是「目标是个完整 epub」，不是「路径存在」——
    半截文件（合成中途被打断留下的）必须重下，否则那一卷会被永久跳过。所以夹具不能再拿
    b"existing" 这种 9 字节占位冒充成品。
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("mimetype", "application/epub+zip")
        z.writestr("vol1_ch1.xhtml", f"<html><body><p>{payload.decode()}</p></body></html>")


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
    monkeypatch.setattr(app, "resolve_id", lambda identifier, fetcher, browser=None: "99")
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
    monkeypatch.setattr(app, "resolve_id", lambda identifier, fetcher, browser=None: "99")
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


def test_main_does_not_publish_pending_rows_for_an_existing_volume(monkeypatch, tmp_path):
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

    output_root = tmp_path / "downloads"
    existing = output_root / "测试书" / "测试书 第1卷.epub"
    _write_fake_epub(existing)
    monkeypatch.setattr(app, "Fetcher", FakeFetcher)
    monkeypatch.setattr(app, "resolve_id", lambda identifier, fetcher, browser=None: "99")
    monkeypatch.setattr(app, "fetch_novel", lambda nid, fetcher: novel)
    monkeypatch.setattr(app, "parse_catalog", lambda html, nid: [volume])
    monkeypatch.setattr(app, "NOVEL_DIR", output_root)
    monkeypatch.setattr(app, "CACHE_DIR", tmp_path / "cache")

    result = app.main(["--novel", "99", "--volumes", "1"], observer=events.append)

    assert result == 0
    assert all(event.kind != "chapter_pending" for event in events)
    assert events[0].total == 0


def test_main_retries_a_volume_whose_existing_file_is_not_a_valid_epub(monkeypatch, tmp_path):
    """半截文件不能算「下过了」。

    合成中途被关窗/崩溃打断会留下一个截断的 epub；旧闸门只看 out.exists() 就认了它，
    这一卷于是被永久跳过——用户重跑多少次都拿不到书，界面还说「已存在，跳过」。
    """
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

    output_root = tmp_path / "downloads"
    existing = output_root / "测试书" / "测试书 第1卷.epub"
    existing.parent.mkdir(parents=True)
    existing.write_bytes(b"half-written zip, no central directory")
    monkeypatch.setattr(app, "Fetcher", FakeFetcher)
    monkeypatch.setattr(app, "resolve_id", lambda identifier, fetcher, browser=None: "99")
    monkeypatch.setattr(app, "fetch_novel", lambda nid, fetcher: novel)
    monkeypatch.setattr(app, "parse_catalog", lambda html, nid: [volume])
    monkeypatch.setattr(app, "NOVEL_DIR", output_root)
    monkeypatch.setattr(app, "CACHE_DIR", tmp_path / "cache")

    app.main(["--novel", "99", "--volumes", "1"], observer=events.append)

    assert any(event.kind == "chapter_pending" for event in events)


def test_main_redownloads_when_the_out_file_is_not_a_valid_epub(monkeypatch, tmp_path):
    """--out 的「已存在就跳过」同样要看它是不是一本**完整**的 epub。

    这条闸门在最前面、不 --force 时零网络请求就 return 0。判据若还是 exists()，一个用户
    手放的同名占位文件（或上次被打断留下的半截文件）就能让整本书永远下不出来——每次运行
    都秒退并打印「已存在，跳过」，看起来还像是正常跳过。
    """
    app = importlib.import_module("main")
    novel = Novel(id="99", title="测试书", author="测试作者")
    volume = Volume(title="测试书 1", chapters=[
        Chapter(id="1", url="https://example.test/1", title="第一章"),
    ])
    output = tmp_path / "book.epub"
    output.write_bytes(b"not a zip at all")

    downloaded = []
    written = []

    class FakeFetcher:
        def __init__(self, **kwargs):
            pass

        def get_html(self, url):
            return "<html/>"

    monkeypatch.setattr(app, "Fetcher", FakeFetcher)
    monkeypatch.setattr(app, "resolve_id", lambda identifier, fetcher, browser=None: "99")
    monkeypatch.setattr(app, "fetch_novel", lambda nid, fetcher: novel)
    monkeypatch.setattr(app, "parse_catalog", lambda html, nid: [volume])
    monkeypatch.setattr(app, "download_chapter", lambda *args: downloaded.append(args))
    monkeypatch.setattr(app, "CACHE_DIR", tmp_path / "cache")
    monkeypatch.setattr(app, "build_epub", lambda sub, out, cover: written.append(out) or out)

    result = app.main(["--novel", "99", "--vol", "all", "--out", str(output)])

    assert result == 0
    assert downloaded, "半截的 --out 目标被当成「下过了」，整本被跳过。"
    assert written == [str(output)]   # --out 原样透传命令行字符串，不做 Path 规整


def _two_volume_book():
    novel = Novel(id="99", title="测试书", author="测试作者")
    vols = [
        Volume(title="测试书 1", chapters=[Chapter(id="1", url="u1", title="第一章")]),
        Volume(title="测试书 2", chapters=[Chapter(id="2", url="u2", title="第二章")]),
    ]
    return novel, vols


class _FakeFetcher:
    def __init__(self, **kwargs):
        pass

    def get_html(self, url):
        return "<html/>"


def _wire(monkeypatch, app, tmp_path, novel, vols, output_root, download, build):
    monkeypatch.setattr(app, "Fetcher", _FakeFetcher)
    monkeypatch.setattr(app, "resolve_id", lambda identifier, fetcher, browser=None: "99")
    monkeypatch.setattr(app, "fetch_novel", lambda nid, fetcher: novel)
    monkeypatch.setattr(app, "parse_catalog", lambda html, nid: vols)
    monkeypatch.setattr(app, "NOVEL_DIR", output_root)
    monkeypatch.setattr(app, "CACHE_DIR", tmp_path / "cache")
    monkeypatch.setattr(app, "download_chapter", download)
    monkeypatch.setattr(app, "build_epub", build)


def test_merge_backfills_bodies_of_volumes_whose_epub_already_existed(monkeypatch, tmp_path):
    """--merge 的合并本不能拿空正文合成。

    逐卷 EPUB 都已存在时，本次进程一次 download_chapter 都没跑过，内存里 Chapter.html
    还是空串。旧代码照样合并，产出一本每章只有标题的书，退出码还是 0——用户拿到「成功」
    却是一本空书，而且因为逐卷 epub 都在，重跑多少次都是这个结果。
    """
    app = importlib.import_module("main")
    novel, vols = _two_volume_book()
    output_root = tmp_path / "downloads"
    for name in ("测试书 第1卷.epub", "测试书 第2卷.epub"):
        _write_fake_epub(output_root / "测试书" / name)

    fetched = []

    def fake_download(ch, nid, fetcher, tmpdir):
        fetched.append(ch.id)
        ch.html = f"<p>{ch.id}</p>"

    merged = []

    def fake_build(sub, out, cover):
        merged.append((out, [ch.html for v in sub.volumes for ch in v.chapters]))
        return out

    _wire(monkeypatch, app, tmp_path, novel, vols, output_root, fake_download, fake_build)

    result = app.main(["--novel", "99", "--vol", "all", "--merge"])

    assert result == 0
    assert fetched == ["1", "2"], "被跳过的卷没补正文就被拿去合并了。"
    out, bodies = merged[-1]
    assert out.name == "测试书 第1-2卷.epub"
    assert bodies == ["<p>1</p>", "<p>2</p>"], f"合并本里有空正文：{bodies}"


def test_merge_refuses_to_write_an_empty_book_when_backfill_fails(monkeypatch, tmp_path):
    """补正文失败时宁可不出合并本，也不出一本只有标题的空书。

    与逐卷路径的区别：那里某个章节下载失败是一次真实的下载错误，成品仍大部分有用；这里
    的正文**从来没被尝试下载过**（整卷被跳过），补不齐说明网络确实有问题。逐卷 EPUB 都还
    在，返回非 0 让用户重跑即可，不该用一本空书冒充成功。
    """
    app = importlib.import_module("main")
    novel, vols = _two_volume_book()
    output_root = tmp_path / "downloads"
    for name in ("测试书 第1卷.epub", "测试书 第2卷.epub"):
        _write_fake_epub(output_root / "测试书" / name)

    def failing_download(ch, nid, fetcher, tmpdir):
        raise RuntimeError("网络断了")

    merged = []

    def fake_build(sub, out, cover):
        merged.append(out)
        return out

    _wire(monkeypatch, app, tmp_path, novel, vols, output_root, failing_download, fake_build)

    result = app.main(["--novel", "99", "--vol", "all", "--merge"])

    assert result == 1
    assert merged == [], "补正文失败却仍写出了合并本——那会是一本只有标题的书。"


def test_main_stops_before_any_network_setup_when_cancelled(monkeypatch):
    app = importlib.import_module("main")
    events = []

    class AlreadyCancelled:
        def is_set(self):
            return True

    monkeypatch.setattr(app, "Fetcher", lambda **kwargs: (_ for _ in ()).throw(
        AssertionError("network setup must not start after cancellation")
    ))

    result = app.main(["--novel", "99"], observer=events.append,
                      cancel_event=AlreadyCancelled())

    assert result == 130
    assert [event.kind for event in events] == ["cancelled"]
