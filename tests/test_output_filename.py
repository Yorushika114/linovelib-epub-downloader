"""成品 EPUB 的文件名与落盘：两个都会「静默毁掉一整卷」的地方。

为什么单独钉这两处：
· 文件名里混进冒号，Windows 会把「名字:后半.epub」当成「文件 + 隐藏数据流」。用户看不到
  文件，`exists()` 却命中流、把这一卷永久跳过。实测已在 download/小说/Re从零开始的异世界
  生活/ 下留下一条 40 MB 的隐藏流（卷标签 "Re:zeropedia 公式书"）。
· 成品若不是原子落盘，合成中途被打断就留下半截 epub；「已存在就跳过」的闸门认了它，
  这一卷同样永久跳过。两处症状一样（书永远下不下来），成因不同，故各钉一组。
"""

import zipfile
from pathlib import Path

import main
from linovelib import epub_builder

HTML = ('<html lang="en" xml:lang="en"><head><title>t</title></head>'
        '<body><p>正文</p></body></html>')


def _make_src(tmp_path, name="ebooklib_raw.zip"):
    """造一个最小可用的「ebooklib 产物」压缩包给 _finalize_xhtml 重写。

    刻意不叫 *.tmp：下面几处断言「目录里不留 .epub.tmp」，夹具自己不能先占一个名字。
    """
    src = tmp_path / name
    with zipfile.ZipFile(src, "w") as z:
        z.writestr("vol1_ch1.xhtml", HTML)
        z.writestr("mimetype", "application/epub+zip")
    return src


# --------------------------------------------------------------- 文件名：冒号

def test_volume_filename_strips_the_colon_that_caused_the_data_loss():
    """就是这条卷标签把 40 MB 的成品变成了不可见的 NTFS 数据流。"""
    name = main._volume_filename("Re从零开始的异世界生活", " Re:zeropedia 公式书")

    assert name == "Re从零开始的异世界生活 Rezeropedia 公式书.epub"
    assert ":" not in name


def test_volume_filename_matches_the_old_writing_for_legal_labels():
    """合法后缀下必须与旧写法逐字节一致，否则已下好的书会被当成新任务重下。"""
    for suffix in (" 第4卷", " 第8.5卷", " 第1-5卷", " 第1,3,5卷", " SSS 篇"):
        assert main._volume_filename("某书", suffix) == f"某书{suffix}.epub"


def test_volume_filename_has_no_separator_when_there_is_no_label():
    assert main._volume_filename("某书", "") == "某书.epub"


def test_volume_filename_never_produces_an_alternate_data_stream(tmp_path):
    """决定性断言：目录里必须出现**一个**名字完整的文件。

    冒号一旦漏进去，Windows 会把后半截当流，目录里只剩一个 0 字节、没有扩展名的
    「Re从零开始的异世界生活 Re」——stat/read_bytes 都可能照样命中那条流而看不出来，
    只有列举目录才照得出真相，这也正是用户在资源管理器里看到的样子。
    """
    folder = tmp_path / "小说"
    folder.mkdir()
    out = folder / main._volume_filename("Re从零开始的异世界生活", " Re:zeropedia 公式书")

    out.write_bytes(b"EPUB" * 10)

    assert [p.name for p in folder.iterdir()] == [out.name]
    assert out.read_bytes() == b"EPUB" * 10


def test_volume_suffix_stays_unsanitized_for_display():
    """清洗只发生在拼文件名时：同一个后缀还要当界面卷标签用，那儿不该被改。"""
    source = Path(main.__file__).read_text(encoding="utf-8")

    assert "vol_label = _volume_suffix([vol], novel.title).strip()" in source


def test_both_filename_sites_go_through_the_helper():
    """两处拼名（逐卷、--merge 合并本）都必须走 _volume_filename，不能再有裸 f-string。"""
    source = Path(main.__file__).read_text(encoding="utf-8")

    assert source.count("_volume_filename(") == 3  # 1 处定义 + 2 处调用
    assert "_volume_suffix([vol], novel.title)}.epub" not in source
    assert "_volume_suffix(volumes, novel.title)}.epub" not in source


def test_sanitize_keeps_its_novel_fallback():
    """抽 _strip_illegal 时别把书名的兜底一起弄丢——旧行为是空名变 "novel"。"""
    assert main._sanitize(":::") == "novel"
    assert main._sanitize("书名") == "书名"
    assert main._strip_illegal(":::") == ""


# --------------------------------------------------- 跳过闸门：半截文件不算数

def test_skip_gate_accepts_a_real_epub(tmp_path):
    assert main._is_complete_epub(_make_src(tmp_path))


def test_skip_gate_rejects_a_truncated_epub(tmp_path):
    """合成到一半被打断的文件：ZIP 中央目录在末尾，截断后找不到，必须判为不完整。

    没有这道闸门，用户看到的就是「明明没下下来，工具却说已存在、跳过」。
    """
    src = _make_src(tmp_path)
    whole = src.read_bytes()
    truncated = tmp_path / "half.epub"
    truncated.write_bytes(whole[: len(whole) // 2])

    assert not main._is_complete_epub(truncated)


def test_skip_gate_rejects_missing_and_empty_paths(tmp_path):
    assert not main._is_complete_epub(tmp_path / "没有这个文件.epub")
    empty = tmp_path / "空.epub"
    empty.write_bytes(b"")
    assert not main._is_complete_epub(empty)


# --------------------------------------------------------- 原子落盘：不留半成品

def test_finalize_writes_the_target_and_cleans_up_its_temp(tmp_path):
    src = _make_src(tmp_path)
    dst = tmp_path / "书名 第1卷.epub"

    epub_builder._finalize_xhtml(src, dst, {"vol1_ch1.xhtml": "第一章"})

    assert dst.exists()
    with zipfile.ZipFile(dst) as z:
        assert "正文" in z.read("vol1_ch1.xhtml").decode("utf-8")
        assert 'lang="zh-CN"' in z.read("vol1_ch1.xhtml").decode("utf-8")
    # 目标目录里除了成品不该留下任何 .epub.tmp。
    assert [p.name for p in tmp_path.iterdir() if p.suffix == ".tmp"] == []


def test_finalize_failure_leaves_no_target_and_no_temp(tmp_path, monkeypatch):
    """中途炸掉时，目标文件必须**根本不存在**——留半截就是永久跳过的种子。"""
    src = _make_src(tmp_path)
    dst = tmp_path / "书名 第2卷.epub"

    def boom(*a, **kw):
        raise RuntimeError("合成中途失败")

    monkeypatch.setattr(epub_builder, "_inject_head", boom)

    try:
        epub_builder._finalize_xhtml(src, dst, {})
    except RuntimeError:
        pass
    else:
        raise AssertionError("异常应当原样抛出，交给调用方走替身路径")

    assert not dst.exists()
    assert [p.name for p in tmp_path.iterdir() if p.suffix == ".tmp"] == []


def test_finalize_overwrites_a_truncated_target(tmp_path):
    """重下时必须能盖掉上次留下的半截文件（os.replace 会无条件覆盖）。"""
    src = _make_src(tmp_path)
    dst = tmp_path / "书名 第3卷.epub"
    dst.write_bytes(b"half-written garbage")

    epub_builder._finalize_xhtml(src, dst, {})

    assert zipfile.is_zipfile(dst)


# ------------------------------------------------- 替身路径：目标被占用时的退路

def test_alternate_path_steps_over_a_candidate_it_cannot_open(tmp_path):
    """候选路径存在但打不开时，必须继续往后找，而不是把异常穿出去。

    旧实现用 `open(cand, "rb")` 探空位、只 catch FileNotFoundError —— 候选**本身**
    不可打开时抛的是 PermissionError，恰好是替身机制本该生效的那种情况，它却失效了。
    这里用一个同名目录来制造「存在但打不开」：open 目录在 Windows 抛 PermissionError、
    在 Linux 抛 IsADirectoryError，两者都不是 FileNotFoundError。
    """
    target = tmp_path / "书名.epub"
    blocked = tmp_path / "书名. 1.epub"
    blocked.mkdir()

    assert epub_builder._alternate_path(target) == tmp_path / "书名. 2.epub"


def test_alternate_path_skips_existing_files(tmp_path):
    target = tmp_path / "书名.epub"
    (tmp_path / "书名. 1.epub").write_bytes(b"x")

    assert epub_builder._alternate_path(target) == tmp_path / "书名. 2.epub"
