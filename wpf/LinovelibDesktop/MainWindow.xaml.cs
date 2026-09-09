using System.Collections.ObjectModel;
using System.Diagnostics;
using System.IO;
using System.Windows.Data;
using System.Globalization;
using System.Linq;
using System.Windows;
using System.Windows.Controls;
using System.Windows.Input;
using System.Windows.Media;
using System.Windows.Threading;
using Microsoft.Win32;
using LinovelibDesktop.Models;
using LinovelibDesktop.Services;

namespace LinovelibDesktop;

public partial class MainWindow : Window
{
    private enum DownloadMode { Novel, Comic }

    private readonly DownloaderBridge _bridge = new();
    private readonly ObservableCollection<ChapterRow> _rows = new();
    private readonly Dictionary<string, ChapterRow> _rowsById = new();
    private readonly ObservableCollection<ResolveResultDto> _candidates = new();
    private string _lastLogLine = "";
    private DownloadMode _mode = DownloadMode.Novel;
    private readonly ObservableCollection<ChapterRow> _comicRows = new();
    private readonly Dictionary<string, ChapterRow> _comicRowsById = new();
    private readonly ObservableCollection<ResolveResultDto> _comicCandidates = new();
    private string _comicLastLogLine = "";
    private string _comicFilter = "全部";

    public MainWindow()
    {
        InitializeComponent();
        ChapterGrid.ItemsSource = _rows;
        CandidateList.ItemsSource = _candidates;
        CollectionViewSource.GetDefaultView(_rows).Filter = FilterRows;
        ComicChapterGrid.ItemsSource = _comicRows;
        ComicCandidateList.ItemsSource = _comicCandidates;
        CollectionViewSource.GetDefaultView(_comicRows).Filter = ComicFilterRows;
        SetDownloadMode(DownloadMode.Novel);
        UpdateTaskOverview();
    }

    private void NovelModeButton_Click(object sender, RoutedEventArgs e)
    {
        if (!StartButton.IsEnabled || !ComicStartButton.IsEnabled) { ModeSwitchBlocked(); return; }
        SetDownloadMode(DownloadMode.Novel);
    }

    private void ComicModeButton_Click(object sender, RoutedEventArgs e)
    {
        if (!StartButton.IsEnabled || !ComicStartButton.IsEnabled) { ModeSwitchBlocked(); return; }
        SetDownloadMode(DownloadMode.Comic);
    }

    private void ModeSwitchBlocked()
    {
        // 任一模式下载中禁止切换；提示落在当前可见模式的状态栏。
        if (_mode == DownloadMode.Comic) ComicReport("下载正在进行，完成或安全取消后才能切换模式。");
        else Report("下载正在进行，完成或安全取消后才能切换模式。");
    }

    private void SetDownloadMode(DownloadMode mode)
    {
        _mode = mode;
        var novel = mode == DownloadMode.Novel;
        NovelPage.Visibility = novel ? Visibility.Visible : Visibility.Collapsed;
        ComicPage.Visibility = novel ? Visibility.Collapsed : Visibility.Visible;
        NovelModeButton.Background = novel ? new SolidColorBrush(Color.FromRgb(225, 244, 235)) : Brushes.White;
        ComicModeButton.Background = novel ? Brushes.White : new SolidColorBrush(Color.FromRgb(225, 244, 235));
    }

    private async void ComicSearchButton_Click(object sender, RoutedEventArgs e)
    {
        var query = ComicIdBox.Text.Trim();
        if (query.Length == 0)
        {
            ComicStatusText.Text = "请输入漫画编号或书名。";
            return;
        }
        if (query.All(char.IsDigit))
        {
            _comicCandidates.Clear();
            ComicTaskOverviewText.Text = $"已识别漫画编号 #{query}";
            ComicStatusText.Text = "已识别漫画编号，可直接开始下载。";
            ComicLogSummaryText.Text = ComicStatusText.Text;
            SetComicCentralMode(selection: false);
            return;
        }

        ComicSearchButton.IsEnabled = false; ComicStartButton.IsEnabled = false;
        try
        {
            _comicCandidates.Clear();
            ComicStatusText.Text = $"正在按书名搜索『{query}』…";
            var results = await _bridge.ResolveComicAsync(query);
            foreach (var result in results) _comicCandidates.Add(result);

            if (_comicCandidates.Count == 0)
            {
                SetComicCentralMode(selection: false);
                ComicReport($"未找到名为『{query}』的漫画，请改用编号。");
                return;
            }
            if (_comicCandidates.Count == 1)
            {
                SetComicCentralMode(selection: false);
                var only = _comicCandidates[0];
                ComicIdBox.Text = only.Id;
                ComicReport($"已选定：{only.Title}（id={only.Id}）；请设置卷号后开始下载。");
                return;
            }
            // 书目与候选精确吻合：等同 CLI 的自动选取，无需再让用户筛选。
            var exact = _comicCandidates.FirstOrDefault(c => c.Exact);
            if (exact is not null)
            {
                SetComicCentralMode(selection: false);
                ComicIdBox.Text = exact.Id;
                ComicReport($"已选定：{exact.Title}（id={exact.Id}）；请设置卷号后开始下载。");
                return;
            }
            // 无精确吻合：在主内容区列出候选让用户筛选，确定后才进入卷数/下载。
            ComicCandidateList.SelectedIndex = -1;
            SetComicCentralMode(selection: true);
            ComicReport($"找到 {_comicCandidates.Count} 个候选，请在上方列表中选择书名。");
        }
        catch (Exception ex) { ComicReport(ex.Message); }
        finally { ComicSearchButton.IsEnabled = true; ComicStartButton.IsEnabled = true; }
    }

    private void ComicCandidateList_SelectionChanged(object sender, SelectionChangedEventArgs e)
    {
        if (ComicCandidateList.SelectedItem is not ResolveResultDto c) return;
        ComicIdBox.Text = c.Id;
        SetComicCentralMode(selection: false);
        ComicReport($"已选定：{c.Title}（id={c.Id}）；请设置卷号后开始下载。");
    }

    private async void ComicStartButton_Click(object sender, RoutedEventArgs e)
    {
        var id = ComicIdBox.Text.Trim();
        if (id.Length == 0)
        {
            ComicStatusText.Text = "请输入漫画编号，或先搜索书名。";
            return;
        }
        if (!id.All(char.IsDigit))
        {
            ComicStatusText.Text = "书名需先搜索并选定编号后才能开始下载。";
            return;
        }

        _comicRows.Clear(); _comicRowsById.Clear(); _comicLastLogLine = ""; ComicLogBox.Clear();
        ComicProgress.Value = 0; ComicProgress.Maximum = 1; ComicProgressText.Text = "0 / 0 章";
        SetComicFilter("全部"); UpdateComicOverview(); SetComicCentralMode(selection: false);
        ComicStartButton.IsEnabled = false; ComicCancelButton.IsEnabled = true; ComicStatusText.Text = "正在启动下载任务…";

        // 漫画无间隔输入框，沿用 comic CLI 默认 0.5s。
        var volumes = string.IsNullOrWhiteSpace(ComicVolumesBox.Text) ? "all" : ComicVolumesBox.Text.Trim();
        var request = new ComicDownloadRequest(id, volumes, "0.5", ComicOutputBox.Text.Trim());
        try
        {
            var exitCode = await _bridge.StartComicAsync(request,
                item => Dispatcher.Invoke(() => ApplyComicEvent(item)),
                line => Dispatcher.Invoke(() => AppendComicLog(line)));
            if (exitCode != 0 && ComicStatusText.Text is not "已在章节边界安全取消下载。")
                ComicReport($"下载进程已退出，代码 {exitCode}。");
        }
        catch (Exception ex) { ComicReport(ex.Message); }
        finally { ComicStartButton.IsEnabled = true; ComicCancelButton.IsEnabled = false; }
    }

    private void ComicCancelButton_Click(object sender, RoutedEventArgs e)
    {
        _bridge.RequestCancel();
        ComicCancelButton.IsEnabled = false;
        ComicStatusText.Text = "将在当前章节完成后安全取消。";
        AppendComicLog("已请求安全取消。");
    }

    private void ComicChooseOutput_Click(object sender, RoutedEventArgs e)
    {
        // 漫画 --out 是输出目录（非文件）：用目录选择器。
        var dialog = new Microsoft.Win32.OpenFolderDialog { Title = "选择漫画输出目录" };
        if (dialog.ShowDialog(this) == true) ComicOutputBox.Text = dialog.FolderName;
    }

    private async void StartButton_Click(object sender, RoutedEventArgs e)
    {
        var idText = NovelIdBox.Text.Trim();
        if (idText.Length == 0) { Report("请输入小说编号或书名。"); return; }
        // 书名必须先点击「搜索」解析出编号并选定，否则禁止直接下载（保证「书名先筛选→再卷数」）。
        if (!idText.All(char.IsDigit)) { Report("书名需先解析为编号：请点击『搜索』并选定候选后再开始下载。"); return; }
        if (!double.TryParse(DelayBox.Text, NumberStyles.Float, CultureInfo.InvariantCulture, out var delay) || delay < 0) { Report("请求间隔必须是大于等于 0 的数字。"); return; }

        _rows.Clear(); _rowsById.Clear(); _lastLogLine = ""; LogBox.Clear(); Progress.Value = 0; Progress.Maximum = 1; ProgressText.Text = "0 / 0 章";
        SetFilter("全部");
        UpdateTaskOverview();
        SetCentralMode(selection: false);
        StartButton.IsEnabled = false; CancelButton.IsEnabled = true; StatusText.Text = "正在启动下载任务…";
        var request = new DownloadRequest(idText, string.IsNullOrWhiteSpace(VolumesBox.Text) ? "all" : VolumesBox.Text.Trim(), delay.ToString(CultureInfo.InvariantCulture), OutputBox.Text.Trim());
        try
        {
            var exitCode = await _bridge.StartAsync(request, item => Dispatcher.Invoke(() => ApplyEvent(item)), line => Dispatcher.Invoke(() => AppendLog(line)));
            if (exitCode != 0 && StatusText.Text is not "已在章节边界安全取消下载。") Report($"下载进程已退出，代码 {exitCode}。");
        }
        catch (Exception ex) { Report(ex.Message); }
        finally { StartButton.IsEnabled = true; CancelButton.IsEnabled = false; }
    }

    private void CancelButton_Click(object sender, RoutedEventArgs e) { _bridge.RequestCancel(); CancelButton.IsEnabled = false; StatusText.Text = "将在当前章节完成后安全取消。"; AppendLog("已请求安全取消。"); }
    private void ChooseOutput_Click(object sender, RoutedEventArgs e) { var dialog = new SaveFileDialog { Filter = "EPUB 文件|*.epub", DefaultExt = ".epub" }; if (dialog.ShowDialog(this) == true) OutputBox.Text = dialog.FileName; }

    private void OpenDownloadsButton_Click(object sender, RoutedEventArgs e)
    {
        try
        {
            var directory = Path.Combine(ProjectPaths.FindRoot(), "download");
            Directory.CreateDirectory(directory);
            // Explicit Explorer invocation avoids custom default folder actions (e.g. terminals).
            var explorer = Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.Windows), "explorer.exe");
            var startInfo = new ProcessStartInfo(explorer)
            {
                UseShellExecute = false,
                CreateNoWindow = true,
            };
            startInfo.ArgumentList.Add(directory);
            Process.Start(startInfo);
        }
        catch (Exception ex)
        {
            MessageBox.Show(this, $"无法打开下载目录：{ex.Message}", "打开下载目录",
                MessageBoxButton.OK, MessageBoxImage.Warning);
        }
    }

    private async void SearchButton_Click(object sender, RoutedEventArgs e)
    {
        var text = NovelIdBox.Text.Trim();
        if (text.Length == 0) { Report("请输入小说编号或书名。"); return; }
        if (text.All(char.IsDigit)) { Report("已是编号，无需搜索；请直接设置卷号并点击开始下载。"); return; }

        SearchButton.IsEnabled = false; StartButton.IsEnabled = false;
        try
        {
            StatusText.Text = $"正在按书名搜索『{text}』…";
            var results = await _bridge.ResolveAsync(text);
            _candidates.Clear();
            foreach (var result in results) _candidates.Add(result);

            if (_candidates.Count == 0)
            {
                SetCentralMode(selection: false);
                Report($"未找到名为『{text}』的小说，请改用编号。");
                return;
            }
            if (_candidates.Count == 1)
            {
                SetCentralMode(selection: false);
                var only = _candidates[0];
                NovelIdBox.Text = only.Id;
                Report($"已选定：{only.Title}（id={only.Id}）；请设置卷号后开始下载。");
                return;
            }
            // 书目与候选精确吻合：等同 CLI 的自动选取，无需再让用户筛选。
            var exact = _candidates.FirstOrDefault(c => c.Exact);
            if (exact is not null)
            {
                SetCentralMode(selection: false);
                NovelIdBox.Text = exact.Id;
                Report($"已选定：{exact.Title}（id={exact.Id}）；请设置卷号后开始下载。");
                return;
            }
            // 无精确吻合：在主内容区列出候选让用户筛选，确定后才进入卷数/下载。
            CandidateList.SelectedIndex = -1;
            SetCentralMode(selection: true);
            Report($"找到 {_candidates.Count} 个候选，请在上方列表中选择书名。");
        }
        catch (Exception ex) { Report(ex.Message); }
        finally { SearchButton.IsEnabled = true; StartButton.IsEnabled = true; }
    }

    private void CandidateList_SelectionChanged(object sender, SelectionChangedEventArgs e)
    {
        if (CandidateList.SelectedItem is not ResolveResultDto c) return;
        NovelIdBox.Text = c.Id;
        SetCentralMode(selection: false);
        Report($"已选定：{c.Title}（id={c.Id}）；请设置卷号后开始下载。");
    }

    /// <summary>切换主内容区：true=展示书名候选列表供选择；false=展示章节下载进度表。</summary>
    private void SetCentralMode(bool selection)
    {
        CandidateList.Visibility = selection ? Visibility.Visible : Visibility.Collapsed;
        ChapterGrid.Visibility = selection ? Visibility.Collapsed : Visibility.Visible;
    }

    /// <summary>DataGrid 默认滚轮一次跳 3 行，改为每次只滚动一行（逐行平滑滚）。</summary>
    private void DataGrid_SmoothWheel(object sender, MouseWheelEventArgs e)
    {
        if (sender is DependencyObject d && FindVisualChildScrollViewer(d) is { } sv)
        {
            if (e.Delta > 0) sv.LineUp(); else sv.LineDown();
            e.Handled = true;
        }
    }

    private static ScrollViewer? FindVisualChildScrollViewer(DependencyObject root)
    {
        for (var i = 0; i < VisualTreeHelper.GetChildrenCount(root); i++)
        {
            var child = VisualTreeHelper.GetChild(root, i);
            if (child is ScrollViewer sv) return sv;
            if (FindVisualChildScrollViewer(child) is { } nested) return nested;
        }
        return null;
    }

    private void ApplyEvent(DownloadEventDto item)
    {
        if (item.Total > 0) { Progress.Maximum = item.Total; ProgressText.Text = $"{item.Completed} / {item.Total} 章"; }
        Progress.Value = Math.Min(item.Completed, Progress.Maximum);
        if (!string.IsNullOrWhiteSpace(item.Message)) { StatusText.Text = item.Message; AppendLog(item.Message); }
        if (!string.IsNullOrWhiteSpace(item.OutputPath)) { StatusText.Text = $"已生成：{item.OutputPath}"; AppendLog(StatusText.Text); }
        if (item.Kind == "chapter_pending") { var row = new ChapterRow { Id = item.ChapterId, Volume = item.VolumeTitle, Chapter = item.ChapterTitle }; _rows.Add(row); _rowsById[item.ChapterId] = row; }
        if (_rowsById.TryGetValue(item.ChapterId, out var current))
        {
            current.State = item.Kind switch { "chapter_started" => "下载中", "chapter_finished" => "已完成", "chapter_failed" => "失败", _ => current.State };
            if (!string.IsNullOrWhiteSpace(item.Message)) current.Detail = item.Message;
        }
        if (item.Kind == "cancelled") StatusText.Text = "已在章节边界安全取消下载。";
        CollectionViewSource.GetDefaultView(_rows).Refresh();
        UpdateTaskOverview();
    }

    private void AppendLog(string line)
    {
        if (line == _lastLogLine) return;
        _lastLogLine = line;
        LogBox.AppendText(line + Environment.NewLine);
        ScrollLogToEndAfterLayout();
        UpdateTaskOverview();
    }

    private void ScrollLogToEndAfterLayout()
    {
        Dispatcher.BeginInvoke(() => LogBox.ScrollToEnd(), DispatcherPriority.Background);
    }

    private void LogToggleButton_Click(object sender, RoutedEventArgs e)
    {
        var opening = LogPanel.Visibility != Visibility.Visible;
        if (opening)
        {
            LogPanel.Visibility = Visibility.Visible;
            LogToggleButton.Content = "收起日志";
            ScrollLogToEndAfterLayout();
        }
        else
        {
            LogPanel.Visibility = Visibility.Collapsed;
            LogToggleButton.Content = "展开日志";
        }
    }

    private string _filter = "全部";
    private bool FilterRows(object item) => item is ChapterRow row && (_filter == "全部" || row.State == _filter);
    private void AllFilterButton_Click(object sender, RoutedEventArgs e) => SetFilter("全部");
    private void CompletedFilterButton_Click(object sender, RoutedEventArgs e) => SetFilter("已完成");
    private void WaitingFilterButton_Click(object sender, RoutedEventArgs e) => SetFilter("等待中");
    private void FailedFilterButton_Click(object sender, RoutedEventArgs e) => SetFilter("失败");
    private void SetFilter(string filter) { _filter = filter; CollectionViewSource.GetDefaultView(_rows).Refresh(); }

    private void UpdateTaskOverview()
    {
        var finished = _rows.Count(row => row.State == "已完成");
        var failed = _rows.Count(row => row.State == "失败");
        var running = _rows.Count(row => row.State == "下载中");
        var waiting = _rows.Count(row => row.State == "等待中");
        TaskOverviewText.Text = _rows.Count == 0
            ? "准备开始新的下载任务"
            : $"{finished} 已完成 · {running} 下载中 · {waiting} 等待 · {failed} 失败";
        LogSummaryText.Text = string.IsNullOrWhiteSpace(_lastLogLine) ? "暂无运行日志" : _lastLogLine;
    }
    private void Report(string message) { StatusText.Text = message; AppendLog(message); }

    private void ApplyComicEvent(DownloadEventDto item)
    {
        // 漫画事件携带「当前卷」的 completed/total（逐卷循环），进度条反映当前卷，符合预期。
        if (item.Total > 0) { ComicProgress.Maximum = item.Total; ComicProgressText.Text = $"{item.Completed} / {item.Total} 章"; }
        ComicProgress.Value = Math.Min(item.Completed, ComicProgress.Maximum);
        if (!string.IsNullOrWhiteSpace(item.Message)) { ComicStatusText.Text = item.Message; AppendComicLog(item.Message); }
        if (!string.IsNullOrWhiteSpace(item.OutputPath)) { ComicStatusText.Text = $"已生成：{item.OutputPath}"; AppendComicLog(ComicStatusText.Text); }
        if (item.Kind == "chapter_pending")
        {
            var row = new ChapterRow { Id = item.ChapterId, Volume = item.VolumeTitle, Chapter = item.ChapterTitle };
            _comicRows.Add(row); _comicRowsById[item.ChapterId] = row;
        }
        if (_comicRowsById.TryGetValue(item.ChapterId, out var current))
        {
            current.State = item.Kind switch
            {
                "chapter_started" => "下载中",
                "chapter_finished" => "已完成",
                "chapter_failed" => "失败",
                _ => current.State
            };
            if (!string.IsNullOrWhiteSpace(item.Message)) current.Detail = item.Message;
        }
        if (item.Kind == "cancelled") ComicStatusText.Text = "已在章节边界安全取消下载。";
        CollectionViewSource.GetDefaultView(_comicRows).Refresh();
        UpdateComicOverview();
    }

    private void AppendComicLog(string line)
    {
        if (line == _comicLastLogLine) return;
        _comicLastLogLine = line;
        ComicLogBox.AppendText(line + Environment.NewLine);
        Dispatcher.BeginInvoke(() => ComicLogBox.ScrollToEnd(), DispatcherPriority.Background);
        UpdateComicOverview();
    }

    private void ComicReport(string message) { ComicStatusText.Text = message; AppendComicLog(message); }

    private bool ComicFilterRows(object item) => item is ChapterRow row && (_comicFilter == "全部" || row.State == _comicFilter);
    private void ComicAllFilterButton_Click(object sender, RoutedEventArgs e) => SetComicFilter("全部");
    private void ComicCompletedFilterButton_Click(object sender, RoutedEventArgs e) => SetComicFilter("已完成");
    private void ComicWaitingFilterButton_Click(object sender, RoutedEventArgs e) => SetComicFilter("等待中");
    private void ComicFailedFilterButton_Click(object sender, RoutedEventArgs e) => SetComicFilter("失败");
    private void SetComicFilter(string filter) { _comicFilter = filter; CollectionViewSource.GetDefaultView(_comicRows).Refresh(); }

    private void UpdateComicOverview()
    {
        var finished = _comicRows.Count(row => row.State == "已完成");
        var failed = _comicRows.Count(row => row.State == "失败");
        var running = _comicRows.Count(row => row.State == "下载中");
        var waiting = _comicRows.Count(row => row.State == "等待中");
        ComicTaskOverviewText.Text = _comicRows.Count == 0
            ? "准备开始新的下载任务"
            : $"{finished} 已完成 · {running} 下载中 · {waiting} 等待 · {failed} 失败";
        ComicLogSummaryText.Text = string.IsNullOrWhiteSpace(_comicLastLogLine) ? "暂无运行日志" : _comicLastLogLine;
    }

    /// <summary>切换漫画主内容区：true=展示书名候选列表供选择；false=展示章节下载进度表。</summary>
    private void SetComicCentralMode(bool selection)
    {
        ComicCandidateList.Visibility = selection ? Visibility.Visible : Visibility.Collapsed;
        ComicChapterGrid.Visibility = selection ? Visibility.Collapsed : Visibility.Visible;
    }

    private void ComicLogToggleButton_Click(object sender, RoutedEventArgs e)
    {
        var opening = ComicLogPanel.Visibility != Visibility.Visible;
        if (opening)
        {
            ComicLogPanel.Visibility = Visibility.Visible;
            ComicLogToggleButton.Content = "收起日志";
            Dispatcher.BeginInvoke(() => ComicLogBox.ScrollToEnd(), DispatcherPriority.Background);
        }
        else
        {
            ComicLogPanel.Visibility = Visibility.Collapsed;
            ComicLogToggleButton.Content = "展开日志";
        }
    }
}
