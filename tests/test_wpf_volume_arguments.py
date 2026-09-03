from pathlib import Path


def test_wpf_bridge_uses_short_all_flag_instead_of_numeric_volume_option():
    source = (Path(__file__).parents[1] / "wpf" / "LinovelibDesktop" / "Services" / "DownloaderBridge.cs").read_text(encoding="utf-8")

    assert 'string.Equals(request.Volumes, "all", StringComparison.OrdinalIgnoreCase)' in source
    assert 'startInfo.ArgumentList.Add("--vol"); startInfo.ArgumentList.Add("all");' in source
