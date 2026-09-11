"""退出时清理缓存：Python 侧 clear_cache() 的行为，以及 WPF 关窗路径确实接上了它。

为什么要有这组测试：缓存目录 _tmp_dl 与 download/ 下的成品只隔一层目录，且整个目录都在
.gitignore 里、删掉不可恢复。清理范围一旦写歪，要么毁用户已经下好的书，要么把开发期手
工放进去的文件一起带走。故安全边界必须钉死。
"""

import importlib
from pathlib import Path


ROOT = Path(__file__).parents[1]
PATHS = importlib.import_module("linovelib.paths")

HTML = "<html></html>"

# 目录里**不是**本程序产出的东西：_tmp_dl 整个在 .gitignore 里，这些删了不可恢复。
# 清理必须原样留下它们（用户 2026-09-11 明确选了「只删 app 产物」这个范围）。
FOREIGN_FILES = ("probe8.py", "comic_run1.log", "search_dump.html")


def _seed(cache_dir, images=3, payload=b"x" * 128):
    """铺一份像真缓存的目录：本程序产出的三类 + 开发期手工放进去的若干文件。"""
    (cache_dir / "images").mkdir(parents=True, exist_ok=True)
    for i in range(images):
        (cache_dir / "images" / f"{i}_1.jpg").write_bytes(payload)
    (cache_dir / "vol_1_page.html").write_text(HTML, encoding="utf-8")
    (cache_dir / "tmpab12cd.epub.tmp").write_bytes(b"half-built")
    for name in FOREIGN_FILES:
        (cache_dir / name).write_text("dev leftover", encoding="utf-8")


def _survivors(cache_dir):
    return sorted(p.name for p in cache_dir.iterdir())


# ------------------------------------------------------------------ clear_cache

def test_clear_cache_removes_app_produced_cache(monkeypatch, tmp_path):
    cache = tmp_path / "_tmp_dl"
    _seed(cache)
    monkeypatch.setattr(PATHS, "CACHE_DIR", cache)

    files, total = PATHS.clear_cache()

    assert not (cache / "images").exists()
    assert not (cache / "vol_1_page.html").exists()
    assert not (cache / "tmpab12cd.epub.tmp").exists()
    assert files == 5
    assert total == 3 * 128 + len(HTML) + len(b"half-built")


def test_clear_cache_keeps_files_the_app_never_wrote(monkeypatch, tmp_path):
    """开发期手工放进 _tmp_dl 的侦察脚本/日志不能被清理带走——它们在 .gitignore 里，删了不可恢复。"""
    cache = tmp_path / "_tmp_dl"
    _seed(cache)
    monkeypatch.setattr(PATHS, "CACHE_DIR", cache)

    PATHS.clear_cache()

    assert _survivors(cache) == sorted(FOREIGN_FILES)


def test_clear_cache_drops_the_directory_once_it_is_empty(monkeypatch, tmp_path):
    """缓存是目录里的全部内容时，目录本身也一并收掉（不留空壳）。"""
    cache = tmp_path / "_tmp_dl"
    (cache / "images").mkdir(parents=True)
    (cache / "images" / "1_1.jpg").write_bytes(b"x")
    monkeypatch.setattr(PATHS, "CACHE_DIR", cache)

    PATHS.clear_cache()

    assert not cache.exists()


def test_clear_cache_is_a_noop_when_cache_is_absent(monkeypatch, tmp_path):
    monkeypatch.setattr(PATHS, "CACHE_DIR", tmp_path / "missing")

    assert PATHS.clear_cache() == (0, 0)


def test_clear_cache_never_touches_sibling_downloads(monkeypatch, tmp_path):
    """清理的安全边界：缓存清干净，download/ 下的成品必须原样在。"""
    cache = tmp_path / "_tmp_dl"
    book = tmp_path / "download" / "小说" / "某书" / "某书 第1卷.epub"
    _seed(cache)
    book.parent.mkdir(parents=True)
    book.write_bytes(b"EPUB")

    monkeypatch.setattr(PATHS, "CACHE_DIR", cache)
    PATHS.clear_cache()

    assert book.read_bytes() == b"EPUB"


# ------------------------------------------------- cache_usage 与清理口径一致

def test_cache_usage_counts_only_app_produced_cache(monkeypatch, tmp_path):
    """统计口径必须与清理口径一致：报一个数、实际清掉另一个数是最难查的那种不一致。"""
    cache = tmp_path / "_tmp_dl"
    _seed(cache, images=2, payload=b"y" * 10)
    monkeypatch.setattr(PATHS, "CACHE_DIR", cache)

    files, total = PATHS.cache_usage()

    assert files == 4
    assert total == 2 * 10 + len(HTML) + len(b"half-built")


def test_cache_usage_reports_zero_for_absent_cache(monkeypatch, tmp_path):
    monkeypatch.setattr(PATHS, "CACHE_DIR", tmp_path / "missing")

    assert PATHS.cache_usage() == (0, 0)


def test_cache_usage_matches_what_clear_cache_removes(monkeypatch, tmp_path):
    cache = tmp_path / "_tmp_dl"
    _seed(cache)
    monkeypatch.setattr(PATHS, "CACHE_DIR", cache)

    assert PATHS.cache_usage() == PATHS.clear_cache()


# ------------------------------------------------------- 跨语言：两处清单必须一致

def _project_paths_source():
    return (ROOT / "wpf" / "LinovelibDesktop" / "Services"
            / "ProjectPaths.cs").read_text(encoding="utf-8")


def _cleaner_source():
    return (ROOT / "wpf" / "LinovelibDesktop" / "Services"
            / "CacheCleaner.cs").read_text(encoding="utf-8")


def test_csharp_cache_dir_name_matches_python():
    """C# 的 CacheDirectoryName 与 Python 的 CACHE_DIR.name 必须字面一致。

    两边各写各的，一旦只改一边，退出清理就会去删一个不存在的目录（静默无效），或者
    更糟——删到别的东西上。
    """
    assert f'CacheDirectoryName = "{PATHS.CACHE_DIR.name}"' in _project_paths_source()


def test_csharp_cache_scope_matches_python():
    """C# 的三类清理清单要与 Python 侧字面一致，否则两边删的东西不一样。"""
    source = _cleaner_source()

    assert f'ImageCacheDirName = "{PATHS.IMAGE_CACHE_DIR_NAME}"' in source
    assert f'VolumePagePattern = "{PATHS.VOLUME_PAGE_GLOB}"' in source
    assert f'TempEpubPattern = "{PATHS.TEMP_EPUB_GLOB}"' in source


# ------------------------------------------------------------ WPF 关窗路径接上了

def _main_window_source():
    return (ROOT / "wpf" / "LinovelibDesktop" / "MainWindow.xaml.cs").read_text(encoding="utf-8")


def _bridge_source():
    return (ROOT / "wpf" / "LinovelibDesktop" / "Services"
            / "DownloaderBridge.cs").read_text(encoding="utf-8")


def test_main_window_cleans_cache_on_close():
    source = _main_window_source()

    assert "protected override async void OnClosing(CancelEventArgs e)" in source
    assert "CacheCleaner.Clean(" in source


def test_close_path_stops_the_download_before_cleaning():
    """顺序不能反：先停进程再删目录，否则 Python 还在往被删的目录里写。"""
    source = _main_window_source()

    assert source.index("await _bridge.StopAsync(") < source.index("CacheCleaner.Clean(")


def test_close_path_cleans_off_the_ui_thread():
    """清理要放到线程池：实测 900 MB / 6800 个文件耗时 3~4 秒，同步做会把 UI 线程卡住，
    连「正在清理」那行状态文字都刷不出来，用户看到的就是关窗卡死。"""
    source = _main_window_source()

    assert "await Task.Run(() => CacheCleaner.Clean(" in source


def test_main_window_guards_against_reentrant_close():
    """async void OnClosing 里 e.Cancel = true 之后还会再进来一次，必须只收尾一遍。"""
    source = _main_window_source()

    assert "_shutdownStarted" in source
    assert "_shutdownDone" in source
    assert "e.Cancel = true;" in source


def test_main_window_confirms_before_killing_a_running_download():
    source = _main_window_source()

    assert "_bridge.IsRunning" in source
    assert "MessageBox.Show(" in source


def test_bridge_exposes_running_state_and_stop():
    source = _bridge_source()

    assert "public bool IsRunning" in source
    assert "public async Task StopAsync(TimeSpan gracefulTimeout)" in source


def test_bridge_stop_prefers_graceful_cancel_before_killing():
    """先请求章节边界安全取消，超时才杀进程树——避免砍在 EPUB 合成中途留下半成品。"""
    source = _bridge_source()

    assert 'WriteLine("cancel")' in source
    assert "Kill(entireProcessTree: true)" in source
    assert source.index('WriteLine("cancel")') < source.index("Kill(entireProcessTree: true)")


def test_cache_cleaner_is_best_effort():
    """清理失败只能吞掉：文件被占用是常态，不该把异常抛到关窗流程上。"""
    source = _cleaner_source()

    assert "throw" not in source
    assert "catch (Exception)" in source


def test_bridge_isrunning_tolerates_a_never_started_process():
    """IsRunning 必须容忍「构造过但从未启动 / 已 Dispose」的 Process。

    Process.HasExited 在这两种状态下会抛 InvalidOperationException（No process is
    associated with this object）。IsRunning 是从 async void 的 OnClosing 里读的，异常
    经 DispatcherSynchronizationContext 会变成未处理的调度器异常——关窗流程连同后面的
    「清理缓存」整段消失。故它必须走带保护的辅助方法，而不是裸的模式匹配。
    """
    source = _bridge_source()

    assert "_process is { HasExited: false }" not in source, (
        "IsRunning/RequestCancel 仍在裸读 HasExited —— 从未启动或已 Dispose 的进程会抛。"
    )
    assert "private static bool IsAlive(Process? process)" in source
    assert "public bool IsRunning => IsAlive(_process);" in source


def test_bridge_clears_the_field_when_start_fails_or_the_run_ends():
    """Start 抛异常时留下的那个 Process 必须清出字段并释放。

    否则 _process 一直指着一个从未启动的进程，IsRunning / RequestCancel 读它即抛；
    正常运行结束时也要收尾，否则字段指着已死的进程。故收尾走 finally。
    """
    source = _bridge_source()
    run = source.split("private async Task<int> RunDownloadAsync", 1)[1].split("\n    }", 1)[0]

    assert "finally" in run, "收尾没走 finally：抛异常时字段会残留。"
    assert "ReferenceEquals(_process, process)" in run
    assert "_process = null;" in run
    assert "process.Dispose();" in run


def test_bridge_kills_the_resolve_process_on_close():
    """搜索 / 取目录同样会拉起 Python + Edge，关窗时不能把它们留成孤儿。

    留着不会写坏任何文件，但会白占一个浏览器实例，最长要到解析超时（60 秒）才自己退。
    """
    source = _bridge_source()

    assert "private Process? _resolveProcess;" in source, "解析进程没有登记到字段。"
    assert "_resolveProcess = process;" in source, "RunJsonLinesAsync 没有登记解析进程。"

    stop = source.split("public async Task StopAsync", 1)[1].split("\n    }", 1)[0]
    assert "TryKill(_resolveProcess);" in stop, "StopAsync 没有收掉搜索/取目录进程。"


def test_main_window_guards_reentry_before_prompting():
    """收尾那 4 秒里再点一次 X，不该被再问一遍（第一次已经答过「确定」了）。"""
    source = _main_window_source()

    assert source.index("if (_shutdownStarted)") < source.index("MessageBox.Show("), (
        "重入检查排在询问之后——收尾期间每点一次 X 都会多弹一个对话框。"
    )


def test_main_window_disables_itself_during_shutdown():
    """收尾期间窗口必须禁用。

    清理要 3~4 秒，这期间窗口仍然活着；不禁用的话用户还能再点一次「开始下载」，那个新起的
    桥接进程会被收尾末尾的 Close() 抛下变成孤儿——正是本次要修掉的那个问题。
    """
    source = _main_window_source()
    body = source.split("protected override async void OnClosing", 1)[1]

    assert "IsEnabled = false;" in body, "收尾期间窗口仍可交互。"
    assert body.index("_shutdownStarted = true;") < body.index("IsEnabled = false;")
