using System.Collections.ObjectModel;
using System.Globalization;
using System.Windows;
using Microsoft.Win32;
using LinovelibDesktop.Models;
using LinovelibDesktop.Services;

namespace LinovelibDesktop;

public partial class MainWindow : Window
{
    private readonly DownloaderBridge _bridge = new();
    private readonly ObservableCollection<ChapterRow> _rows = new();
    private readonly Dictionary<string, ChapterRow> _rowsById = new();

    public MainWindow()
    {
        InitializeComponent();
        ChapterGrid.ItemsSource = _rows;
    }

    private async void StartButton_Click(object sender, RoutedEventArgs e)
    {
        if (string.IsNullOrWhiteSpace(NovelIdBox.Text)) { Report("请输入小说编号。"); return; }
        if (!double.TryParse(DelayBox.Text, NumberStyles.Float, CultureInfo.InvariantCulture, out var delay) || delay < 0) { Report("请求间隔必须是大于等于 0 的数字。"); return; }

        _rows.Clear(); _rowsById.Clear(); Progress.Value = 0; Progress.Maximum = 1; ProgressText.Text = "0 / 0 章";
        StartButton.IsEnabled = false; CancelButton.IsEnabled = true; StatusText.Text = "正在启动下载任务…";
        var request = new DownloadRequest(NovelIdBox.Text.Trim(), string.IsNullOrWhiteSpace(VolumesBox.Text) ? "all" : VolumesBox.Text.Trim(), delay.ToString(CultureInfo.InvariantCulture), OutputBox.Text.Trim());
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
    }

    private void AppendLog(string line) { LogBox.AppendText(line + Environment.NewLine); LogBox.ScrollToEnd(); }
    private void Report(string message) { StatusText.Text = message; AppendLog(message); }
}
