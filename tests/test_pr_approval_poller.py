from __future__ import annotations

import json
import os
import plistlib
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
POLLER = REPO_ROOT / "scripts" / "pr-approval-poller.sh"
PLIST = REPO_ROOT / "scripts" / "com.doctorfolio.autoloop-pr-approval.plist"

DISCORD_USER_ID = "1131404924094251099"
DISCORD_BOT_ID = "1491798466660139148"


def _write_executable(path: Path, content: str) -> None:
    path.write_text(content)
    path.chmod(0o755)


def _state_text(*, anchor: str = "200") -> str:
    return (
        "---\n"
        "phase: awaiting_pr\n"
        "status: null\n"
        "issue: 22\n"
        "branch: feat/22-pr-approval-poller\n"
        "proposed_at: null\n"
        "delegated_at: 2026-06-23T00:00:00Z\n"
        "review_cycle: 1\n"
        "escalated_at: null\n"
        "updated: 2026-06-23T00:10:00Z\n"
        f"pr_approval_message_id: {anchor}\n"
        "---\n\n"
        "## ✅ Done\n"
        "- #17 prior work — merged (PR #18)\n\n"
        "## 🔧 Current\n"
        "- #22 reviewed; PR approval pending.\n"
    )


def _message(
    message_id: str,
    content: str,
    *,
    author_id: str = DISCORD_USER_ID,
    bot: bool = False,
) -> dict[str, object]:
    return {
        "id": message_id,
        "content": content,
        "author": {"id": author_id, "bot": bot},
    }


def _poller_env(
    tmp_path: Path,
    messages: list[dict[str, object]],
    *,
    anchor: str = "200",
    pr_list: list[dict[str, object]] | None = None,
) -> tuple[Path, dict[str, str], Path]:
    project_dir = tmp_path / "project"
    state_dir = project_dir / ".auto-loop"
    bin_dir = tmp_path / "bin"
    state_dir.mkdir(parents=True)
    bin_dir.mkdir()
    (state_dir / "work-status.md").write_text(_state_text(anchor=anchor))

    command_log = tmp_path / "commands.log"
    command_log.write_text("")
    _write_executable(
        bin_dir / "curl",
        "#!/bin/sh\n"
        'printf "curl %s\\n" "$*" >> "$FAKE_COMMAND_LOG"\n'
        'case "$*" in\n'
        '  *"/messages?"*) printf "%s" "$FAKE_DISCORD_MESSAGES" ;;\n'
        '  *) printf \'{"id":"notification-1"}\' ;;\n'
        "esac\n",
    )
    _write_executable(
        bin_dir / "git",
        "#!/bin/sh\n"
        'printf "git %s\\n" "$*" >> "$FAKE_COMMAND_LOG"\n'
        'case "$1" in\n'
        '  show-ref) exit 0 ;;\n'
        '  log) printf "%s\\n" "Implement distress bridge samples" ;;\n'
        "esac\n",
    )
    _write_executable(
        bin_dir / "gh",
        "#!/bin/sh\n"
        'printf "gh %s\\n" "$*" >> "$FAKE_COMMAND_LOG"\n'
        'case "$1 $2" in\n'
        '  "pr list") printf "%s\\n" "$FAKE_PR_LIST" ;;\n'
        '  "pr create") printf \'https://github.com/ksah3756/'
        "doctorFolio-narrative-engine/pull/23\\n' ;;\n"
        '  "pr view") printf \'{"state":"MERGED","number":23,"url":'
        '"https://github.com/ksah3756/doctorFolio-narrative-engine/pull/23"}\\n\' ;;\n'
        "esac\n",
    )

    env = os.environ.copy()
    env.update(
        {
            "AUTO_LOOP_PROJECT_DIR": str(project_dir),
            "CURL_BIN": str(bin_dir / "curl"),
            "GIT_BIN": str(bin_dir / "git"),
            "GH_BIN": str(bin_dir / "gh"),
            "DISCORD_BOT_TOKEN": "test-token",
            "FAKE_COMMAND_LOG": str(command_log),
            "FAKE_DISCORD_MESSAGES": json.dumps(messages),
            "FAKE_PR_LIST": json.dumps(pr_list or []),
        }
    )
    return project_dir, env, command_log


def _run_poller(project_dir: Path, env: dict[str, str]) -> subprocess.CompletedProcess[str]:
    assert POLLER.exists(), "PR approval poller must exist"
    return subprocess.run(
        [str(POLLER)],
        cwd=project_dir,
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )


@pytest.mark.parametrize(
    "messages",
    [
        [_message("199", "ㄱㄱ")],
        [_message("201", "ㄱㄱ", author_id="999")],
        [_message("201", "ㄱㄱ", author_id=DISCORD_BOT_ID, bot=True)],
        [_message("201", "go now")],
        [_message("201", "이전 작업 ㄱㄱ")],
    ],
)
def test_poller_rejects_stale_or_inexact_approvals(
    tmp_path: Path,
    messages: list[dict[str, object]],
) -> None:
    project_dir, env, command_log = _poller_env(tmp_path, messages)

    result = _run_poller(project_dir, env)

    assert result.returncode == 0, result.stderr
    commands = command_log.read_text()
    assert "gh pr create" not in commands
    assert "gh pr merge" not in commands
    assert "phase: awaiting_pr" in (project_dir / ".auto-loop/work-status.md").read_text()


def test_poller_creates_and_merges_for_exact_current_gate_approval(tmp_path: Path) -> None:
    messages = [_message("201", f"<@{DISCORD_BOT_ID}>  ㄱㄱ")]
    project_dir, env, command_log = _poller_env(tmp_path, messages)

    result = _run_poller(project_dir, env)

    assert result.returncode == 0, result.stderr
    commands = command_log.read_text()
    assert "after=200" in commands
    assert "git push" in commands
    assert "gh pr create" in commands
    assert "gh pr merge" in commands
    state = (project_dir / ".auto-loop/work-status.md").read_text()
    assert "phase: idle" in state
    assert "issue: null" in state
    assert "branch: null" in state
    assert "pr_approval_message_id: null" in state
    assert "#22" in state and "PR #23" in state


def test_poller_fails_closed_without_review_message_anchor(tmp_path: Path) -> None:
    project_dir, env, command_log = _poller_env(
        tmp_path,
        [_message("201", "go")],
        anchor="null",
    )

    result = _run_poller(project_dir, env)

    assert result.returncode == 0, result.stderr
    commands = command_log.read_text()
    assert "curl " not in commands
    assert "gh " not in commands
    assert "phase: awaiting_pr" in (project_dir / ".auto-loop/work-status.md").read_text()


def test_poller_recovers_state_when_pr_was_already_merged(tmp_path: Path) -> None:
    messages = [_message("201", "go")]
    project_dir, env, command_log = _poller_env(
        tmp_path,
        messages,
        pr_list=[
            {
                "number": 23,
                "state": "MERGED",
                "url": "https://github.com/ksah3756/doctorFolio-narrative-engine/pull/23",
                "isDraft": False,
            }
        ],
    )

    result = _run_poller(project_dir, env)

    assert result.returncode == 0, result.stderr
    commands = command_log.read_text()
    assert "gh pr create" not in commands
    assert "gh pr merge" not in commands
    assert "phase: idle" in (project_dir / ".auto-loop/work-status.md").read_text()


def test_review_prompts_persist_the_review_message_id_gate() -> None:
    for prompt_name in ("auto-loop-prompt.md", "codex-auto-loop-prompt.md"):
        prompt = (REPO_ROOT / "scripts" / prompt_name).read_text()
        assert "pr_approval_message_id" in prompt
        assert "returned Discord message ID" in prompt or "반환된 Discord 메시지 ID" in prompt


def test_launch_agent_runs_pr_approval_poller_every_ten_minutes() -> None:
    assert PLIST.exists(), "tracked LaunchAgent plist must exist"
    with PLIST.open("rb") as handle:
        payload = plistlib.load(handle)

    assert payload["Label"] == "com.doctorfolio.autoloop-pr-approval"
    assert payload["StartInterval"] == 600
    assert payload["ProgramArguments"][-1].endswith("scripts/pr-approval-poller.sh")
