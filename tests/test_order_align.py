from ebooklib import epub

from linovelib.order_align import ReferenceAligner


def _write_reference_epub(path, paragraphs):
    book = epub.EpubBook()
    book.set_identifier("reference-test")
    book.set_title("reference")
    chapter = epub.EpubHtml(title="reference", file_name="reference.xhtml", lang="zh")
    chapter.content = "".join(f"<p>{text}</p>" for text in paragraphs)
    book.add_item(chapter)
    book.add_item(epub.EpubNcx())
    book.add_item(epub.EpubNav())
    book.spine = [chapter]
    epub.write_epub(str(path), book)


def test_reference_aligner_reorders_cross_translation_paragraphs(tmp_path):
    reference = [
        "天空万里无云，冬意渐浓。",
        "红绿灯变成绿色。",
        "我穿过校门，继续向前走。",
    ]
    _write_reference_epub(tmp_path / "reference.epub", reference)
    aligner = ReferenceAligner(tmp_path / "reference.epub")
    scrambled_site_text = [
        "我继续向前走过了校门。",
        "灯号变绿了。",
        "头顶天空无云，冬天逐渐来临。",
    ]

    assert aligner.align(scrambled_site_text) == [
        "头顶天空无云，冬天逐渐来临。",
        "灯号变绿了。",
        "我继续向前走过了校门。",
    ]
