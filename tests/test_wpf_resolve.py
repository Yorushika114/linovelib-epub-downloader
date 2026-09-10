import json
from pathlib import Path

from wpf_bridge import resolve_hits_json


class FakeBrowser:
    """模拟 RenderFetcher.search_html：返回渲染后的搜索结果页 HTML。"""
    def __init__(self, html):
        self.html = html

    def search_html(self, name):
        return self.html


class NoFetcher:
    # 给定 browser 时，search_hits 优先走浏览器站点搜索，未用到 fetcher。
    pass


SEARCH_HTML = '''
<div class="search-result-list clearfix">
  <div class="imgbox fl se-result-book"><a href="/novel/2013.html"><img src="..."/></a></div>
  <div class="fl se-result-infos"><h2 class="tit"><a href="/novel/2013.html">无职转生 ～到了异世界就拿出真本事～</a></h2></div>
</div>
<div class="search-result-list clearfix">
  <div class="imgbox fl se-result-book"><a href="/novel/4325.html"><img src="..."/></a></div>
  <div class="fl se-result-infos"><h2 class="tit"><a href="/novel/4325.html">无职转生 ～蛇足篇～</a></h2></div>
</div>
'''


def test_resolve_hits_json_returns_serializable_candidates_with_exact_flag():
    # WPF 依赖 resolve_hits_json 拿到可 JSON 化的候选列表（不选取、标记书名吻合）。
    items = resolve_hits_json("无职转生 ～蛇足篇～", NoFetcher(),
                              browser=FakeBrowser(SEARCH_HTML))

    assert [i["id"] for i in items] == ["2013", "4325"]
    assert all(i["kind"] == "search_hit" for i in items)
    assert items[1]["title"] == "无职转生 ～蛇足篇～"
    assert items[1]["exact"] is True   # 查询词与 蛇足篇 标题精确吻合
    assert items[0]["exact"] is False  # 主篇标题与查询词不一致
    for i in items:
        json.dumps(i, ensure_ascii=False)  # 必须可直接序列化


def test_wpf_mainwindow_exposes_search_and_candidate_grid():
    # 书名候选在主内容区以 DataGrid 呈现（与下载进度表同格切换），而非小下拉框。
    xaml = (Path(__file__).parents[1] / "wpf" / "LinovelibDesktop" / "MainWindow.xaml"
            ).read_text(encoding="utf-8")

    assert 'x:Name="SearchButton"' in xaml
    assert 'Click="SearchButton_Click"' in xaml
    assert 'x:Name="CandidateList"' in xaml
    assert 'SelectionChanged="CandidateList_SelectionChanged"' in xaml


def test_wpf_datagrids_scroll_one_row_per_wheel_click():
    # DataGrid 默认滚轮跳 3 行，已改为逐行滚：两张表都挂 PreviewMouseWheel。
    xaml = (Path(__file__).parents[1] / "wpf" / "LinovelibDesktop" / "MainWindow.xaml"
            ).read_text(encoding="utf-8")
    cs = (Path(__file__).parents[1] / "wpf" / "LinovelibDesktop" / "MainWindow.xaml.cs"
          ).read_text(encoding="utf-8")

    assert xaml.count('PreviewMouseWheel="DataGrid_SmoothWheel"') == 4
    assert "private void DataGrid_SmoothWheel" in cs
    assert "sv.LineUp()" in cs and "sv.LineDown()" in cs


def test_wpf_candidate_grid_shows_selection_affordance():
    # 候选表要有悬停高亮 + 手型光标，让用户看清将被点中的行，避免误触。
    xaml = (Path(__file__).parents[1] / "wpf" / "LinovelibDesktop" / "MainWindow.xaml"
            ).read_text(encoding="utf-8")
    seg = xaml.split('x:Name="CandidateList"', 1)[1].split('</DataGrid>', 1)[0]
    assert "IsMouseOver" in seg
    assert 'Value="Hand"' in seg
    assert "IsSelected" in seg


def test_wpf_bridge_has_resolve_mode():
    cs = (Path(__file__).parents[1] / "wpf" / "LinovelibDesktop" / "Services"
          / "DownloaderBridge.cs").read_text(encoding="utf-8")

    assert "ResolveAsync" in cs
    assert '"--resolve"' in cs


def test_wpf_candidate_list_uses_a_notifying_collection_between_searches():
    cs = (Path(__file__).parents[1] / "wpf" / "LinovelibDesktop" / "MainWindow.xaml.cs"
          ).read_text(encoding="utf-8")

    assert "ObservableCollection<ResolveResultDto> _candidates" in cs
    assert "CandidateList.ItemsSource = _candidates;" in cs
    assert "_candidates.AddRange(results)" not in cs


def test_search_timeout_is_not_reported_as_not_found():
    """搜索超时必须抛明确异常，不能返回空列表。

    返回空列表会被 MainWindow 渲染成「未找到名为『X』的小说，请改用编号」——把一次
    超时误报成「这本书不存在」，用户既得到错误结论，也不知道该重试。

    冷机器上这条路径是常态：首次启动 Edge 要建 profile、首次访问站点无任何缓存/cookie，
    暖机+搜索+2.5s 静置很容易越过 60s；而开发机 Edge 已暖、cookie 已在，秒回，所以这个
    误报只在目标机器上暴露——正是分发包要面对的机器。
    """
    cs = (Path(__file__).parents[1] / "wpf" / "LinovelibDesktop" / "Services"
          / "DownloaderBridge.cs").read_text(encoding="utf-8")
    seg = cs.split("finished == deadline", 1)[1].split("var line = await readLine", 1)[0]

    assert "TimeoutException" in seg, (
        "超时分支未抛出明确异常——返回空列表会被当成「未找到」。"
    )
    assert "return results" not in seg, (
        "超时分支仍在返回已收集的（通常为空的）结果——无法与「真没搜到」区分。"
    )
    assert "重试" in seg, "超时提示应告诉用户可以重试。"
