# GitHub Publication and Relative Paths Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Publish a private, reproducible project repository that resolves project-owned paths from the repository root.

**Architecture:** Add a focused `linovelib.paths` module as the single owner of project-root paths. Make `main.py` use this module for cache and default output while retaining explicit output arguments. Keep browser discovery independent of machine-specific hard-coded paths. Git ignores only generated data and local state.

**Tech Stack:** Python 3.10+, pytest, Git, GitHub CLI.

## Global Constraints

- Repository name: `linovelib-epub-downloader` under `Yorushika114`.
- Repository visibility: private.
- Do not commit downloaded EPUBs, images, caches, Python bytecode, or diagnostic artifacts.
- Do not change the crawler's download or ordering behavior in this publication task.

---

### Task 1: Project-relative storage paths

**Files:**
- Create: `linovelib/paths.py`
- Create: `tests/test_paths.py`
- Modify: `main.py`

**Interfaces:**
- Produces: `PROJECT_ROOT: pathlib.Path`, `CACHE_DIR: pathlib.Path`, and `DEFAULT_DOWNLOAD_DIR: pathlib.Path`.
- Consumes: existing `--out` command-line argument.

- [ ] **Step 1: Write a failing test**

```python
from linovelib.paths import CACHE_DIR, DEFAULT_DOWNLOAD_DIR, PROJECT_ROOT

def test_project_owned_paths_are_relative_to_repository_root():
    assert CACHE_DIR == PROJECT_ROOT / "_tmp_dl"
    assert DEFAULT_DOWNLOAD_DIR == PROJECT_ROOT / "download"
    assert PROJECT_ROOT.joinpath("main.py").is_file()
```

- [ ] **Step 2: Run the test and verify it fails because `linovelib.paths` does not exist.**

Run: `pytest tests/test_paths.py -v`

- [ ] **Step 3: Add the path module and replace `pathlib.Path("_tmp_dl")` / `pathlib.Path("download")` in `main.py`.**

```python
PROJECT_ROOT = Path(__file__).resolve().parent.parent
CACHE_DIR = PROJECT_ROOT / "_tmp_dl"
DEFAULT_DOWNLOAD_DIR = PROJECT_ROOT / "download"
```

- [ ] **Step 4: Run the focused test and then the full test suite.**

Run: `pytest tests/test_paths.py -v` and `pytest -q`

### Task 2: Portable browser discovery and repository hygiene

**Files:**
- Modify: `linovelib/fetcher.py`
- Create: `.gitignore`

**Interfaces:**
- Consumes: optional `Fetcher(browser_path=...)` parameter.
- Produces: PATH-based browser discovery without hard-coded Windows installation paths.

- [ ] **Step 1: Replace machine-specific fallback paths with `shutil.which` discovery and keep an explicit `browser_path` override.**
- [ ] **Step 2: Create `.gitignore` for `_tmp_dl/`, `download/`, `__pycache__/`, `.pytest_cache/`, `*.py[cod]`, `*.epub`, and `tmp_*`.**
- [ ] **Step 3: Run `pytest -q` and `git check-ignore` checks for generated directories.**

### Task 3: README and GitHub publication

**Files:**
- Modify: `README.md`
- Create: `docs/superpowers/specs/2026-08-30-github-publication-design.md`

- [ ] **Step 1: Document setup, command examples, project layout, relative-path behavior, ignored generated files, and known content-order limitation.**
- [ ] **Step 2: Initialize Git, stage only intended files, and create the initial commit.**
- [ ] **Step 3: Create private repository `Yorushika114/linovelib-epub-downloader`, add `origin`, and push `main`.**
- [ ] **Step 4: Verify the remote URL, clean status, full test suite, ignored generated files, and absence of absolute Windows paths in tracked text.**
