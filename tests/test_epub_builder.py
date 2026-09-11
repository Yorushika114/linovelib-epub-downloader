import tempfile
import io
import shutil
from pathlib import Path
import pytest
from PIL import Image
from ebooklib import epub
from linovelib.models import Novel, Volume, Chapter, ImageAsset
from linovelib.epub_builder import build_epub
import linovelib.epub_builder as builder


def _jpeg():
    buf = io.BytesIO()
    Image.new("RGB", (50, 50), "red").save(buf, format="JPEG")
    return buf.getvalue()


def test_build_epub_produces_readable_epub():
    cover = _jpeg()
    ch = Chapter(id="154932", url="u", title="序", html="<p>正文</p>")
    ch.image_assets = [ImageAsset(epub_path="images/154932_1.jpg", data=cover)]
    novel = Novel(id="3095", title="测试书", author="某作者", cover_text="(无封面)",
                  volumes=[Volume(title="卷1", chapters=[ch])])

    with tempfile.TemporaryDirectory() as d:
        out = d + "/out.epub"
        build_epub(novel, out, cover)
        book = epub.read_epub(out)
        names = {it.file_name for it in book.get_items()}
        assert book.get_metadata("DC", "title")[0][0] == "测试书"
        assert "cover.xhtml" in names
        assert "vol1_ch1.xhtml" in names
        assert "images/154932_1.jpg" in names
        # 章节正文应进入文档而非只生成封面/导航
        ch1 = next(it for it in book.get_items() if it.file_name == "vol1_ch1.xhtml")
        assert "正文" in ch1.get_content().decode("utf-8")


def test_build_epub_empty_volumes_raises():
    with tempfile.TemporaryDirectory() as d:
        novel = Novel(id="1", title="空书", author="")
        with pytest.raises(ValueError):
            build_epub(novel, d + "/out.epub", None)


def test_build_epub_after_attaching_volumes():
    # 复刻 main.py 的接线：fetch_novel 得到 volumes 为空，随后绑定 volumes 再构建
    cover = _jpeg()
    ch = Chapter(id="154932", url="u", title="序", html="<p>正文</p>")
    novel = Novel(id="3095", title="测试书", author="某作者")
    volumes = [Volume(title="卷1", chapters=[ch])]
    novel.volumes = volumes

    with tempfile.TemporaryDirectory() as d:
        out = d + "/out.epub"
        build_epub(novel, out, cover)
        book = epub.read_epub(out)
        names = {it.file_name for it in book.get_items()}
        assert "vol1_ch1.xhtml" in names


def _simple_novel():
    ch = Chapter(id="154932", url="u", title="序", html="<p>正文</p>")
    return Novel(id="3095", title="测试书", author="某作者",
                 volumes=[Volume(title="卷1", chapters=[ch])])


def test_transient_lock_on_the_target_directory_is_retried(monkeypatch, tmp_path):
    """目标目录里那个临时文件被杀软锁一瞬，不能读成「成品被阅读器占用」。

    _finalize_xhtml 会在**目标同目录**再开一个 .epub.tmp，Windows Defender 的实时扫描
    会把它锁住一瞬并抛 PermissionError——与「目标 epub 正被别的程序打开」是同一个异常
    类型。没有重试的话，前者会被读成后者：本来可以正常覆盖，却凭空写出一个「书名. 1.epub」
    替身；用户下次重跑找不到原文件名，还会整本重下。
    """
    out = tmp_path / "测试书.epub"
    calls = []

    def flaky(src, dst, title_map):
        calls.append(Path(dst))
        if len(calls) == 1:
            raise PermissionError("杀软扫了一下")
        shutil.copyfile(src, dst)

    monkeypatch.setattr(builder, "_finalize_xhtml", flaky)
    monkeypatch.setattr(builder.time, "sleep", lambda _s: None)

    dest = build_epub(_simple_novel(), str(out), None)

    assert Path(dest) == out, "一次瞬时锁就把成品改写成了替身文件。"
    assert calls == [out, out], "没有对目标目录里的临时文件重试。"
    assert not (tmp_path / "测试书. 1.epub").exists()


def test_persistent_lock_still_falls_back_to_an_alternate_file(monkeypatch, tmp_path):
    """真的被占用时仍要走替身——重试不能把「该另存为」的情况一起吞掉。"""
    out = tmp_path / "测试书.epub"

    def locked(src, dst, title_map):
        if Path(dst) == out:
            raise PermissionError("阅读器占着这个文件")
        shutil.copyfile(src, dst)

    monkeypatch.setattr(builder, "_finalize_xhtml", locked)
    monkeypatch.setattr(builder.time, "sleep", lambda _s: None)

    dest = build_epub(_simple_novel(), str(out), None)

    assert Path(dest) == tmp_path / "测试书. 1.epub"
    assert (tmp_path / "测试书. 1.epub").exists()
