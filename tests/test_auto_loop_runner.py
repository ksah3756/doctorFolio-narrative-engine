from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
RUNNER = REPO_ROOT / "scripts" / "run-codex-task.sh"
LESSON_RECORDER = REPO_ROOT / "scripts" / "record-auto-loop-lesson.sh"


def _write_executable(path: Path, content: str) -> None:
    path.write_text(content)
    path.chmod(0o755)


def _init_repo(path: Path) -> None:
    subprocess.run(["git", "init", "-b", "main"], cwd=path, check=True, capture_output=True)
    subprocess.run(
        ["git", "config", "user.email", "auto-loop@example.invalid"],
        cwd=path,
        check=True,
    )
    subprocess.run(["git", "config", "user.name", "Auto Loop Test"], cwd=path, check=True)
    (path / "README.md").write_text("test repo\n")
    subprocess.run(["git", "add", "README.md"], cwd=path, check=True)
    subprocess.run(["git", "commit", "-m", "Initialize test repo"], cwd=path, check=True)


def _runner_env(tmp_path: Path, project_dir: Path) -> tuple[dict[str, str], Path, Path]:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    codex_args = tmp_path / "codex-args.txt"
    codex_prompt = tmp_path / "codex-prompt.txt"
    make_args = tmp_path / "make-args.txt"

    _write_executable(
        bin_dir / "codex",
        "#!/bin/sh\n"
        'printf "%s\\n" "$@" > "$FAKE_CODEX_ARGS"\n'
        'cat > "$FAKE_CODEX_PROMPT"\n'
        "# 새 contract: Codex는 작업을 커밋한다 (runner 커밋 가드 충족).\n"
        'if [ "${FAKE_CODEX_EXIT:-0}" = "0" ]; then\n'
        "  echo work > codex_work.txt\n"
        '  git add codex_work.txt && git commit -q -m "stub codex work"\n'
        "fi\n"
        'exit "${FAKE_CODEX_EXIT:-0}"\n',
    )
    _write_executable(
        bin_dir / "make",
        "#!/bin/sh\n"
        'printf "%s\\n" "$@" > "$FAKE_MAKE_ARGS"\n',
    )

    env = os.environ.copy()
    env.update(
        {
            "AUTO_LOOP_PROJECT_DIR": str(project_dir),
            "AUTO_LOOP_DISABLE_DISCORD": "1",
            "CODEX_BIN": str(bin_dir / "codex"),
            "FAKE_CODEX_ARGS": str(codex_args),
            "FAKE_CODEX_PROMPT": str(codex_prompt),
            "FAKE_MAKE_ARGS": str(make_args),
            "PATH": f"{bin_dir}:{env['PATH']}",
        }
    )
    return env, codex_args, codex_prompt


def test_direct_codex_runner_uses_high_reasoning_and_records_completion(
    tmp_path: Path,
) -> None:
    project_dir = tmp_path / "project"
    project_dir.mkdir()
    _init_repo(project_dir)
    prompt_file = project_dir / "task.md"
    prompt_file.write_text("Implement the approved issue.\n")
    env, codex_args_file, codex_prompt_file = _runner_env(tmp_path, project_dir)

    result = subprocess.run(
        [
            str(RUNNER),
            "--issue",
            "42",
            "--branch",
            "feat/42-direct-codex",
            "--prompt-file",
            str(prompt_file),
        ],
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    codex_args = codex_args_file.read_text().splitlines()
    assert "exec" in codex_args
    assert codex_args.index("--ask-for-approval") < codex_args.index("exec")
    assert codex_args[codex_args.index("--ask-for-approval") + 1] == "never"
    assert "model_reasoning_effort=\"high\"" in codex_args
    assert "--sandbox" in codex_args
    assert "workspace-write" in codex_args
    assert codex_prompt_file.read_text() == prompt_file.read_text()
    assert (tmp_path / "make-args.txt").read_text().splitlines() == ["verify"]

    status = json.loads((project_dir / ".auto-loop/tasks/issue-42.json").read_text())
    assert status["status"] == "completed"
    assert status["branch"] == "feat/42-direct-codex"
    assert status["exit_code"] == 0
    assert (
        subprocess.run(
            ["git", "branch", "--show-current"],
            cwd=project_dir,
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()
        == "feat/42-direct-codex"
    )


def test_direct_codex_runner_records_codex_failure_without_verifying(tmp_path: Path) -> None:
    project_dir = tmp_path / "project"
    project_dir.mkdir()
    _init_repo(project_dir)
    prompt_file = project_dir / "task.md"
    prompt_file.write_text("Fail this test run.\n")
    env, _, _ = _runner_env(tmp_path, project_dir)
    env["FAKE_CODEX_EXIT"] = "7"

    result = subprocess.run(
        [
            str(RUNNER),
            "--issue",
            "43",
            "--branch",
            "feat/43-failing-codex",
            "--prompt-file",
            str(prompt_file),
        ],
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 7
    assert not (tmp_path / "make-args.txt").exists()
    status = json.loads((project_dir / ".auto-loop/tasks/issue-43.json").read_text())
    assert status["status"] == "failed"
    assert status["exit_code"] == 7


def test_auto_loop_prompts_delegate_without_omc_team() -> None:
    for prompt_name in ("auto-loop-prompt.md", "codex-auto-loop-prompt.md"):
        prompt = (REPO_ROOT / "scripts" / prompt_name).read_text()
        assert "omc team" not in prompt
        assert "dispatch-codex-task.sh" in prompt


def test_auto_loop_codex_fallback_uses_high_reasoning() -> None:
    script = (REPO_ROOT / "scripts" / "auto-loop.sh").read_text()
    assert "model_reasoning_effort=\"high\"" in script


def test_lesson_recorder_writes_structured_entry_and_deduplicates(tmp_path: Path) -> None:
    env = os.environ.copy()
    env["AUTO_LOOP_PROJECT_DIR"] = str(tmp_path)
    command = [
        str(LESSON_RECORDER),
        "--issue",
        "5",
        "--phase",
        "implementing",
        "--title",
        "Prefer deterministic approval routing",
        "--context",
        "A short Discord approval opened without project context.",
        "--lesson",
        "Control messages should resolve state before invoking an agent.",
        "--directive",
        "Route approvals through the project state machine.",
        "--evidence",
        "Discord session opened in the wrong working directory.",
    ]

    first = subprocess.run(command, env=env, text=True, capture_output=True, check=False)
    second = subprocess.run(command, env=env, text=True, capture_output=True, check=False)

    assert first.returncode == 0, first.stderr
    assert second.returncode == 0, second.stderr
    journal = (tmp_path / ".auto-loop/lessons.md").read_text()
    assert journal.count("## ") == 1
    assert journal.count("<!-- lesson-key:") == 1
    assert "- Issue: #5" in journal
    assert "- Phase: implementing" in journal
    assert "- Future directive: Route approvals through the project state machine." in journal


def test_auto_loop_prompts_apply_learning_policy() -> None:
    for prompt_name in ("auto-loop-prompt.md", "codex-auto-loop-prompt.md"):
        prompt = (REPO_ROOT / "scripts" / prompt_name).read_text()
        assert "scripts/learning-policy.md" in prompt
        assert "record-auto-loop-lesson.sh" in prompt


def test_active_auto_loop_files_do_not_reference_legacy_runtime_directory() -> None:
    active_files = (
        "auto-loop.sh",
        "auto-loop-prompt.md",
        "codex-auto-loop-prompt.md",
        "run-codex-task.sh",
        "record-auto-loop-lesson.sh",
        "learning-policy.md",
    )
    for file_name in active_files:
        content = (REPO_ROOT / "scripts" / file_name).read_text()
        assert ".omc" not in content
        assert ".auto-loop" in content
