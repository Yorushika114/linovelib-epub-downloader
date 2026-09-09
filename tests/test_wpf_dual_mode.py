from pathlib import Path


ROOT = Path(__file__).parents[1]
XAML = (ROOT / "wpf" / "LinovelibDesktop" / "MainWindow.xaml").read_text(encoding="utf-8")
CODE = (ROOT / "wpf" / "LinovelibDesktop" / "MainWindow.xaml.cs").read_text(encoding="utf-8")


def test_wpf_has_two_mode_sidebar_and_separate_pages():
    for name in ("NovelModeButton", "ComicModeButton", "NovelPage", "ComicPage"):
        assert f'x:Name="{name}"' in XAML
    assert 'Content="小说下载"' in XAML
    assert 'Content="漫画下载"' in XAML
    comic_page = XAML.split('x:Name="ComicPage"', 1)[1].split('x:Name="ComicIdBox"', 1)[0]
    assert 'Visibility="Collapsed"' in comic_page


def test_wpf_declares_mode_switch_and_comic_is_bridged():
    assert "private enum DownloadMode" in CODE
    assert "private void SetDownloadMode(DownloadMode mode)" in CODE
    assert "NovelModeButton_Click" in CODE
    assert "ComicModeButton_Click" in CODE
    assert "ResolveComicAsync" in CODE
    assert "StartComicAsync" in CODE
    assert "ComicCandidateList" in CODE
    assert "SetComicCentralMode" in CODE
    assert "ComicCancelButton_Click" in CODE
    # 漫画仍是桥接后端：WPF 进程不得直接 import comic.fetcher（其顶层 import playwright）。
    assert "comic.fetcher" not in CODE


def test_comic_page_matches_the_novel_task_overview_and_queue_layout():
    for name in (
        "ComicSearchButton",
        "ComicTaskSummaryCard",
        "ComicTaskOverviewText",
        "ComicProgressText",
        "ComicProgress",
        "ComicLogSummaryText",
        "ComicChapterGrid",
        "ComicLogToggleButton",
    ):
        assert f'x:Name="{name}"' in XAML


def test_comic_search_handles_numeric_ids_without_a_title_lookup():
    # ComicSearchButton_Click / ComicStartButton_Click 已是 async void，锚点去掉返回值前缀。
    assert "ComicSearchButton_Click" in CODE
    search_body = CODE.split("ComicSearchButton_Click", 1)[1].split("ComicStartButton_Click", 1)[0]
    assert "All(char.IsDigit)" in search_body
    assert "已识别漫画编号" in search_body
    assert "ResolveComicAsync" in search_body
    assert "书名搜索需要受授权的数据源" not in search_body
