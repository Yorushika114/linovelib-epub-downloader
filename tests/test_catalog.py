import pathlib
from linovelib.catalog import (parse_novel_page, parse_catalog,
                               parse_volume_page, parse_volume_chapters)


def _fixture(name):
    return (pathlib.Path(__file__).parent / "fixtures" / name).read_text(encoding="utf-8")


def test_parse_novel_page():
    novel = parse_novel_page(_fixture("novel_page.html"), "3095")
    assert novel.id == "3095"
    assert novel.title == "败北女角太多了！"
    assert novel.author == "雨森焚火"
    assert novel.cover_url == "https://www.linovelib.com/files/article/image/3/3095/3095s.jpg"


def test_parse_catalog_volumes_and_chapters():
    volumes = parse_catalog(_fixture("catalog_page.html"), "3095")
    assert len(volumes) == 2
    assert volumes[0].title == "败北女角太多了！ 1"
    assert volumes[0].vid == "154930"  # 卷页链接 /novel/3095/vol_154930.html 的卷号
    assert [c.id for c in volumes[0].chapters] == ["154931", "154932"]
    assert volumes[0].chapters[1].title == "序"
    assert volumes[0].chapters[1].url == "https://www.linovelib.com/novel/3095/154932.html"
    assert volumes[1].title == "败北女角太多了！ 2"
    assert volumes[1].vid == ""  # 该 fixture 卷 2 无卷页链接，vid 留空
    assert volumes[1].chapters[0].id == "163811"


def test_parse_volume_page_cover():
    html = _fixture("volume_page.html")
    assert parse_volume_page(html) == "https://img3.readpai.com/cover/3095/200726.jpg"


def test_parse_volume_page_no_cover():
    assert parse_volume_page("<html><head></head><body><p>x</p></body></html>") == ""


def test_parse_volume_chapters_includes_catalog_missing_chapter():
    # vol4 卷页的章节列表含「～第一败～」(181030)——它在目录页被漏掉，但卷页齐全
    chapters = parse_volume_chapters(_fixture("volume4_page.html"), "3095")
    ids = [c.id for c in chapters]
    assert ids == ["181028", "181029", "181030", "181031", "181032",
                   "181033", "181034", "181035", "181036", "181037",
                   "186468", "186469"]
    assert "181030" in ids
    assert chapters[2].title == "～第一败～ 别看我这样，其实很〇〇"
    assert chapters[2].url == "https://www.linovelib.com/novel/3095/181030.html"
