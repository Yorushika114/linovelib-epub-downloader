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

    assert xaml.count('PreviewMouseWheel="DataGrid_SmoothWheel"') == 2
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
