#!/usr/bin/env bash

set -uo pipefail

usage() {
  echo "Usage: $0 --issue <number> --branch <feat/N-slug> --prompt-file <path>" >&2
}

die() {
  echo "[codex-task] $*" >&2
  exit 2
}

issue=""
branch=""
prompt_file=""

while [[ $# -gt 0 ]]; do
  case "$1" in
    --issue)
      issue="${2:-}"
      shift 2
      ;;
    --branch)
      branch="${2:-}"
      shift 2
      ;;
    --prompt-file)
      prompt_file="${2:-}"
      shift 2
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      usage
      die "unknown argument: $1"
      ;;
  esac
done

[[ "$issue" =~ ^[0-9]+$ ]] || die "--issue must be a number"
[[ "$branch" =~ ^feat/${issue}-[a-z0-9][a-z0-9-]*$ ]] || die "branch must match feat/${issue}-slug"
[[ -f "$prompt_file" ]] || die "prompt file not found: $prompt_file"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="${AUTO_LOOP_PROJECT_DIR:-$(cd "$SCRIPT_DIR/.." && pwd)}"
CODEX_BIN="${CODEX_BIN:-$(command -v codex 2>/dev/null || true)}"
TASK_DIR="$PROJECT_DIR/.auto-loop/tasks"
LOG_DIR="$PROJECT_DIR/.auto-loop/logs"
STATUS_FILE="$TASK_DIR/issue-$issue.json"
EVENT_LOG="$LOG_DIR/codex-issue-$issue.jsonl"
FINAL_MESSAGE="$LOG_DIR/codex-issue-$issue-final.md"

[[ -x "$CODEX_BIN" ]] || die "codex binary not found: ${CODEX_BIN:-unset}"
command -v git >/dev/null 2>&1 || die "git is required"
command -v make >/dev/null 2>&1 || die "make is required"
command -v python3 >/dev/null 2>&1 || die "python3 is required"

mkdir -p "$TASK_DIR" "$LOG_DIR"

write_status() {
  local status="$1"
  local exit_code="$2"
  local stage="$3"
  STATUS_FILE="$STATUS_FILE" ISSUE="$issue" BRANCH="$branch" STATUS="$status" \
    EXIT_CODE="$exit_code" STAGE="$stage" python3 - <<'PY'
import json
import os
from datetime import UTC, datetime
from pathlib import Path

path = Path(os.environ["STATUS_FILE"])
try:
    payload = json.loads(path.read_text())
except (FileNotFoundError, json.JSONDecodeError):
    payload = {}

now = datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")
payload.update(
    {
        "issue": int(os.environ["ISSUE"]),
        "branch": os.environ["BRANCH"],
        "status": os.environ["STATUS"],
        "stage": os.environ["STAGE"],
        "exit_code": int(os.environ["EXIT_CODE"]),
        "updated_at": now,
    }
)
payload.setdefault("started_at", now)
if payload["status"] in {"completed", "failed"}:
    payload["finished_at"] = now

temporary = path.with_suffix(".tmp")
temporary.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")
temporary.replace(path)
PY
}

resolve_webhook_url() {
  if [[ -n "${DISCORD_WEBHOOK_URL:-}" ]]; then
    printf '%s' "$DISCORD_WEBHOOK_URL"
    return
  fi
  /bin/zsh -lc 'source ~/.zshrc >/dev/null 2>&1; printf %s "$DISCORD_WEBHOOK_URL"' 2>/dev/null || true
}

notify_discord() {
  local status="$1"
  if [[ "${AUTO_LOOP_DISABLE_DISCORD:-0}" == "1" ]]; then
    return 0
  fi
  local webhook_url
  webhook_url="$(resolve_webhook_url)"
  [[ -n "$webhook_url" ]] || return 0
  command -v curl >/dev/null 2>&1 || return 0

  local message
  if [[ "$status" == "completed" ]]; then
    message="<@1491798466660139148>
[Codex] Direct Codex task completed. Claude review requested.

- Issue: #$issue
- Branch: $branch
- State: .auto-loop/tasks/issue-$issue.json
- Verification: make verify passed

Review this branch now. For mechanical P1 fixes, write a new task prompt and call scripts/dispatch-codex-task.sh directly. If P1 is zero, ask the user for PR approval."
  else
    message="<@1131404924094251099>
❌ [auto-loop] Direct Codex task failed.

- Issue: #$issue
- Branch: $branch
- State: .auto-loop/tasks/issue-$issue.json
- Log: .auto-loop/logs/codex-issue-$issue.jsonl"
  fi

  local payload
  payload="$(python3 - "$message" <<'PY'
import json
import sys

print(json.dumps({"content": sys.argv[1]}))
PY
)"
  curl -sS -X POST "$webhook_url" -H "Content-Type: application/json" -d "$payload" >/dev/null || true
}

cd "$PROJECT_DIR" || die "cannot enter project: $PROJECT_DIR"

git fetch origin --quiet 2>/dev/null || true
current_branch="$(git branch --show-current)"
if [[ "$current_branch" != "$branch" ]]; then
  if git show-ref --verify --quiet "refs/heads/$branch"; then
    git switch "$branch" || die "cannot switch to existing branch: $branch"
  else
    # 신규 작업 브랜치는 최신 origin/main에서 분기한다 (stale HEAD 분기로 인한 충돌 방지).
    # origin/main이 없으면(원격 미설정/테스트 레포) 현재 HEAD에서 분기로 폴백한다.
    if git rev-parse --verify --quiet origin/main >/dev/null 2>&1; then
      git switch -c "$branch" origin/main || die "cannot create branch from origin/main: $branch"
    else
      git switch -c "$branch" || die "cannot create branch: $branch"
    fi
  fi
fi

# 커밋 가드용 기준 커밋 (Codex 실행 전 HEAD).
head_before="$(git rev-parse HEAD)"

write_status "running" 0 "codex"
: > "$EVENT_LOG"

# workspace-write 샌드박스는 기본적으로 `!**/.git/**`로 .git을 read-only로 막아
# Codex 커밋이 EPERM으로 실패한다(#9·#11·#14 재발). 프로젝트 .git을 명시적으로
# writable_roots에 넣어 이 배제를 무력화하고 TDD 커밋(Red/Green 분리)을 허용한다.
git_writable_root="sandbox_workspace_write.writable_roots=[\"$PROJECT_DIR/.git\"]"

"$CODEX_BIN" --ask-for-approval never exec \
  --cd "$PROJECT_DIR" \
  --sandbox workspace-write \
  -c "$git_writable_root" \
  -c 'model_reasoning_effort="high"' \
  --json \
  --output-last-message "$FINAL_MESSAGE" \
  - < "$prompt_file" >> "$EVENT_LOG" 2>&1
codex_status=$?

if [[ "$codex_status" -ne 0 ]]; then
  write_status "failed" "$codex_status" "codex"
  notify_discord "failed"
  exit "$codex_status"
fi

make verify >> "$EVENT_LOG" 2>&1
verify_status=$?
if [[ "$verify_status" -ne 0 ]]; then
  write_status "failed" "$verify_status" "verify"
  notify_discord "failed"
  exit "$verify_status"
fi

# 커밋 가드: Codex는 작업을 반드시 커밋해야 한다. verify는 통과시키되 staged-only로
# 남기고 완료 보고하는 경우(미커밋)를 차단한다 — 완료 = 새 커밋 존재 + index clean.
head_after="$(git rev-parse HEAD)"
if [[ "$head_after" == "$head_before" ]] || ! git diff --cached --quiet; then
  write_status "failed" 1 "commit"
  notify_discord "failed"
  echo "[codex-task] issue #$issue: 미커밋 변경 감지 — 완료 차단 (HEAD 미전진 또는 staged 잔존)" >&2
  exit 1
fi

write_status "completed" 0 "verify"
notify_discord "completed"
echo "[codex-task] issue #$issue completed on $branch"
