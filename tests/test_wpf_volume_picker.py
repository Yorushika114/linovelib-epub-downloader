"""选定作品后列出卷、按卷下载（单选/多选/全选）的契约。

背景：WPF 的「卷号」原先是个裸文本框（默认 all）。用户选定小说/漫画后看不到
这部作品有哪些卷，只能盲填或先上网站自己数。本次在「章节队列」面板增加卷列表：
下载前显示卷（只到卷级，不展开章节），点行内「下载」只下该卷；进入多选后可勾选
若干卷点「下载选中」，也可「全选」。

关键约束（侦察结论，决定了实现形态）：
- 两侧都没有「只取目录」的通路——resolve 只返回 id/title/exact，卷列表必须单独取。
- 小说目录页用纯 requests 即可（不需要浏览器）；漫画必须 Playwright + 暖机 msedge。
- 卷的章节数只有 len(vol.chapters)，模型里没有独立计数字段。
- 测试把 DataGrid 数量钉死在 4（test_wpf_resolve.py:65），**不能新增第 5 个**，
  故卷列表必须复用现有 grid 而非新建。
- 自动选中（唯一命中/精确吻合）绕过 SelectionChanged，拉目录必须从那些路径也调用。
"""

from pathlib import Path
from types import SimpleNamespace

import wpf_bridge

ROOT = Path(__file__).parents[1]


def _vol(title, n_chapters, vid="", index=None):
    chapters = [SimpleNamespace(id=f"c{i}", title=f"第{i}章") for i in range(n_chapters)]
    d = {"title": title, "chapters": chapters, "vid": vid}
    if index is not None:
        d["index"] = index
    return SimpleNamespace(**d)


class FakeNovelFetcher:
    """只实现取目录页所需的 get_html，避免测试打网络。"""

    def __init__(self, html=""):
        self.html = html
        self.requested: list[str] = []

    def get_html(self, url, **kw):
        self.requested.append(url)
        return self.html


def test_novel_catalog_json_returns_volume_rows(monkeypatch):
    """小说的卷列表：每卷一行，末行 catalog_done 汇总。"""
    volumes = [_vol("第一卷 序章", 8, vid="1"), _vol("第二卷 转", 10, vid="2")]
    monkeypatch.setattr("linovelib.catalog.parse_catalog", lambda html, nid: volumes)

    rows = wpf_bridge.novel_catalog_json("3095", fetcher=FakeNovelFetcher("<html></html>"))

    vols = [r for r in rows if r["kind"] == "volume"]
    assert [v["title"] for v in vols] == ["第一卷 序章", "第二卷 转"]
    # 卷序 1 起：下载时卷号框填的就是它，不能是 0 起。
    assert [v["index"] for v in vols] == [1, 2]
    # 章数只能来自 len(chapters)——模型里没有独立计数字段。
    assert [v["chapters"] for v in vols] == [8, 10]
    assert rows[-1] == {"kind": "catalog_done", "total": 2}


def test_novel_catalog_fetches_catalog_page_without_browser():
    """小说取目录只发一次页面请求，不需要浏览器（成本差异的关键）。"""
    fetcher = FakeNovelFetcher("<html></html>")
    import linovelib.catalog as catalog_mod
    original = catalog_mod.parse_catalog
    catalog_mod.parse_catalog = lambda html, nid: []
    try:
        wpf_bridge.novel_catalog_json("3095", fetcher=fetcher)
    finally:
        catalog_mod.parse_catalog = original

    assert len(fetcher.requested) == 1, f"应只请求一次目录页，实际 {fetcher.requested}"
    assert "/novel/3095/catalog" in fetcher.requested[0]


def test_comic_catalog_json_returns_volume_rows():
    """漫画的卷列表：编号取 vol.index（漫画侧 Volume 自带 index）。"""
    comic = SimpleNamespace(
        id=368, title="某漫", author="某作者",
        volumes=[_vol("第1卷", 5, index=1), _vol("第2卷", 7, index=2)],
    )
    fetcher = SimpleNamespace(get_catalog=lambda cid: comic)

    rows = wpf_bridge.comic_catalog_json("368", fetcher=fetcher)

    vols = [r for r in rows if r["kind"] == "volume"]
    assert [v["index"] for v in vols] == [1, 2]
    assert [v["chapters"] for v in vols] == [5, 7]
    assert rows[-1]["kind"] == "catalog_done"


def test_volume_row_fields_match_the_csharp_dto():
    """卷行的字段名必须与 C# 侧 ResolveResultDto 的属性一一对应。

    两侧是松耦合的 JSON 契约（C# 按属性名反序列化，拼错不会编译报错，只会静默变成
    0/空串）。这里钉死字段名，避免哪天改了 Python 侧键名而界面「卷号全是 0」。
    """
    import json
    import subprocess
    import sys

    proc = subprocess.run(
        [sys.executable, str(ROOT / "wpf_bridge.py"), "--catalog", "3095"],
        capture_output=True, timeout=300, cwd=str(ROOT))
    # 桥接 stdout 是系统区域编码（中文 Windows = GBK），与搜索通路一致；
    # 若这里改成 utf-8 解码会 UnicodeDecodeError，那才是真回归。
    rows = [json.loads(line) for line in proc.stdout.decode("gbk").splitlines() if line.strip()]
    volumes = [r for r in rows if r["kind"] == "volume"]
    assert volumes, "未取到任何卷行（网络或站点结构变化）"

    required = {"kind", "index", "title", "chapters", "vid"}
    assert required <= set(volumes[0]), (
        f"卷行缺少字段：{sorted(required - set(volumes[0]))}——"
        "C# 侧 ResolveResultDto 按这些名字反序列化。")
    # 卷号 1 起且连续：它就是要写进「卷号」框的数字。
    assert [v["index"] for v in volumes] == list(range(1, len(volumes) + 1))
    assert all(isinstance(v["chapters"], int) and v["chapters"] > 0 for v in volumes), (
        "章数必须是正整数（原样来自 len(vol.chapters)）。")
    assert rows[-1]["kind"] == "catalog_done" and rows[-1]["total"] == len(volumes)


def test_bridge_dispatches_catalog_modes():
    """桥接必须能只取目录而不下载（两侧各一个模式）。

    没有这条通路就只能先启动下载才知道有哪些卷——本功能的前提。
    """
    src = (ROOT / "wpf_bridge.py").read_text(encoding="utf-8")
    assert '"--catalog"' in src, "桥接缺少小说目录模式。"
    assert '"--comic-catalog"' in src, "桥接缺少漫画目录模式。"
    assert "novel_catalog_json" in src and "comic_catalog_json" in src


def test_catalog_errors_are_reported_not_silently_dropped():
    """取目录失败必须报错，不能静默返回空列表（否则界面显示「0 卷」误导用户）。"""
    src = (ROOT / "wpf_bridge.py").read_text(encoding="utf-8")
    assert "catalog_error" in src


def test_wpf_grid_count_unchanged():
    """卷列表复用现有 grid，不得新增第 5 个 DataGrid。

    test_wpf_resolve.py:65 把 PreviewMouseWheel 计数钉死在 4；新增 grid 会破坏它，
    也会让「下载前卷列表 / 下载中章节进度」的两用设计变成两套并行表格。
    """
    xaml = (ROOT / "wpf" / "LinovelibDesktop" / "MainWindow.xaml").read_text(encoding="utf-8")
    assert xaml.count('PreviewMouseWheel="DataGrid_SmoothWheel"') == 4


def test_volume_columns_live_on_existing_chapter_grids():
    """卷列必须挂在 ChapterGrid / ComicChapterGrid 上（复用而非新建）。"""
    xaml = (ROOT / "wpf" / "LinovelibDesktop" / "MainWindow.xaml").read_text(encoding="utf-8")
    for name in ("ChapterGrid", "ComicChapterGrid"):
        seg = xaml.split(f'x:Name="{name}"', 1)[1].split("</DataGrid>", 1)[0]
        assert "IsSelected" in seg, f"{name} 缺少卷选择列——多选无从挂载。"
        assert "VolumeDownloadButton_Click" in seg, f"{name} 缺少行内「下载」按钮。"
        assert "Visibility=\"Collapsed\"" in seg, (
            f"{name} 的卷列默认应隐藏（默认是章节进度模式）。"
        )


def test_multi_select_and_select_all_controls_exist():
    """多选开关、全选、下载选中三个控件都要在，且都接上处理器。"""
    xaml = (ROOT / "wpf" / "LinovelibDesktop" / "MainWindow.xaml").read_text(encoding="utf-8")
    for name in ("MultiSelectToggle", "SelectAllButton", "DownloadSelectedButton"):
        assert f'x:Name="{name}"' in xaml, f"缺少 {name}。"
    for handler in ("MultiSelectToggle_Click", "SelectAllButton_Click",
                    "DownloadSelectedButton_Click"):
        assert handler in xaml, f"控件未接上 {handler}。"


def test_comic_side_has_parallel_controls():
    """小说与漫画是两套并行实现——任一侧缺失都会让该模式功能不全。"""
    xaml = (ROOT / "wpf" / "LinovelibDesktop" / "MainWindow.xaml").read_text(encoding="utf-8")
    for name in ("ComicMultiSelectToggle", "ComicSelectAllButton",
                 "ComicDownloadSelectedButton"):
        assert f'x:Name="{name}"' in xaml, f"漫画侧缺少 {name}。"


def test_volume_selection_syncs_into_volume_box():
    """勾选卷后要把卷号写回「卷号」输入框（勾选优先 + 自动同步）。

    用户据此能直观看到最终下载范围，也能继续手改（如 1-4）。
    """
    cs = (ROOT / "wpf" / "LinovelibDesktop" / "MainWindow.xaml.cs").read_text(encoding="utf-8")
    assert "SyncVolumesToBox" in cs, "缺少卷选择→卷号框的同步逻辑。"
    # 同步的产物必须是 CLI 认得的逗号分隔卷号（main.py:_expand_volume_spec）。
    assert 'string.Join(",",' in cs


def test_auto_select_paths_load_volumes():
    """唯一命中与精确吻合也要列卷。

    这两条路径直接设 NovelIdBox/ComicIdBox 并 return，不经过 SelectionChanged，
    若只在选中处理器里拉目录，「搜索到唯一一本书」这个最常见的情况反而看不到卷。
    """
    cs = (ROOT / "wpf" / "LinovelibDesktop" / "MainWindow.xaml.cs").read_text(encoding="utf-8")
    assert cs.count("LoadNovelVolumesAsync") >= 3, (
        "小说侧拉目录的调用点不足 3 处（候选选中 + 唯一命中 + 精确吻合）。"
    )
    assert cs.count("LoadComicVolumesAsync") >= 3, (
        "漫画侧拉目录的调用点不足 3 处（候选选中 + 唯一命中 + 精确吻合）。"
    )


def test_download_start_switches_back_to_chapter_progress():
    """开始下载后要切回逐章进度模式，不能还停在卷列表上。"""
    cs = (ROOT / "wpf" / "LinovelibDesktop" / "MainWindow.xaml.cs").read_text(encoding="utf-8")
    assert "ShowChapterMode" in cs, "缺少「卷列表 → 章节进度」的切换。"
    # 两侧各有自己的切换方法（ShowChapterMode / ShowComicChapterMode），不能张冠李戴。
    for method, switcher in (("StartButton_Click", "ShowChapterMode"),
                             ("ComicStartButton_Click", "ShowComicChapterMode")):
        seg = cs.split(f"void {method}", 1)[1].split("\n    }", 1)[0]
        assert switcher in seg, f"{method} 未调用 {switcher} 切回章节进度模式。"


def test_catalog_calls_do_not_regress_timeout_semantics():
    """取目录的超时也必须抛异常，不能返回空列表。

    上一轮刚修好这个误报（超时被显示成「未找到」）；新增的取目录通路若复制旧的
    「返回已收集结果」写法，会把「超时」显示成「这部作品 0 卷」。
    """
    cs = (ROOT / "wpf" / "LinovelibDesktop" / "Services" / "DownloaderBridge.cs"
          ).read_text(encoding="utf-8")
    catalog = cs.split("CatalogAsync", 1)[1]
    assert "TimeoutException" in catalog or "ResolveCoreAsync" in catalog, (
        "取目录未复用既有超时语义。"
    )


def test_volume_overview_is_not_clobbered_by_chapter_overview():
    """卷模式下概览必须走卷口径。

    Report() → AppendLog() → UpdateTaskOverview() 是每次提示的必经之路；若它照旧按
    _rows（章节）算文案，刚写好的「共 N 卷 · 已选 M 卷」会被「准备开始新的下载任务」
    覆盖——用户刚选定作品就看到一句「准备开始」，仿佛什么都没发生。
    """
    cs = (ROOT / "wpf" / "LinovelibDesktop" / "MainWindow.xaml.cs").read_text(encoding="utf-8")
    for method, flag, delegate in (
        ("UpdateTaskOverview", "_showingVolumes", "UpdateVolumeOverview"),
        ("UpdateComicOverview", "_comicShowingVolumes", "UpdateComicVolumeOverview"),
    ):
        body = cs.split(f"private void {method}()", 1)[1].split("\n    }", 1)[0]
        assert flag in body and delegate in body, (
            f"{method} 未在卷模式下让路给 {delegate}。")


def test_selection_count_refreshes_when_a_box_is_checked():
    """勾选变化要同时刷新卷号框与概览计数（否则「已选 N 卷」永远停在 0）。"""
    cs = (ROOT / "wpf" / "LinovelibDesktop" / "MainWindow.xaml.cs").read_text(encoding="utf-8")
    for handler, overview in (("VolumeRow_PropertyChanged", "UpdateVolumeOverview"),
                              ("ComicVolumeRow_PropertyChanged", "UpdateComicVolumeOverview")):
        body = cs.split(f"private void {handler}", 1)[1].split("\n    }", 1)[0]
        assert "SyncVolumesToBox" in body, f"{handler} 未同步卷号框。"
        assert overview in body, f"{handler} 未刷新概览计数。"


def test_bulk_selection_is_batched_to_avoid_ui_stutter():
    """全选/退出多选必须批量改勾选，不能逐行触发回调。

    逐行赋 IsSelected 会让每行都发一次 PropertyChanged，每次回调都要跑一遍全表
    LINQ 聚合 + 刷 TextBlock，合计 O(N²) 外加 N 次布局过程——卷一多就肉眼可见地卡，
    这正是用户报的「切换多选时卡顿」。批量期间抑制回调，结束后只汇总一次。
    """
    cs = (ROOT / "wpf" / "LinovelibDesktop" / "MainWindow.xaml.cs").read_text(encoding="utf-8")

    assert "SetAllSelected" in cs, "缺少批量勾选helper。"
    # 两个批量入口（全选 / 退出多选）都要走批量 helper，而不是裸 foreach。
    for method in ("ResetMultiSelect", "ResetComicMultiSelect",
                   "SelectAllButton_Click", "ComicSelectAllButton_Click"):
        body = cs.split(f"private void {method}", 1)[1].split("\n    }", 1)[0]
        assert "SetAllSelected" in body, (
            f"{method} 仍在逐行改勾选——会成为卡顿源。")

    # 回调里必须有抑制开关，否则批量等于没做。
    for handler, flag in (("VolumeRow_PropertyChanged", "_suspendVolumeSync"),
                          ("ComicVolumeRow_PropertyChanged", "_suspendComicVolumeSync")):
        body = cs.split(f"private void {handler}", 1)[1].split("\n    }", 1)[0]
        assert flag in body and "return" in body, (
            f"{handler} 未在批量期间提前返回。")


def test_volume_toolbar_sits_left_of_the_filter_buttons():
    """卷工具条要在「全部」左边——用户指定的位置。"""
    xaml = (ROOT / "wpf" / "LinovelibDesktop" / "MainWindow.xaml").read_text(encoding="utf-8")
    # 按各自的「展开日志」按钮切出所属头部：两页都有「章节队列」字样，
    # 直接按标题 split 会两次都命中小说页。
    for toolbar, first_filter, log_btn in (
            ("VolumeToolbar", "AllFilterButton", "LogToggleButton"),
            ("ComicVolumeToolbar", "ComicAllFilterButton", "ComicLogToggleButton")):
        header = xaml.split(f'x:Name="{log_btn}"', 1)[0].rsplit('<Grid Margin="22,18,22,14">', 1)[-1]
        assert f'x:Name="{toolbar}"' in header, f"{toolbar} 不在队列头部。"
        assert header.index(f'x:Name="{toolbar}"') < header.index(f'x:Name="{first_filter}"'), (
            f"{toolbar} 应排在「全部」按钮左边。")
