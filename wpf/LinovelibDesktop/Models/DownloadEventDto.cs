using System.ComponentModel;
using System.Runtime.CompilerServices;

namespace LinovelibDesktop.Models;

public sealed class DownloadEventDto
{
    public string Kind { get; init; } = "";
    public int? VolumeIndex { get; init; }
    public string VolumeTitle { get; init; } = "";
    public string ChapterId { get; init; } = "";
    public string ChapterTitle { get; init; } = "";
    public int Completed { get; init; }
    public int Total { get; init; }
    public string Message { get; init; } = "";
    public string OutputPath { get; init; } = "";
}

public sealed class ChapterRow : INotifyPropertyChanged
{
    private string _state = "等待中";
    private string _detail = "";

    public required string Id { get; init; }
    public string Volume { get; init; } = "";
    public string Chapter { get; init; } = "";
    public string State { get => _state; set => SetField(ref _state, value); }
    public string Detail { get => _detail; set => SetField(ref _detail, value); }
    public event PropertyChangedEventHandler? PropertyChanged;

    private void SetField(ref string field, string value, [CallerMemberName] string? name = null)
    {
        if (field == value) return;
        field = value;
        PropertyChanged?.Invoke(this, new PropertyChangedEventArgs(name));
    }
}

/// <summary>卷选择表的一行：只到卷级，不展开章节（用户明确「具体章节不用显示出来」）。</summary>
public sealed class VolumeRow : INotifyPropertyChanged
{
    private bool _isSelected;

    /// <summary>卷序，从 1 起——它就是要写进「卷号」框的那个数字。</summary>
    public int Index { get; init; }
    public string Title { get; init; } = "";

    /// <summary>卷内章节数。两侧模型都没有独立计数字段，取值只能是 len(chapters)。</summary>
    public int ChapterCount { get; init; }

    public bool IsSelected
    {
        get => _isSelected;
        set
        {
            if (_isSelected == value) return;
            _isSelected = value;
            PropertyChanged?.Invoke(this, new PropertyChangedEventArgs(nameof(IsSelected)));
        }
    }

    public event PropertyChangedEventHandler? PropertyChanged;
}

public sealed record DownloadRequest(string NovelId, string Volumes, string Delay, string OutputPath);

/// <summary>漫画下载请求：字段语义与 DownloadRequest 不同（ComicId / --vol / 输出为目录）。</summary>
public sealed record ComicDownloadRequest(string ComicId, string Volumes, string Delay, string OutputPath);

/// <summary>书名搜索解析返回的单个候选（id + 标题）。</summary>
public sealed class ResolveResultDto
{
    public string Kind { get; init; } = "";
    public string Id { get; init; } = "";
    public string Title { get; init; } = "";
    public bool Exact { get; init; }

    /// <summary>取回搜索过程的错误原因（如 Cloudflare 限速），无错误时为空。</summary>
    public string Message { get; init; } = "";

    // —— 以下三项目前只被取目录（--catalog / --comic-catalog）的输出填充。
    // 与搜索共用同一个反序列化类型，是为了让「读裸 JSON 行」的子进程循环只有一份。
    /// <summary>卷序（仅 kind=volume 行有值）。</summary>
    public int Index { get; init; }

    /// <summary>卷内章节数（仅 kind=volume 行有值）。</summary>
    public int Chapters { get; init; }

    /// <summary>卷的 vid，小说侧目录页定位用（仅 kind=volume 行有值）。</summary>
    public string Vid { get; init; } = "";

    /// <summary>供候选列表『吻合』列显示的文本。</summary>
    public string ExactText => Exact ? "书名吻合" : "";
}
