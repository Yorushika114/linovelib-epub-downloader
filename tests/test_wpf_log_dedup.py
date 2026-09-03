from pathlib import Path


def test_wpf_log_skips_adjacent_duplicate_messages():
    source = (Path(__file__).parents[1] / "wpf" / "LinovelibDesktop" / "MainWindow.xaml.cs").read_text(encoding="utf-8")

    assert 'private string _lastLogLine = "";' in source
    assert 'if (line == _lastLogLine) return;' in source
