using System.Diagnostics;
using System.IO;
using System.Linq;
using System.Text.Json;
using LinovelibDesktop.Models;

namespace LinovelibDesktop.Services;

public sealed class DownloaderBridge
{
    private Process? _process;
    private static readonly JsonSerializerOptions JsonOptions = new() { PropertyNameCaseInsensitive = true };

    public async Task<int> StartAsync(DownloadRequest request, Action<DownloadEventDto> onEvent, Action<string> onLog)
    {
        var startInfo = CreateBridgeStartInfo();
        startInfo.ArgumentList.Add("--novel"); startInfo.ArgumentList.Add(request.NovelId);
        if (string.Equals(request.Volumes, "all", StringComparison.OrdinalIgnoreCase))
        {
            startInfo.ArgumentList.Add("--vol"); startInfo.ArgumentList.Add("all");
        }
        else
        {
            startInfo.ArgumentList.Add("--volumes"); startInfo.ArgumentList.Add(request.Volumes);
        }
        startInfo.ArgumentList.Add("--delay"); startInfo.ArgumentList.Add(request.Delay);
        if (!string.IsNullOrWhiteSpace(request.OutputPath))
        {
            startInfo.ArgumentList.Add("--out"); startInfo.ArgumentList.Add(request.OutputPath);
        }

        _process = new Process { StartInfo = startInfo };
        if (!_process.Start()) throw new InvalidOperationException("无法启动 Python 下载桥接进程。");

        var stdoutTask = ReadEventsAsync(_process, onEvent, onLog);
        var stderrTask = ReadErrorsAsync(_process, onLog);
        await Task.WhenAll(stdoutTask, stderrTask, _process.WaitForExitAsync());
        var exitCode = _process.ExitCode;
        _process.Dispose();
        _process = null;
        return exitCode;
    }

    public void RequestCancel()
    {
        if (_process is { HasExited: false })
        {
            _process.StandardInput.WriteLine("cancel");
            _process.StandardInput.Flush();
        }
    }

    /// <summary>仅按书名解析候选列表（不下载），供 WPF 先做书名筛选，再进入卷数/下载。</summary>
    public async Task<List<ResolveResultDto>> ResolveAsync(string text)
    {
        var startInfo = CreateBridgeStartInfo();
        startInfo.ArgumentList.Add("--resolve");
        startInfo.ArgumentList.Add(text);

        using var process = new Process { StartInfo = startInfo };
        if (!process.Start()) throw new InvalidOperationException("无法启动 Python 搜索桥接进程。");

        var stderrTask = process.StandardError.ReadToEndAsync();
        var results = new List<ResolveResultDto>();
        while (await process.StandardOutput.ReadLineAsync() is { } line)
        {
            try
            {
                var item = JsonSerializer.Deserialize<ResolveResultDto>(line, JsonOptions);
                if (item is not null) results.Add(item);
            }
            catch (JsonException) { }
        }
        await process.WaitForExitAsync();
        await stderrTask;
        return results.Where(r => r.Kind == "search_hit").ToList();
    }

    private static ProcessStartInfo CreateBridgeStartInfo()
    {
        var root = ProjectPaths.FindRoot();
        var startInfo = new ProcessStartInfo(ProjectPaths.FindPython(root))
        {
            WorkingDirectory = root,
            UseShellExecute = false,
            RedirectStandardInput = true,
            RedirectStandardOutput = true,
            RedirectStandardError = true,
            CreateNoWindow = true,
        };
        startInfo.ArgumentList.Add(Path.Combine(root, "wpf_bridge.py"));
        return startInfo;
    }

    private static async Task ReadEventsAsync(Process process, Action<DownloadEventDto> onEvent, Action<string> onLog)
    {
        while (await process.StandardOutput.ReadLineAsync() is { } line)
        {
            try
            {
                var item = JsonSerializer.Deserialize<DownloadEventDto>(line, JsonOptions);
                if (item is not null) onEvent(item);
            }
            catch (JsonException) { onLog(line); }
        }
    }

    private static async Task ReadErrorsAsync(Process process, Action<string> onLog)
    {
        while (await process.StandardError.ReadLineAsync() is { } line) onLog(line);
    }
}
