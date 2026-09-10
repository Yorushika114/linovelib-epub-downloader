from pathlib import Path


ROOT = Path(__file__).parents[1]


def test_cli_and_wpf_versions_match_current_release():
    package = (ROOT / "linovelib" / "__init__.py").read_text(encoding="utf-8")
    project = (ROOT / "wpf" / "LinovelibDesktop" / "LinovelibDesktop.csproj").read_text(encoding="utf-8")

    assert '__version__ = "2.0.1"' in package
    assert "<Version>2.0.1</Version>" in project
