from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
AUTO_LOOP = REPO_ROOT / "scripts" / "auto-loop.sh"
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


def _auto_loop_env(
    tmp_path: Path,
    phase: str,
    discord_messages: list[dict[str, object]],
) -> tuple[Path, dict[str, str], Path]:
    project_dir = tmp_path / "auto-loop-project"
    scripts_dir = project_dir / "scripts"
    state_dir = project_dir / ".auto-loop"
    bin_dir = tmp_path / "auto-loop-bin"
    scripts_dir.mkdir(parents=True)
    state_dir.mkdir()
    bin_dir.mkdir()

    shutil.copy2(AUTO_LOOP, scripts_dir / "auto-loop.sh")
    (scripts_dir / "auto-loop-prompt.md").write_text("Run one Claude step.\n")
    (scripts_dir / "codex-auto-loop-prompt.md").write_text("Run one Codex step.\n")
    (scripts_dir / "auto-loop-mcp.json").write_text("{}\n")

    proposed_at = "2026-06-23T00:00:00Z" if phase == "awaiting_approval" else "null"
    (state_dir / "work-status.md").write_text(
        f"---\nphase: {phase}\nproposed_at: {proposed_at}\nupdated: 2026-06-23T00:00:00Z\n---\n"
    )

    claude_called = tmp_path / "claude-called"
    _write_executable(
        bin_dir / "claude",
        '#!/bin/sh\ntouch "$FAKE_CLAUDE_CALLED"\n',
    )
    env = os.environ.copy()
    env.update(
        {
            "CLAUDE_BIN": str(bin_dir / "claude"),
            "FAKE_CLAUDE_CALLED": str(claude_called),
            "FAKE_DISCORD_MESSAGES": json.dumps(discord_messages),
            "HOME": str(tmp_path),
        }
    )
    return project_dir, env, claude_called


def _runner_env(tmp_path: Path, project_dir: Path) -> tuple[dict[str, str], Path, Path]:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    codex_args = tmp_path / "codex-args.txt"
    codex_prompt = tmp_path / "codex-prompt.txt"
    review_codex_args = tmp_path / "review-codex-args.txt"
    review_codex_prompt = tmp_path / "review-codex-prompt.txt"
    review_count = tmp_path / "review-count.txt"
    implementation_count = tmp_path / "implementation-count.txt"
    make_args = tmp_path / "make-args.txt"
    discord_payloads = tmp_path / "discord-payloads.jsonl"
    discord_count = tmp_path / "discord-count.txt"

    _write_executable(
        bin_dir / "codex",
        "#!/bin/sh\n"
        'review_output=""\n'
        'previous=""\n'
        'for argument in "$@"; do\n'
        '  if [ "$previous" = "--output-last-message" ]; then review_output="$argument"; fi\n'
        '  previous="$argument"\n'
        "done\n"
        'if printf "%s\\n" "$@" | grep -q -- "--output-schema"; then\n'
        '  printf "%s\\n" "$@" > "$FAKE_REVIEW_CODEX_ARGS"\n'
        '  cat > "$FAKE_REVIEW_CODEX_PROMPT"\n'
        '  count=$(cat "$FAKE_REVIEW_COUNT" 2>/dev/null || echo 0)\n'
        "  count=$((count + 1))\n"
        '  echo "$count" > "$FAKE_REVIEW_COUNT"\n'
        '  python3 - "$FAKE_REVIEW_RESULTS" "$count" "$review_output" <<\'PY\'\n'
        "import json\n"
        "import sys\n"
        "from pathlib import Path\n"
        "results = json.loads(sys.argv[1])\n"
        "Path(sys.argv[3]).write_text(json.dumps(results[int(sys.argv[2]) - 1]))\n"
        "PY\n"
        '  exit "${FAKE_REVIEW_EXIT:-0}"\n'
        "fi\n"
        'printf "%s\\n" "$@" > "$FAKE_CODEX_ARGS"\n'
        'cat > "$FAKE_CODEX_PROMPT"\n'
        'count=$(cat "$FAKE_IMPLEMENTATION_COUNT" 2>/dev/null || echo 0)\n'
        "count=$((count + 1))\n"
        'echo "$count" > "$FAKE_IMPLEMENTATION_COUNT"\n'
        "# 새 contract: Codex는 작업을 커밋한다 (runner 커밋 가드 충족).\n"
        'if [ "${FAKE_CODEX_EXIT:-0}" = "0" ]; then\n'
        '  echo "work-$count" > "codex_work_$count.txt"\n'
        '  git add "codex_work_$count.txt" && git commit -q -m "stub codex work $count"\n'
        "fi\n"
        'exit "${FAKE_CODEX_EXIT:-0}"\n',
    )
    _write_executable(
        bin_dir / "make",
        '#!/bin/sh\nprintf "%s\\n" "$@" > "$FAKE_MAKE_ARGS"\n',
    )
    _write_executable(
        bin_dir / "curl",
        "#!/bin/sh\n"
        'payload=""\n'
        'previous=""\n'
        'for argument in "$@"; do\n'
        '  if [ "$previous" = "-d" ]; then payload="$argument"; fi\n'
        '  previous="$argument"\n'
        "done\n"
        'printf "%s\\n" "$payload" >> "$FAKE_DISCORD_PAYLOADS"\n'
        'count=$(cat "$FAKE_DISCORD_COUNT" 2>/dev/null || echo 0)\n'
        "count=$((count + 1))\n"
        'echo "$count" > "$FAKE_DISCORD_COUNT"\n'
        'printf \'{"id":"%s"}\\n\' "$((9000 + count))"\n',
    )

    env = os.environ.copy()
    env.update(
        {
            "AUTO_LOOP_PROJECT_DIR": str(project_dir),
            "AUTO_LOOP_DISABLE_DISCORD": "1",
            "CODEX_BIN": str(bin_dir / "codex"),
            "FAKE_CODEX_ARGS": str(codex_args),
            "FAKE_CODEX_PROMPT": str(codex_prompt),
            "FAKE_REVIEW_CODEX_ARGS": str(review_codex_args),
            "FAKE_REVIEW_CODEX_PROMPT": str(review_codex_prompt),
            "FAKE_REVIEW_COUNT": str(review_count),
            "FAKE_IMPLEMENTATION_COUNT": str(implementation_count),
            "FAKE_REVIEW_RESULTS": json.dumps(
                [
                    {
                        "status": "APPROVED",
                        "summary": "변경 범위와 테스트가 요구사항을 충족합니다.",
                        "p1_findings": [],
                        "p2_findings": [],
                    }
                ]
            ),
            "FAKE_MAKE_ARGS": str(make_args),
            "FAKE_DISCORD_PAYLOADS": str(discord_payloads),
            "FAKE_DISCORD_COUNT": str(discord_count),
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
    assert 'model_reasoning_effort="high"' in codex_args
    assert "--sandbox" in codex_args
    assert "workspace-write" in codex_args
    # workspace-write 샌드박스의 기본 .git read-only 배제를 무력화해 Codex 커밋을 허용한다.
    assert any(
        "sandbox_workspace_write.writable_roots" in arg and arg.endswith('/.git"]')
        for arg in codex_args
    ), codex_args
    assert codex_prompt_file.read_text() == prompt_file.read_text()
    assert (tmp_path / "make-args.txt").read_text().splitlines() == ["verify"]
    review_args = (tmp_path / "review-codex-args.txt").read_text().splitlines()
    assert "--ephemeral" in review_args
    assert "--output-schema" in review_args
    assert review_args[review_args.index("--sandbox") + 1] == "read-only"
    assert (tmp_path / "review-count.txt").read_text().strip() == "1"
    assert (tmp_path / "implementation-count.txt").read_text().strip() == "1"

    status = json.loads((project_dir / ".auto-loop/tasks/issue-42.json").read_text())
    assert status["status"] == "completed"
    assert status["branch"] == "feat/42-direct-codex"
    assert status["exit_code"] == 0
    assert status["stage"] == "review"
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
    assert not (tmp_path / "review-count.txt").exists()


def test_review_result_posts_without_mentions_and_arms_current_pr_gate(
    tmp_path: Path,
) -> None:
    project_dir = tmp_path / "project"
    project_dir.mkdir()
    _init_repo(project_dir)
    state_dir = project_dir / ".auto-loop"
    state_dir.mkdir()
    (state_dir / "work-status.md").write_text(
        "---\n"
        "phase: implementing\n"
        "issue: 44\n"
        "branch: feat/44-reviewed\n"
        "review_cycle: 0\n"
        "pr_approval_message_id: null\n"
        "updated: 2026-06-23T00:00:00Z\n"
        "---\n"
    )
    prompt_file = project_dir / "task.md"
    prompt_file.write_text("Implement the approved issue.\n")
    env, _, _ = _runner_env(tmp_path, project_dir)
    env.update(
        {
            "AUTO_LOOP_DISABLE_DISCORD": "0",
            "DISCORD_WEBHOOK_URL": "https://discord.invalid/webhook",
            "FAKE_REVIEW_RESULTS": json.dumps(
                [
                    {
                        "status": "APPROVED",
                        "summary": "회귀 테스트와 상태 전이가 일치합니다.",
                        "p1_findings": [],
                        "p2_findings": [
                            {
                                "location": "scripts/run-codex-task.sh:1",
                                "issue": "주석을 더 줄일 수 있습니다.",
                                "fix": "선택적으로 정리합니다.",
                            }
                        ],
                    }
                ]
            ),
        }
    )

    result = subprocess.run(
        [
            str(RUNNER),
            "--issue",
            "44",
            "--branch",
            "feat/44-reviewed",
            "--prompt-file",
            str(prompt_file),
        ],
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    payloads = [
        json.loads(line) for line in (tmp_path / "discord-payloads.jsonl").read_text().splitlines()
    ]
    assert len(payloads) == 1
    content = payloads[0]["content"]
    assert "회귀 테스트와 상태 전이가 일치합니다." in content
    assert "P1 0건" in content
    assert "<@" not in content
    assert "Claude" not in content
    assert payloads[0]["allowed_mentions"] == {"parse": []}

    state = (state_dir / "work-status.md").read_text()
    assert "phase: awaiting_pr" in state
    assert "review_cycle: 1" in state
    assert "pr_approval_message_id: 9001" in state


def test_p1_review_automatically_runs_fix_and_a_fresh_second_review(
    tmp_path: Path,
) -> None:
    project_dir = tmp_path / "project"
    project_dir.mkdir()
    _init_repo(project_dir)
    state_dir = project_dir / ".auto-loop"
    state_dir.mkdir()
    (state_dir / "work-status.md").write_text(
        "---\n"
        "phase: implementing\n"
        "issue: 45\n"
        "branch: feat/45-review-fix\n"
        "review_cycle: 0\n"
        "pr_approval_message_id: null\n"
        "updated: 2026-06-23T00:00:00Z\n"
        "---\n"
    )
    prompt_file = project_dir / "task.md"
    prompt_file.write_text("Implement the approved issue.\n")
    env, _, _ = _runner_env(tmp_path, project_dir)
    env.update(
        {
            "AUTO_LOOP_DISABLE_DISCORD": "0",
            "DISCORD_WEBHOOK_URL": "https://discord.invalid/webhook",
            "FAKE_REVIEW_RESULTS": json.dumps(
                [
                    {
                        "status": "NEEDS_REVISION",
                        "summary": "승인 상태 저장에 회귀가 있습니다.",
                        "p1_findings": [
                            {
                                "location": "scripts/run-codex-task.sh:200",
                                "issue": "현재 작업 검증 없이 상태를 덮어씁니다.",
                                "fix": "issue와 branch를 재검증합니다.",
                            }
                        ],
                        "p2_findings": [],
                    },
                    {
                        "status": "APPROVED",
                        "summary": "현재 작업 검증이 추가됐습니다.",
                        "p1_findings": [],
                        "p2_findings": [],
                    },
                ]
            ),
        }
    )

    result = subprocess.run(
        [
            str(RUNNER),
            "--issue",
            "45",
            "--branch",
            "feat/45-review-fix",
            "--prompt-file",
            str(prompt_file),
        ],
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert (tmp_path / "implementation-count.txt").read_text().strip() == "2"
    assert (tmp_path / "review-count.txt").read_text().strip() == "2"
    payloads = (tmp_path / "discord-payloads.jsonl").read_text().splitlines()
    assert len(payloads) == 2
    assert all("<@" not in json.loads(payload)["content"] for payload in payloads)
    state = (state_dir / "work-status.md").read_text()
    assert "phase: awaiting_pr" in state
    assert "review_cycle: 2" in state
    assert "pr_approval_message_id: 9002" in state


def test_active_review_flow_has_no_claude_review_request_or_bot_mention() -> None:
    active_files = (
        "run-codex-task.sh",
        "auto-loop-prompt.md",
        "codex-auto-loop-prompt.md",
    )
    for file_name in active_files:
        content = (REPO_ROOT / "scripts" / file_name).read_text()
        assert "Claude review requested" not in content
        assert "<@1491798466660139148>" not in content


def test_auto_loop_prompts_delegate_without_omc_team() -> None:
    for prompt_name in ("auto-loop-prompt.md", "codex-auto-loop-prompt.md"):
        prompt = (REPO_ROOT / "scripts" / prompt_name).read_text()
        assert "omc team" not in prompt
        assert "dispatch-codex-task.sh" in prompt


def test_auto_loop_codex_fallback_uses_high_reasoning() -> None:
    script = (REPO_ROOT / "scripts" / "auto-loop.sh").read_text()
    assert 'model_reasoning_effort="high"' in script


def test_awaiting_pr_always_defers_to_shell_poller(tmp_path: Path) -> None:
    messages: list[dict[str, object]] = [
        {
            "timestamp": "2026-06-23T00:30:00Z",
            "author": {"id": "1131404924094251099", "bot": False},
            "content": "ㄱㄱ",
        },
    ]
    project_dir, env, claude_called = _auto_loop_env(tmp_path, "awaiting_pr", messages)

    result = subprocess.run(
        [str(project_dir / "scripts" / "auto-loop.sh")],
        cwd=project_dir,
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert not claude_called.exists()
    log = (project_dir / ".auto-loop/logs/auto-loop.log").read_text()
    assert "10분 PR 승인 poller" in log


def test_legacy_awaiting_approval_runs_without_new_user_message(tmp_path: Path) -> None:
    project_dir, env, claude_called = _auto_loop_env(
        tmp_path,
        "awaiting_approval",
        [],
    )

    result = subprocess.run(
        [str(project_dir / "scripts" / "auto-loop.sh")],
        cwd=project_dir,
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert claude_called.exists()


def test_waiting_phase_wakes_llm_for_new_designated_user_message(tmp_path: Path) -> None:
    messages: list[dict[str, object]] = [
        {
            "timestamp": "2026-06-23T00:30:00Z",
            "author": {"id": "1131404924094251099", "bot": False},
            "content": "ㄱㄱ",
        }
    ]
    project_dir, env, claude_called = _auto_loop_env(
        tmp_path,
        "awaiting_approval",
        messages,
    )

    result = subprocess.run(
        [str(project_dir / "scripts" / "auto-loop.sh")],
        cwd=project_dir,
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert claude_called.exists()


def test_idle_prompts_auto_start_without_implementation_approval() -> None:
    claude_prompt = (REPO_ROOT / "scripts" / "auto-loop-prompt.md").read_text()
    codex_prompt = (REPO_ROOT / "scripts" / "codex-auto-loop-prompt.md").read_text()

    assert "승인 대기 없이 즉시 착수" in claude_prompt
    assert "without waiting for implementation approval" in codex_prompt
    assert "phase idle→implementing" in claude_prompt
    assert "phase idle→implementing" in codex_prompt


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
