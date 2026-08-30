import pytest
from linovelib.models import Volume
from linovelib.cli import build_parsed_args, choose_volumes


def _vols():
    return [Volume(title="v1"), Volume(title="v2"), Volume(title="v3")]


def test_choose_volumes_all_numeric():
    vs = choose_volumes(_vols(), [1, 3])
    assert [v.title for v in vs] == ["v1", "v3"]


def test_choose_volumes_all_string():
    vs = choose_volumes(_vols(), "all")
    assert [v.title for v in vs] == ["v1", "v2", "v3"]


def test_choose_volumes_out_of_range_raises():
    with pytest.raises(ValueError):
        choose_volumes(_vols(), [99])


def test_cli_does_not_accept_reference_epub_as_a_runtime_sorting_source():
    args = build_parsed_args(["--novel", "3095", "--volumes", "4"])
    assert not hasattr(args, "ref")
