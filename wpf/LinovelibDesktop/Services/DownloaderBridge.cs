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
    private const string EventPrefix = "@@LINOVELIB_EVENT@@";
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

        return await RunDownloadAsync(startInfo, onEvent, onLog);
    }

    /// <summary>按漫画编号/卷号启动 WPF 漫画下载桥接进程（--comic / --vol / --delay / --out）。</summary>
    public async Task<int> StartComicAsync(ComicDownloadRequest request, Action<DownloadEventDto> onEvent, Action<string> onLog)
    {
        var startInfo = CreateBridgeStartInfo();
        startInfo.ArgumentList.Add("--comic"); startInfo.ArgumentList.Add(request.ComicId);
        startInfo.ArgumentList.Add("--vol"); startInfo.ArgumentList.Add(request.Volumes);
        startInfo.ArgumentList.Add("--delay"); startInfo.ArgumentList.Add(request.Delay);
        if (!string.IsNullOrWhiteSpace(request.OutputPath))
        {
            startInfo.ArgumentList.Add("--out"); startInfo.ArgumentList.Add(request.OutputPath);
        }
        return await RunDownloadAsync(startInfo, onEvent, onLog);
    }

    private async Task<int> RunDownloadAsync(ProcessStartInfo startInfo, Action<DownloadEventDto> onEvent, Action<string> onLog)
    {
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
        => await ResolveCoreAsync("--resolve", text);

    /// <summary>仅按书名解析漫画候选列表（不下载）；后续进入编号/卷数/下载。</summary>
    public async Task<List<ResolveResultDto>> ResolveComicAsync(string text)
        => await ResolveCoreAsync("--resolve-comic", text);

    private async Task<List<ResolveResultDto>> ResolveCoreAsync(string mode, string text)
        => await RunJsonLinesAsync(mode, text, "search_hit");

    /// <summary>只取小说卷列表（不下载），供 WPF 在下载前渲染卷选择表。</summary>
    public async Task<List<VolumeRow>> CatalogAsync(string nid)
        => await CatalogCoreAsync("--catalog", nid);

    /// <summary>只取漫画卷列表（不下载）。漫画目录需 Playwright，比小说侧慢得多。</summary>
    public async Task<List<VolumeRow>> CatalogComicAsync(string cid)
        => await CatalogCoreAsync("--comic-catalog", cid);

    private async Task<List<VolumeRow>> CatalogCoreAsync(string mode, string id)
    {
        var items = await RunJsonLinesAsync(mode, id, "volume", errorKinds: new[] { "catalog_error" });
        return items
            .Select(r => new VolumeRow { Index = r.Index, Title = r.Title, ChapterCount = r.Chapters })
            .ToList();
    }

    /// <summary>
    /// 跑一次「读裸 JSON 行」的桥接子进程（搜索 / 取目录），只保留 <paramref name="keepKind"/> 的行。
    /// </summary>
    /// <remarks>
    /// 搜索与取目录共用这一份实现，是为了让超时语义只有一处：上一轮刚修好「超时被当成
    /// 未找到」的误报，若取目录另写一份「读完已收集的行就返回」的循环，同样的误报会
    /// 以「这部作品 0 卷」的形式复发。
    /// </remarks>
    private async Task<List<ResolveResultDto>> RunJsonLinesAsync(
        string mode, string text, string keepKind, string[]? errorKinds = null)
    {
        var startInfo = CreateBridgeStartInfo();
        startInfo.ArgumentList.Add(mode);
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
                    // 超时**不能**当成「没搜到」返回空列表：调用方会把空列表渲染成
                    // 「未找到名为『X』的小说，请改用编号」，用户于是得到一本并不存在的
                    // 结论，也不知道该重试。冷机器上这条路径其实是常态——首次启动 Edge
                    // 要建 profile、首次访问站点无任何缓存/cookie，暖机 + 搜索 + 2.5s
                    // 静置很容易越过 60s；而开发机 Edge 已暖、cookie 已在，秒回，故这个
                    // 误报只在目标机器上出现。抛出明确异常，让界面如实提示「超时，请重试」。
                    throw new TimeoutException(
                        $"搜索超时（{ResolveTimeoutSeconds} 秒）。首次使用或网络较慢时，" +
                        "浏览器需要更长时间启动，请稍后重试；仍不行可改用编号。");
                }
                var line = await readLine;
                if (line is null) break; // stdout EOF
                try
                {
                    var item = JsonSerializer.Deserialize<ResolveResultDto>(line, JsonOptions);
                    if (item is null) continue;
                    // 搜索/取目录方主动上报错误（如漫画 Cloudflare 限速、目录页被封）：
                    // 交由界面呈现真实原因，不要静默降级成「没有结果」。
                    if (item.Kind == "search_error" || errorKinds?.Contains(item.Kind) == true)
                    {
                        throw new InvalidOperationException(item.Message);
                    }
                    results.Add(item);
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
        return results.Where(r => r.Kind == keepKind).ToList();
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
        // 只有显式协议前缀后的 JSON 才是下载事件；普通日志即使以 { 开头也保持为日志。
        var payload = line.TrimStart('\uFEFF').TrimStart();
        if (!payload.StartsWith(EventPrefix, StringComparison.Ordinal))
        {
            onLog(line);
            return;
        }

        payload = payload[EventPrefix.Length..];

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
