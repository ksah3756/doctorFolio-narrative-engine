from __future__ import annotations

import json
import os
import shutil
import subprocess
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
AUTO_LOOP = REPO_ROOT / "scripts" / "auto-loop.sh"
RUNNER = REPO_ROOT / "scripts" / "run-codex-task.sh"
LESSON_RECORDER = REPO_ROOT / "scripts" / "record-auto-loop-lesson.sh"
REVIEW_RESULT_TOOL = REPO_ROOT / "scripts" / "codex_review_result.py"


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
    codex_called = tmp_path / "codex-called"
    _write_executable(
        bin_dir / "claude",
        "#!/bin/sh\n"
        'touch "$FAKE_CLAUDE_CALLED"\n'
        'if [ -n "${FAKE_CLAUDE_SLEEP_SECONDS:-}" ]; then sleep "$FAKE_CLAUDE_SLEEP_SECONDS"; fi\n'
        'printf "%s\\n" "${FAKE_CLAUDE_OUTPUT:-}"\n'
        'exit "${FAKE_CLAUDE_EXIT:-0}"\n',
    )
    _write_executable(
        bin_dir / "codex",
        "#!/bin/sh\n"
        'touch "$FAKE_CODEX_CALLED"\n'
        'if [ -n "${FAKE_CODEX_SLEEP_SECONDS:-}" ]; then sleep "$FAKE_CODEX_SLEEP_SECONDS"; fi\n',
    )
    env = os.environ.copy()
    env.update(
        {
            "CLAUDE_BIN": str(bin_dir / "claude"),
            "FAKE_CLAUDE_CALLED": str(claude_called),
            "CODEX_BIN": str(bin_dir / "codex"),
            "FAKE_CODEX_CALLED": str(codex_called),
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
        '  work_path="${FAKE_IMPLEMENTATION_PATH:-codex_work_$count.txt}"\n'
        '  mkdir -p "$(dirname "$work_path")"\n'
        '  echo "work-$count" > "$work_path"\n'
        '  git add "$work_path" && git commit -q -m "stub codex work $count"\n'
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
                        "risk_level": "LOW",
                        "claude_escalation": False,
                        "escalation_reasons": [],
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
    legacy_review = project_dir / "REVIEW-1.md"
    legacy_review.write_text("another issue review\n")
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
                        "risk_level": "LOW",
                        "claude_escalation": False,
                        "escalation_reasons": [],
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
    assert legacy_review.read_text() == "another issue review\n"
    assert (project_dir / "REVIEW-44-1.md").exists()

    state = (state_dir / "work-status.md").read_text()
    assert "phase: awaiting_pr" in state
    assert "review_cycle: 1" in state
    assert "pr_approval_message_id: 9001" in state


def test_p1_review_escalates_to_claude_without_automatic_codex_fix(
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
                        "risk_level": "HIGH",
                        "claude_escalation": True,
                        "escalation_reasons": ["p1_finding"],
                        "p1_findings": [
                            {
                                "location": "scripts/run-codex-task.sh:200",
                                "issue": "현재 작업 검증 없이 상태를 덮어씁니다.",
                                "fix": "issue와 branch를 재검증합니다.",
                            }
                        ],
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
    assert (tmp_path / "implementation-count.txt").read_text().strip() == "1"
    assert (tmp_path / "review-count.txt").read_text().strip() == "1"
    payloads = (tmp_path / "discord-payloads.jsonl").read_text().splitlines()
    assert len(payloads) == 1
    payload = json.loads(payloads[0])
    assert "<@1491798466660139148>" in payload["content"]
    assert "PR 생성+머지를 승인하려면" not in payload["content"]
    assert payload["allowed_mentions"] == {"users": ["1491798466660139148"]}
    state = (state_dir / "work-status.md").read_text()
    assert "phase: awaiting_claude_review" in state
    assert "status: CLAUDE_REVIEW" in state
    assert "review_cycle: 1" in state
    status = json.loads((state_dir / "tasks/issue-45.json").read_text())
    assert status["status"] == "escalated"
    assert status["stage"] == "claude_review"


def test_numeric_core_change_escalates_even_when_codex_finds_no_p1(
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
        "issue: 46\n"
        "branch: feat/46-numeric-review\n"
        "review_cycle: 0\n"
        "pr_approval_message_id: null\n"
        "updated: 2026-06-23T00:00:00Z\n"
        "---\n"
    )
    prompt_file = project_dir / "task.md"
    prompt_file.write_text("Adjust mean reversion behavior.\n")
    env, _, _ = _runner_env(tmp_path, project_dir)
    env.update(
        {
            "AUTO_LOOP_DISABLE_DISCORD": "0",
            "DISCORD_WEBHOOK_URL": "https://discord.invalid/webhook",
            "FAKE_IMPLEMENTATION_PATH": "src/dcf_engine/mature_case.py",
        }
    )

    result = subprocess.run(
        [
            str(RUNNER),
            "--issue",
            "46",
            "--branch",
            "feat/46-numeric-review",
            "--prompt-file",
            str(prompt_file),
        ],
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    payload = json.loads((tmp_path / "discord-payloads.jsonl").read_text())
    assert "<@1491798466660139148>" in payload["content"]
    assert "numeric_semantics" in payload["content"]
    assert "PR 생성+머지를 승인하려면" not in payload["content"]
    state = (state_dir / "work-status.md").read_text()
    assert "phase: awaiting_claude_review" in state


def test_risk_router_covers_provider_architecture_uncertainty_and_explicit_request(
    tmp_path: Path,
) -> None:
    review_json = tmp_path / "review.json"
    changed_files = tmp_path / "changed-files.txt"
    review_json.write_text(
        json.dumps(
            {
                "status": "APPROVED",
                "summary": "검증은 통과했지만 요구사항 해석에 불확실성이 있습니다.",
                "risk_level": "HIGH",
                "claude_escalation": True,
                "escalation_reasons": ["uncertainty"],
                "p1_findings": [],
                "p2_findings": [],
            }
        )
    )
    changed_files.write_text(
        "pyproject.toml\n"
        + "\n".join(f"src/new_architecture/module_{index}.py" for index in range(8))
        + "\n"
    )

    result = subprocess.run(
        [
            "python3",
            str(REVIEW_RESULT_TOOL),
            "risk",
            "--review-json",
            str(review_json),
            "--changed-files",
            str(changed_files),
            "--cycle",
            "1",
            "--force",
        ],
        text=True,
        capture_output=True,
        check=True,
    )

    reasons = set(result.stdout.strip().split(","))
    assert reasons == {
        "architecture_change",
        "explicit_request",
        "external_provider",
        "uncertainty",
    }


def test_second_failed_review_is_classified_as_repeated_fix_failure(tmp_path: Path) -> None:
    review_json = tmp_path / "review.json"
    changed_files = tmp_path / "changed-files.txt"
    review_json.write_text(
        json.dumps(
            {
                "status": "NEEDS_REVISION",
                "summary": "수정 후에도 blocker가 남았습니다.",
                "risk_level": "HIGH",
                "claude_escalation": True,
                "escalation_reasons": ["p1_finding"],
                "p1_findings": [
                    {
                        "location": "src/example.py:10",
                        "issue": "blocker",
                        "fix": "correct it",
                    }
                ],
                "p2_findings": [],
            }
        )
    )
    changed_files.write_text("src/example.py\n")

    result = subprocess.run(
        [
            "python3",
            str(REVIEW_RESULT_TOOL),
            "risk",
            "--review-json",
            str(review_json),
            "--changed-files",
            str(changed_files),
            "--cycle",
            "2",
        ],
        text=True,
        capture_output=True,
        check=True,
    )

    assert set(result.stdout.strip().split(",")) == {
        "p1_finding",
        "repeated_fix_failure",
    }


def test_active_review_flow_uses_only_conditional_claude_escalation() -> None:
    active_files = (
        "run-codex-task.sh",
        "auto-loop-prompt.md",
        "codex-auto-loop-prompt.md",
    )
    for file_name in active_files:
        content = (REPO_ROOT / "scripts" / file_name).read_text()
        assert "Claude review requested" not in content
    review_flow = (REPO_ROOT / "scripts/run-codex-task.sh").read_text() + (
        REPO_ROOT / "scripts/codex_review_result.py"
    ).read_text()
    assert "CLAUDE_BOT_ID" in review_flow
    assert "awaiting_claude_review" in review_flow


def test_codex_output_schema_uses_only_supported_array_keywords() -> None:
    schema = json.loads((REPO_ROOT / "scripts/codex-review-schema.json").read_text())

    assert "uniqueItems" not in schema["properties"]["escalation_reasons"]


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
    assert not (tmp_path / "codex-called").exists()
    log = (project_dir / ".auto-loop/logs/auto-loop.log").read_text()
    assert "10분 PR 승인 poller" in log


def test_awaiting_claude_review_retries_claude_on_scheduled_loop(tmp_path: Path) -> None:
    project_dir, env, claude_called = _auto_loop_env(
        tmp_path,
        "awaiting_claude_review",
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
    assert not (tmp_path / "codex-called").exists()
    log = (project_dir / ".auto-loop/logs/auto-loop.log").read_text()
    assert "조건부 Claude 리뷰 재시도" in log


def test_hung_codex_tick_times_out_and_releases_lock_for_retry(tmp_path: Path) -> None:
    project_dir, env, _ = _auto_loop_env(tmp_path, "idle", [])
    timeout_bin = shutil.which("timeout") or shutil.which("gtimeout")
    assert timeout_bin is not None
    env.update(
        {
            "AUTO_LOOP_AGENT_TIMEOUT_SECONDS": "0.1",
            "AUTO_LOOP_AGENT_KILL_AFTER_SECONDS": "0.1",
            "FAKE_CODEX_SLEEP_SECONDS": "2",
            "TIMEOUT_BIN": timeout_bin,
        }
    )

    started = time.monotonic()
    result = subprocess.run(
        [str(project_dir / "scripts" / "auto-loop.sh")],
        cwd=project_dir,
        env=env,
        text=True,
        capture_output=True,
        check=False,
        timeout=5,
    )
    elapsed = time.monotonic() - started

    assert result.returncode == 0, result.stderr
    assert elapsed < 1.5
    assert not (project_dir / ".auto-loop/auto-loop.lock").exists()
    log_path = project_dir / ".auto-loop/logs/auto-loop.log"
    assert "agent timeout: codex exceeded 0.1s" in log_path.read_text()

    del env["FAKE_CODEX_SLEEP_SECONDS"]
    (tmp_path / "codex-called").unlink()
    retry = subprocess.run(
        [str(project_dir / "scripts" / "auto-loop.sh")],
        cwd=project_dir,
        env=env,
        text=True,
        capture_output=True,
        check=False,
        timeout=5,
    )

    assert retry.returncode == 0, retry.stderr
    assert (tmp_path / "codex-called").exists()


def test_hung_required_claude_review_times_out_without_codex_fallback(
    tmp_path: Path,
) -> None:
    project_dir, env, claude_called = _auto_loop_env(
        tmp_path,
        "awaiting_claude_review",
        [],
    )
    timeout_bin = shutil.which("timeout") or shutil.which("gtimeout")
    assert timeout_bin is not None
    env.update(
        {
            "AUTO_LOOP_AGENT_TIMEOUT_SECONDS": "0.1",
            "AUTO_LOOP_AGENT_KILL_AFTER_SECONDS": "0.1",
            "FAKE_CLAUDE_SLEEP_SECONDS": "2",
            "TIMEOUT_BIN": timeout_bin,
        }
    )

    result = subprocess.run(
        [str(project_dir / "scripts" / "auto-loop.sh")],
        cwd=project_dir,
        env=env,
        text=True,
        capture_output=True,
        check=False,
        timeout=5,
    )

    assert result.returncode == 0, result.stderr
    assert claude_called.exists()
    assert not (tmp_path / "codex-called").exists()
    assert not (project_dir / ".auto-loop/auto-loop.lock").exists()
    state = (project_dir / ".auto-loop/work-status.md").read_text()
    assert "phase: awaiting_claude_review" in state
    log = (project_dir / ".auto-loop/logs/auto-loop.log").read_text()
    assert "agent timeout: claude exceeded 0.1s" in log


def test_required_claude_review_never_falls_back_to_codex_on_session_limit(
    tmp_path: Path,
) -> None:
    project_dir, env, claude_called = _auto_loop_env(
        tmp_path,
        "awaiting_claude_review",
        [],
    )
    env.update(
        {
            "FAKE_CLAUDE_EXIT": "7",
            "FAKE_CLAUDE_OUTPUT": "You've hit your session limit",
        }
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
    assert not (tmp_path / "codex-called").exists()
    state = (project_dir / ".auto-loop/work-status.md").read_text()
    assert "phase: awaiting_claude_review" in state
    log = (project_dir / ".auto-loop/logs/auto-loop.log").read_text()
    assert "Codex fallback 금지" in log


def test_legacy_awaiting_approval_runs_codex_without_new_user_message(tmp_path: Path) -> None:
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
    assert not claude_called.exists()
    assert (tmp_path / "codex-called").exists()


def test_legacy_waiting_phase_uses_codex_even_with_new_user_message(tmp_path: Path) -> None:
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
    assert not claude_called.exists()
    assert (tmp_path / "codex-called").exists()


def test_idle_planning_defaults_to_codex_not_claude(tmp_path: Path) -> None:
    project_dir, env, claude_called = _auto_loop_env(tmp_path, "idle", [])

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
    assert (tmp_path / "codex-called").exists()


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
