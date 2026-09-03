from pathlib import Path


def test_wpf_layout_does_not_use_auto_as_a_margin_component():
    xaml = (Path(__file__).parents[1] / "wpf" / "LinovelibDesktop" / "MainWindow.xaml").read_text(encoding="utf-8")

    assert 'Margin="0,auto,0,8"' not in xaml
