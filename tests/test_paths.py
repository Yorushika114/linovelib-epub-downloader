from linovelib.paths import CACHE_DIR, DEFAULT_DOWNLOAD_DIR, PROJECT_ROOT


def test_project_owned_paths_are_relative_to_repository_root():
    assert CACHE_DIR == PROJECT_ROOT / "_tmp_dl"
    assert DEFAULT_DOWNLOAD_DIR == PROJECT_ROOT / "download"
    assert (PROJECT_ROOT / "main.py").is_file()
