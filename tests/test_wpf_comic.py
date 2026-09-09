"""WPF 漫画模式桥接测试：事件 JSON、桥路由、候选解析、XAML 接线。"""
import json
import re
from pathlib import Path

from comic.models import ComicHit
from linovelib.events import DownloadEvent
from wpf_bridge import comic_resolve_hits_json, event_to_json


ROOT = Path(__file__).parents[1]
BRIDGE_PY = (ROOT / "wpf_bridge.py").read_text(encoding="utf-8")
XAML = (ROOT / "wpf" / "LinovelibDesktop" / "MainWindow.xaml").read_text(encoding="utf-8")


class FakeComicFetcher:
    """模拟 ComicFetcher.search：返回命中列表，无需启动 Edge。"""
    def __init__(self, hits):
        self._hits = hits

    def search(self, keyword):
        return self._hits


def test_comic_event_json_keeps_chinese_and_one_line():
    line = event_to_json(DownloadEvent(
        "pdf_written", volume_index=1, volume_title="第一卷",
        chapter_id="9", chapter_title="第 9 话", output_path="C:\\out.pdf"))
    assert line.startswith("@@LINOVELIB_EVENT@@")
    payload = json.loads(line.removeprefix("@@LINOVELIB_EVENT@@"))

    assert payload["kind"] == "pdf_written"
    assert payload["chapterTitle"] == "第 9 话"
    assert payload["outputPath"] == "C:\\out.pdf"
    assert "chapter_title" not in payload and "output_path" not in payload


def test_wpf_bridge_routes_comic_modes():
    assert '"--resolve-comic"' in BRIDGE_PY
    assert '"--comic"' in BRIDGE_PY
    assert "run_comic_resolve" in BRIDGE_PY
    assert "run_comic" in BRIDGE_PY
    # comic.fetcher 顶层 import playwright，必须留在桥内懒加载：别把 playwright 拖进小说导入链。
    top = BRIDGE_PY.split("if __name__", 1)[0]
    assert not [l for l in top.splitlines() if re.match(r"^(from comic|import comic)\b", l)]


def test_comic_resolve_hits_json_serializable_exact():
    hits = [ComicHit(id=1270, title="咒术回战", author="芥见下下"),
            ComicHit(id=888, title="咒术回战0", author="芥见下下")]
    items = comic_resolve_hits_json("咒术回战", FakeComicFetcher(hits))

    assert [i["id"] for i in items] == ["1270", "888"]
    assert all(i["kind"] == "search_hit" for i in items)
    assert items[0]["exact"] is True   # 查询词与书名精确吻合
    assert items[1]["exact"] is False  # 书名有额外后缀，不吻合
    for i in items:
        json.dumps(i, ensure_ascii=False)  # 必须可直接序列化


def test_wpf_comic_declares_candidate_grid_cancel_and_log():
    assert 'x:Name="ComicCandidateList"' in XAML
    assert 'SelectionChanged="ComicCandidateList_SelectionChanged"' in XAML
    assert 'x:Name="ComicCancelButton"' in XAML
    assert 'Click="ComicCancelButton_Click"' in XAML
    assert 'x:Name="ComicLogToggleButton"' in XAML
    assert 'Click="ComicLogToggleButton_Click"' in XAML
