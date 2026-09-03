# WPF 浅色阅读工作台 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 将现有 WPF 下载窗口重构为浅色、清晰、面向任务进度的阅读工作台，同时保持现有下载桥接行为。

**Architecture:** 仅修改 WPF 呈现层。`MainWindow.xaml` 提供可复用的浅色控件、表格和状态徽标样式，并重排为标题、下载设置、任务概览、章节队列和折叠日志五个区块。`MainWindow.xaml.cs` 从既有下载事件派生任务完成、失败与等待数量，更新概览卡和日志摘要，不修改 Python 事件协议或 `DownloaderBridge`。

**Tech Stack:** .NET 8、WPF XAML/C#、pytest（静态 UI 回归检查）。

## Global Constraints

- 不修改 `main.py`、`wpf_bridge.py`、下载协议或爬虫访问策略。
- 不添加网络请求、外部图片、NuGet 包或绝对路径。
- 主文字只能位于实体浅色背景上；装饰只用本地渐变、星点和光晕。
- 保留搜索、候选选择、开始、安全取消、实时章节状态、进度和日志。
- .NET 8 WPF 必须在隔离输出目录中零警告构建。

---

### Task 1: 用回归测试锁定浅色工作台的视觉契约

**Files:**
- Create: `tests/test_wpf_visual_redesign.py`
- Modify: `wpf/LinovelibDesktop/MainWindow.xaml`
- Modify: `wpf/LinovelibDesktop/MainWindow.xaml.cs`

**Interfaces:**
- Consumes: 既有 `MainWindow` 命名控件与 `DownloadEventDto` 事件。
- Produces: 受测试约束的 `TaskSummaryCard`、`TaskOverviewText`、`LogToggleButton`、`LogPanel` 和状态徽标样式名。

- [ ] **Step 1: 写出失败的视觉契约测试**

```python
from pathlib import Path


ROOT = Path(__file__).parents[1]
XAML = (ROOT / "wpf" / "LinovelibDesktop" / "MainWindow.xaml").read_text(encoding="utf-8")
CODE = (ROOT / "wpf" / "LinovelibDesktop" / "MainWindow.xaml.cs").read_text(encoding="utf-8")


def test_wpf_uses_light_workspace_sections_and_a_collapsed_log_panel():
    for name in ("TaskSummaryCard", "TaskOverviewText", "LogToggleButton", "LogPanel"):
        assert f'x:Name="{name}"' in XAML
    assert 'Visibility="Collapsed"' in XAML.split('x:Name="LogPanel"', 1)[1].split('</Border>', 1)[0]
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
```

- [ ] **Step 2: 运行测试，确认它因重构尚未实现而失败**

Run: `python -m pytest tests/test_wpf_visual_redesign.py -q`

Expected: FAIL，缺少 `TaskSummaryCard`、状态徽标样式和 `UpdateTaskOverview`。

- [ ] **Step 3: 实现最小的 XAML 视觉结构与 C# 概览更新**

在 `MainWindow.xaml` 中：

```xml
<Border x:Name="TaskSummaryCard" Style="{StaticResource SurfaceCard}">
  <TextBlock x:Name="TaskOverviewText" Text="准备开始新的下载任务" />
</Border>
<Button x:Name="LogToggleButton" Click="LogToggleButton_Click" Content="展开日志" />
<Border x:Name="LogPanel" Visibility="Collapsed">
  <TextBox x:Name="LogBox" IsReadOnly="True" />
</Border>
```

在 `MainWindow.xaml.cs` 中，以 `_rows` 内状态计算概览和日志摘要：

```csharp
private void UpdateTaskOverview()
{
    var finished = _rows.Count(row => row.State == "已完成");
    var failed = _rows.Count(row => row.State == "失败");
    var running = _rows.Count(row => row.State == "下载中");
    TaskOverviewText.Text = $"{finished} 已完成 · {running} 下载中 · {failed} 失败";
    LogSummaryText.Text = string.IsNullOrWhiteSpace(_lastLogLine) ? "暂无运行日志" : _lastLogLine;
}
```

`ChapterRow.State` 使用 `DataGridTemplateColumn` 和 `ContentControl`，由 `State` 的 DataTrigger 分配 `WaitingBadge`、`RunningBadge`、`CompletedBadge` 或 `FailedBadge`。

- [ ] **Step 4: 运行视觉契约测试，确认通过**

Run: `python -m pytest tests/test_wpf_visual_redesign.py -q`

Expected: `3 passed`。

- [ ] **Step 5: 提交测试和视觉重构**

```powershell
git add tests/test_wpf_visual_redesign.py wpf/LinovelibDesktop/MainWindow.xaml wpf/LinovelibDesktop/MainWindow.xaml.cs
git commit -m "feat: redesign WPF as light workspace"
```

### Task 2: 完成下载工作台布局与可读性细节

**Files:**
- Modify: `wpf/LinovelibDesktop/MainWindow.xaml`
- Modify: `wpf/LinovelibDesktop/MainWindow.xaml.cs`
- Test: `tests/test_wpf_visual_redesign.py`

**Interfaces:**
- Consumes: Task 1 的命名卡片、日志控件与 `UpdateTaskOverview()`。
- Produces: 可折叠日志、卡片式输入区、无伪导航栏的主窗口和可视化章节状态。

- [ ] **Step 1: 扩展失败测试以保护日志切换行为**

```python
def test_wpf_exposes_log_toggle_handler_and_clear_status_copy():
    assert "private void LogToggleButton_Click" in CODE
    assert 'LogPanel.Visibility = Visibility.Visible' in CODE
    assert 'LogPanel.Visibility = Visibility.Collapsed' in CODE
    assert "已完成" in XAML and "下载中" in XAML and "失败" in XAML
```

- [ ] **Step 2: 运行新增测试，确认切换行为尚未实现时失败**

Run: `python -m pytest tests/test_wpf_visual_redesign.py::test_wpf_exposes_log_toggle_handler_and_clear_status_copy -q`

Expected: FAIL，未找到 `LogToggleButton_Click` 或其可见性切换。

- [ ] **Step 3: 实现日志切换并完成布局**

在 `MainWindow.xaml.cs` 中新增：

```csharp
private void LogToggleButton_Click(object sender, RoutedEventArgs e)
{
    var isOpening = LogPanel.Visibility != Visibility.Visible;
    LogPanel.Visibility = isOpening ? Visibility.Visible : Visibility.Collapsed;
    LogToggleButton.Content = isOpening ? "收起日志" : "展开日志";
}
```

在 XAML 中保留现有事件绑定，替换旧侧栏、默认 DataGrid 外观和直接暴露的日志框；使用 `SurfaceCard`、`FieldLabel`、`PrimaryButton`、`SecondaryButton`、`ChapterStatusBadge` 资源样式。保持 `NovelIdBox`、`VolumesBox`、`DelayBox`、`OutputBox`、`SearchButton`、`StartButton`、`CancelButton`、`ChapterGrid`、`CandidateList`、`Progress` 和 `StatusText` 的名称与点击/选择事件不变。

- [ ] **Step 4: 运行全套相关 Python 测试与隔离 WPF 构建**

Run:

```powershell
python -m pytest tests/test_wpf_visual_redesign.py tests/test_wpf_layout.py tests/test_wpf_log_dedup.py tests/test_wpf_resolve.py tests/test_wpf_volume_arguments.py tests/test_wpf_batch_launcher.py -q
$buildRoot = Join-Path ([System.IO.Path]::GetTempPath()) ('linovelib-wpf-ui-' + [guid]::NewGuid().ToString('N'))
dotnet build wpf/LinovelibDesktop/LinovelibDesktop.csproj "-p:BaseOutputPath=$buildRoot\\" --nologo --verbosity:minimal
```

Expected: 所有指定 pytest 通过，`dotnet build` 以代码 0 完成且无 warning/error。

- [ ] **Step 5: 提交完成后的 UI 代码**

```powershell
git add tests/test_wpf_visual_redesign.py wpf/LinovelibDesktop/MainWindow.xaml wpf/LinovelibDesktop/MainWindow.xaml.cs
git commit -m "feat: polish WPF task workspace"
```

### Task 3: 运行时验收和项目回归验证

**Files:**
- Modify: `README.md`（仅在启动或交互说明确实变化时）
- Test: 已跟踪的 `tests/*.py`

**Interfaces:**
- Consumes: 已完成的 WPF 视图、现有 `start_wpf_ui.bat`。
- Produces: 经过构建和实际启动检查的 UI 改动。

- [ ] **Step 1: 运行已跟踪 Python 测试，避免把用户未跟踪草稿纳入范围**

```powershell
$trackedTests = git ls-files 'tests/*.py'
python -m pytest -q $trackedTests
```

Expected: 退出码 0；未跟踪的 `tests/test_bilibili_bangumi_rank.py` 不会被执行。

- [ ] **Step 2: 用隔离构建启动一次 WPF 窗口并人工检查**

```powershell
$runRoot = Join-Path ([System.IO.Path]::GetTempPath()) ('linovelib-wpf-run-' + [guid]::NewGuid().ToString('N'))
dotnet build wpf/LinovelibDesktop/LinovelibDesktop.csproj "-p:BaseOutputPath=$runRoot\\" --nologo --verbosity:minimal
Start-Process (Join-Path $runRoot 'Debug\\net8.0-windows\\LinovelibDesktop.exe')
```

Inspect: 窗口可见；文字位于浅色实体卡片上；日志默认收起；状态徽标、进度卡和候选/章节表格没有遮挡或模糊。

- [ ] **Step 3: 提交任何必要的说明更新并核对工作树**

```powershell
git status --short
git log --oneline -3
```

Expected: 只有本任务的已提交文件和既有未跟踪 Bilibili 草稿；不添加、移动或删除后者。
