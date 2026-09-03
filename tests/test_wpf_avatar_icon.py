from pathlib import Path


def test_wpf_window_uses_local_anime_avatar_icon():
    root = Path(__file__).parents[1]
    xaml = (root / "wpf" / "LinovelibDesktop" / "MainWindow.xaml").read_text(encoding="utf-8")

    assert 'Icon="Assets/anime-avatar-icons8.jpg"' in xaml
    assert (root / "wpf" / "LinovelibDesktop" / "Assets" / "anime-avatar-icons8.jpg").is_file()
    project = (root / "wpf" / "LinovelibDesktop" / "LinovelibDesktop.csproj").read_text(encoding="utf-8")
    assert '<Resource Include="Assets\\anime-avatar-icons8.jpg" />' in project
