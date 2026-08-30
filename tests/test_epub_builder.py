import tempfile
import io
import pytest
from PIL import Image
from ebooklib import epub
from linovelib.models import Novel, Volume, Chapter, ImageAsset
from linovelib.epub_builder import build_epub


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
