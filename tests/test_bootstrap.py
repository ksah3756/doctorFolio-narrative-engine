from pathlib import Path


def test_project_skeleton_exists() -> None:
    assert Path("src/dcf_engine").is_dir()

