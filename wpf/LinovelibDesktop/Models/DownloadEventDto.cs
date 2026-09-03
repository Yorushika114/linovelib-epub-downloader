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

public sealed record DownloadRequest(string NovelId, string Volumes, string Delay, string OutputPath);

/// <summary>书名搜索解析返回的单个候选（id + 标题）。</summary>
public sealed class ResolveResultDto
{
    public string Kind { get; init; } = "";
    public string Id { get; init; } = "";
    public string Title { get; init; } = "";
    public bool Exact { get; init; }

    /// <summary>供候选列表『吻合』列显示的文本。</summary>
    public string ExactText => Exact ? "书名吻合" : "";
}
