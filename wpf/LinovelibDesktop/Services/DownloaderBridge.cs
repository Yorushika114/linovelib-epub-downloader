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
    private const int ResolveTimeoutSeconds = 60;

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
        var stderrTask = ReadErrorsAsync(_process, onEvent, onLog);
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

        // 给整个解析过程加硬性时限：站内搜索 / 站外引擎（Bing、DDG）或 Edge 关闭偶发挂起时，
        // 不能让 SearchButton 一直被禁用导致「无法继续搜索」。超时则终止整棵进程树并返回空。
        var deadline = Task.Delay(TimeSpan.FromSeconds(ResolveTimeoutSeconds));
        var stderrTask = process.StandardError.ReadToEndAsync();
        var results = new List<ResolveResultDto>();
        try
        {
            // 读完输出行直到 stdout EOF；一旦超时立刻终结进程，避免界面卡死。
            while (true)
            {
                var readLine = process.StandardOutput.ReadLineAsync();
                var finished = await Task.WhenAny(readLine, deadline);
                if (finished == deadline)
                {
                    TryKill(process);
                    return results.Where(r => r.Kind == "search_hit").ToList();
                }
                var line = await readLine;
                if (line is null) break; // stdout EOF
                try
                {
                    var item = JsonSerializer.Deserialize<ResolveResultDto>(line, JsonOptions);
                    if (item is not null) results.Add(item);
                }
                catch (JsonException) { }
            }
            // stdout 已 EOF，但仍可能卡在进程退出（如 Edge 关闭）。同样交给时限收尾。
            await Task.WhenAny(process.WaitForExitAsync(), deadline);
            if (!process.HasExited) TryKill(process);
            await stderrTask;
        }
        catch
        {
            TryKill(process);
            throw;
        }
        return results.Where(r => r.Kind == "search_hit").ToList();
    }

    /// <summary>把还活着的解析进程连子进程一起杀掉（Playwright/Edge 等子进程一并结束）。</summary>
    private static void TryKill(Process process)
    {
        try
        {
            if (!process.HasExited) process.Kill(entireProcessTree: true);
        }
        catch
        {
            // 进程已退出或无权结束：忽略，界面仍会在 finally 里恢复搜索按钮。
        }
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
            RouteBridgeLine(line, onEvent, onLog);
        }
    }

    private static async Task ReadErrorsAsync(Process process, Action<DownloadEventDto> onEvent, Action<string> onLog)
    {
        while (await process.StandardError.ReadLineAsync() is { } line)
        {
            RouteBridgeLine(line, onEvent, onLog);
        }
    }

    private static void RouteBridgeLine(string line, Action<DownloadEventDto> onEvent, Action<string> onLog)
    {
        // Python 在某些宿主下可能把协议行写到 stderr，或在首行带 UTF-8 BOM。
        // 两个流统一先按事件解析，避免把 JSON 协议直接泄露到用户日志区。
        var payload = line.TrimStart('\uFEFF');
        if (!payload.TrimStart().StartsWith("{", StringComparison.Ordinal))
        {
            onLog(line);
            return;
        }

        try
        {
            var item = JsonSerializer.Deserialize<DownloadEventDto>(payload, JsonOptions);
            if (item is not null && !string.IsNullOrWhiteSpace(item.Kind))
            {
                onEvent(item);
                return;
            }
        }
        catch (JsonException)
        {
            onLog("下载进度事件格式异常，已忽略。");
            return;
        }

        onLog("下载进度事件格式异常，已忽略。");
    }
}
