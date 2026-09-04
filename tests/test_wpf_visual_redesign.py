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


def test_wpf_log_panel_is_height_limited_and_scrollable():
    log_box = XAML.split('x:Name="LogBox"', 1)[1].split('</TextBox>', 1)[0]
    log_panel = XAML.split('x:Name="LogPanel"', 1)[1].split('x:Name="LogBox"', 1)[0]

    assert 'MaxHeight="188"' in log_panel
    assert 'VerticalAlignment="Stretch"' in log_box
    assert 'VerticalScrollBarVisibility="Auto"' in log_box


def test_wpf_default_window_fits_a_1280_by_720_work_area():
    assert 'Width="1180"' in XAML
    assert 'Height="650"' in XAML


def test_wpf_refreshes_active_filter_after_each_download_event():
    event_body = CODE.split("private void ApplyEvent", 1)[1].split("private void AppendLog", 1)[0]
    assert "CollectionViewSource.GetDefaultView(_rows).Refresh();" in event_body


def test_wpf_filter_buttons_share_the_queue_header_row_with_log_toggle():
    queue_header = XAML.split('Text="章节队列"', 1)[1].split('<Grid Grid.Row="1"', 1)[0]

    for name in ("AllFilterButton", "CompletedFilterButton", "WaitingFilterButton"):
        assert f'x:Name="{name}"' in queue_header
    assert 'Grid.Column="1" Orientation="Horizontal" VerticalAlignment="Center"' in queue_header
    assert 'x:Name="LogToggleButton" Grid.Column="2"' in queue_header
    assert 'Margin="0,350,215,0"' not in XAML


def test_wpf_resets_the_chapter_filter_before_a_new_download_starts():
    start_body = CODE.split("private async void StartButton_Click", 1)[1].split("private void CancelButton_Click", 1)[0]
    assert 'SetFilter("全部");' in start_body
