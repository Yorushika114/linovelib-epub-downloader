from pathlib import Path


ROOT = Path(__file__).parents[1]
XAML = (ROOT / "wpf" / "LinovelibDesktop" / "MainWindow.xaml").read_text(encoding="utf-8")
CODE = (ROOT / "wpf" / "LinovelibDesktop" / "MainWindow.xaml.cs").read_text(encoding="utf-8")


def test_wpf_uses_light_workspace_sections_and_a_collapsed_log_panel():
    for name in ("TaskSummaryCard", "TaskOverviewText", "LogToggleButton", "LogPanel"):
        assert f'x:Name="{name}"' in XAML
    log_panel = XAML.split('x:Name="LogPanel"', 1)[1].split('x:Name="LogBox"', 1)[0]
    assert 'Visibility="Collapsed"' in log_panel
    assert 'Click="LogToggleButton_Click"' in XAML


def test_wpf_has_semantic_chapter_status_badges_and_no_fake_navigation_rail():
    for style in ("WaitingBadge", "RunningBadge", "CompletedBadge", "FailedBadge"):
        assert f'x:Key="{style}"' in XAML
    assert 'Style="{StaticResource ChapterStatusBadge}"' in XAML
    assert 'Grid.ColumnDefinitions><ColumnDefinition Width="58"' not in XAML


def test_wpf_updates_overview_from_download_events():
    assert "private void UpdateTaskOverview()" in CODE
    assert "TaskOverviewText.Text" in CODE
    assert "LogSummaryText.Text" in CODE
    assert "UpdateTaskOverview();" in CODE


def test_wpf_exposes_log_toggle_handler_and_clear_status_copy():
    assert "private void LogToggleButton_Click" in CODE
    assert 'LogPanel.Visibility = Visibility.Visible' in CODE
    assert 'LogPanel.Visibility = Visibility.Collapsed' in CODE
    assert "已完成" in XAML and "下载中" in XAML and "失败" in XAML


def test_wpf_default_window_fits_a_1280_by_720_work_area():
    assert 'Width="1180"' in XAML
    assert 'Height="650"' in XAML
