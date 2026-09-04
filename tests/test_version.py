from pathlib import Path


ROOT = Path(__file__).parents[1]


def test_cli_and_wpf_versions_are_1_0_5():
    package = (ROOT / "linovelib" / "__init__.py").read_text(encoding="utf-8")
    project = (ROOT / "wpf" / "LinovelibDesktop" / "LinovelibDesktop.csproj").read_text(encoding="utf-8")

    assert '__version__ = "1.0.5"' in package
    assert "<Version>1.0.5</Version>" in project
