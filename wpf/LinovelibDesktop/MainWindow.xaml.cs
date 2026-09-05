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
using Microsoft.Win32;
using LinovelibDesktop.Models;
using LinovelibDesktop.Services;

namespace LinovelibDesktop;

public partial class MainWindow : Window
{
    private readonly DownloaderBridge _bridge = new();
    private readonly ObservableCollection<ChapterRow> _rows = new();
    private readonly Dictionary<string, ChapterRow> _rowsById = new();
    private readonly ObservableCollection<ResolveResultDto> _candidates = new();
    private string _lastLogLine = "";

    public MainWindow()
    {
        InitializeComponent();
        ChapterGrid.ItemsSource = _rows;
        CandidateList.ItemsSource = _candidates;
        CollectionViewSource.GetDefaultView(_rows).Filter = FilterRows;
        UpdateTaskOverview();
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
        LogBox.ScrollToEnd();
        UpdateTaskOverview();
    }
    private void LogToggleButton_Click(object sender, RoutedEventArgs e)
    {
        var opening = LogPanel.Visibility != Visibility.Visible;
        if (opening)
        {
            LogPanel.Visibility = Visibility.Visible;
            LogToggleButton.Content = "收起日志";
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
}
