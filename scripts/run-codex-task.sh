#!/usr/bin/env bash

set -uo pipefail

usage() {
  echo "Usage: $0 --issue <number> --branch <feat/N-slug> --prompt-file <path> [--review-only]" >&2
}

die() {
  echo "[codex-task] $*" >&2
  exit 2
}

issue=""
branch=""
prompt_file=""
review_only=0

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
    --review-only)
      review_only=1
      shift
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
TIMEOUT_BIN="${TIMEOUT_BIN:-$(command -v timeout 2>/dev/null || command -v gtimeout 2>/dev/null || true)}"
CODEX_REVIEW_TIMEOUT_SECONDS="${CODEX_REVIEW_TIMEOUT_SECONDS:-600}"
CODEX_REVIEW_KILL_AFTER_SECONDS="${CODEX_REVIEW_KILL_AFTER_SECONDS:-30}"
CODEX_REVIEW_IGNORE_USER_CONFIG="${CODEX_REVIEW_IGNORE_USER_CONFIG:-1}"
TASK_DIR="$PROJECT_DIR/.auto-loop/tasks"
LOG_DIR="$PROJECT_DIR/.auto-loop/logs"
STATUS_FILE="$TASK_DIR/issue-$issue.json"
EVENT_LOG="$LOG_DIR/codex-issue-$issue.jsonl"
FINAL_MESSAGE="$LOG_DIR/codex-issue-$issue-final.md"
STATE_FILE="$PROJECT_DIR/.auto-loop/work-status.md"
REVIEW_SCHEMA="$SCRIPT_DIR/codex-review-schema.json"
REVIEW_RESULT_TOOL="$SCRIPT_DIR/codex_review_result.py"
CLAUDE_BOT_ID="1491798466660139148"

[[ -x "$CODEX_BIN" ]] || die "codex binary not found: ${CODEX_BIN:-unset}"
command -v git >/dev/null 2>&1 || die "git is required"
command -v make >/dev/null 2>&1 || die "make is required"
command -v python3 >/dev/null 2>&1 || die "python3 is required"
[[ -f "$REVIEW_SCHEMA" ]] || die "review schema not found: $REVIEW_SCHEMA"
[[ -f "$REVIEW_RESULT_TOOL" ]] || die "review result tool not found: $REVIEW_RESULT_TOOL"

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
else:
    payload.pop("finished_at", None)

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

notify_failure() {
  if [[ "${AUTO_LOOP_DISABLE_DISCORD:-0}" == "1" ]]; then
    return 0
  fi
  local webhook_url
  webhook_url="$(resolve_webhook_url)"
  [[ -n "$webhook_url" ]] || return 0
  command -v curl >/dev/null 2>&1 || return 0

  local message
  message="<@1131404924094251099>
❌ [auto-loop] Direct Codex task failed.

- Issue: #$issue
- Branch: $branch
- State: .auto-loop/tasks/issue-$issue.json
- Log: .auto-loop/logs/codex-issue-$issue.jsonl"

  local payload
  payload="$(python3 - "$message" <<'PY'
import json
import sys

print(json.dumps({"content": sys.argv[1]}))
PY
)"
  curl -sS -X POST "$webhook_url" -H "Content-Type: application/json" -d "$payload" >/dev/null || true
}

has_transient_agent_limit() {
  grep -qiE "usage limit|session limit|try again at|resets [0-9]+[ap]m" "$1"
}

is_timeout_status() {
  local status="$1"
  [[ "$status" -eq 124 || "$status" -eq 137 || "$status" -eq 143 ]]
}

run_review_codex() {
  local review_prompt="$1"
  local review_json="$2"
  local review_attempt_log="$3"
  local -a args

  args=("$CODEX_BIN" --ask-for-approval never exec)
  if [[ "$CODEX_REVIEW_IGNORE_USER_CONFIG" != "0" ]]; then
    args+=(--ignore-user-config)
  fi
  args+=(
    --cd "$PROJECT_DIR"
    --sandbox read-only
    --ephemeral
    -c 'model_reasoning_effort="high"'
    --output-schema "$REVIEW_SCHEMA"
    --output-last-message "$review_json"
    -
  )

  if [[ -n "$TIMEOUT_BIN" && -x "$TIMEOUT_BIN" ]]; then
    "$TIMEOUT_BIN" \
      --kill-after="${CODEX_REVIEW_KILL_AFTER_SECONDS}s" \
      "${CODEX_REVIEW_TIMEOUT_SECONDS}s" \
      "${args[@]}" < "$review_prompt" > "$review_attempt_log" 2>&1
    return $?
  fi

  "${args[@]}" < "$review_prompt" > "$review_attempt_log" 2>&1
}

task_value() {
  local key="$1"
  python3 - "$STATUS_FILE" "$key" <<'PY'
import json
import sys
from pathlib import Path

try:
    payload = json.loads(Path(sys.argv[1]).read_text())
except (FileNotFoundError, json.JSONDecodeError):
    raise SystemExit(1)
value = payload.get(sys.argv[2], "")
print(value if value is not None else "")
PY
}

state_value() {
  local key="$1"
  awk -F: -v key="$key" '
    /^---$/ { section += 1; next }
    section == 1 && $1 == key {
      value = $0
      sub(/^[^:]*:/, "", value)
      sub(/#.*/, "", value)
      gsub(/^[[:space:]]+|[[:space:]]+$/, "", value)
      print value
      exit
    }
  ' "$STATE_FILE" 2>/dev/null
}

update_review_state() {
  local cycle="$1"
  local status="$2"
  local message_id="${3:-null}"
  [[ -f "$STATE_FILE" ]] || return 0

  python3 "$REVIEW_RESULT_TOOL" state \
    --state-file "$STATE_FILE" \
    --issue "$issue" \
    --branch "$branch" \
    --cycle "$cycle" \
    --status "$status" \
    --message-id "$message_id"
}

write_review_prompt() {
  local cycle="$1"
  local review_prompt="$2"
  cat > "$review_prompt" <<EOF
You are a fresh, independent Codex code-review agent. Review issue #$issue on branch
$branch against $review_base. The implementation agent's context is intentionally not
available to you.

Inspect the full diff and commit history. Follow AGENTS.md, including TDD ordering,
Tidy First, input safety, numerical correctness, NaN/inf protection, and the Lore
commit protocol. Verification already passed in the outer runner; use read-only
commands for any additional evidence. Do not modify files, create commits, push, or
create a PR.

Classify only release-blocking correctness defects, missing required tests, failed
verification implications, or numerical errors as P1. Classify optional improvements
as P2. Mark risk HIGH and request Claude escalation for DCF/numerical semantics,
external providers, large architecture changes, requirement conflicts, user choices,
or material uncertainty. Return only JSON matching the provided schema. APPROVED
requires zero P1 findings; otherwise return NEEDS_REVISION. This is review cycle $cycle.
EOF
}

format_review() {
  local cycle="$1"
  local review_json="$2"
  local review_report="$3"
  local discord_message="$4"
  local fix_prompt="$5"

  python3 "$REVIEW_RESULT_TOOL" format \
    --review-json "$review_json" \
    --review-report "$review_report" \
    --discord-message "$discord_message" \
    --fix-prompt "$fix_prompt" \
    --issue "$issue" \
    --branch "$branch" \
    --cycle "$cycle"
}

post_review() {
  local message_file="$1"
  if [[ "${AUTO_LOOP_DISABLE_DISCORD:-0}" == "1" ]]; then
    return 0
  fi
  local webhook_url endpoint payload response
  webhook_url="$(resolve_webhook_url)"
  [[ -n "$webhook_url" ]] || return 1
  command -v curl >/dev/null 2>&1 || return 1
  if [[ "$webhook_url" == *\?* ]]; then
    endpoint="${webhook_url}&wait=true"
  else
    endpoint="${webhook_url}?wait=true"
  fi
  payload="$(python3 - "$message_file" <<'PY'
import json
import sys
from pathlib import Path

print(
    json.dumps(
        {
            "content": Path(sys.argv[1]).read_text(),
            "allowed_mentions": {"parse": []},
        }
    )
)
PY
)"
  response="$(curl -sS --fail --max-time 15 -X POST "$endpoint" \
    -H "Content-Type: application/json" -d "$payload")" || return 1
  printf '%s' "$response" | python3 -c '
import json, sys
payload = json.load(sys.stdin)
message_id = str(payload.get("id", ""))
if not message_id.isdigit():
    raise SystemExit(1)
print(message_id)
'
}

post_claude_escalation() {
  local message_file="$1"
  if [[ "${AUTO_LOOP_DISABLE_DISCORD:-0}" == "1" ]]; then
    return 1
  fi
  local webhook_url endpoint payload
  webhook_url="$(resolve_webhook_url)"
  [[ -n "$webhook_url" ]] || return 1
  command -v curl >/dev/null 2>&1 || return 1
  if [[ "$webhook_url" == *\?* ]]; then
    endpoint="${webhook_url}&wait=true"
  else
    endpoint="${webhook_url}?wait=true"
  fi
  payload="$(python3 - "$message_file" "$CLAUDE_BOT_ID" <<'PY'
import json
import sys
from pathlib import Path

print(
    json.dumps(
        {
            "content": Path(sys.argv[1]).read_text(),
            "allowed_mentions": {"users": [sys.argv[2]]},
        }
    )
)
PY
)"
  curl -sS --fail --max-time 15 -X POST "$endpoint" \
    -H "Content-Type: application/json" -d "$payload" >/dev/null
}

classify_review_risk() {
  local review_json="$1"
  local changed_files="$2"
  local cycle="$3"
  if [[ "${AUTO_LOOP_FORCE_CLAUDE_REVIEW:-0}" == "1" ]] || \
      grep -qiE 'claude-review-required|claude review required|Claude 리뷰 필수' "$prompt_file"; then
    python3 "$REVIEW_RESULT_TOOL" risk \
      --review-json "$review_json" \
      --changed-files "$changed_files" \
      --cycle "$cycle" \
      --force
    return
  fi
  python3 "$REVIEW_RESULT_TOOL" risk \
    --review-json "$review_json" \
    --changed-files "$changed_files" \
    --cycle "$cycle"
}

run_implementation() {
  local current_prompt="$1"
  local head_before head_after codex_status verify_status
  head_before="$(git rev-parse HEAD)"
  write_status "running" 0 "codex"

  "$CODEX_BIN" --ask-for-approval never exec \
    --cd "$PROJECT_DIR" \
    --sandbox workspace-write \
    -c "$git_writable_root" \
    -c 'model_reasoning_effort="high"' \
    --json \
    --output-last-message "$FINAL_MESSAGE" \
    - < "$current_prompt" >> "$EVENT_LOG" 2>&1
  codex_status=$?
  if [[ "$codex_status" -ne 0 ]]; then
    write_status "failed" "$codex_status" "codex"
    notify_failure
    return "$codex_status"
  fi

  make verify >> "$EVENT_LOG" 2>&1
  verify_status=$?
  if [[ "$verify_status" -ne 0 ]]; then
    write_status "failed" "$verify_status" "verify"
    notify_failure
    return "$verify_status"
  fi

  head_after="$(git rev-parse HEAD)"
  if [[ "$head_after" == "$head_before" ]] || ! git diff --cached --quiet; then
    write_status "failed" 1 "commit"
    notify_failure
    echo "[codex-task] issue #$issue: 미커밋 변경 감지 — 완료 차단 (HEAD 미전진 또는 staged 잔존)" >&2
    return 1
  fi
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

# workspace-write 샌드박스는 기본적으로 `!**/.git/**`로 .git을 read-only로 막아
# Codex 커밋이 EPERM으로 실패한다(#9·#11·#14 재발). 프로젝트 .git을 명시적으로
# writable_roots에 넣어 이 배제를 무력화하고 TDD 커밋(Red/Green 분리)을 허용한다.
git_writable_root="sandbox_workspace_write.writable_roots=[\"$PROJECT_DIR/.git\"]"

if [[ "$review_only" -eq 1 ]]; then
  [[ "$(task_value status)" == "retryable" && "$(task_value stage)" == "review" ]] || \
    die "--review-only requires retryable review status"
else
  : > "$EVENT_LOG"
  run_implementation "$prompt_file" || exit $?
fi

if [[ "${AUTO_LOOP_DISABLE_DISCORD:-0}" != "1" && ! -f "$STATE_FILE" ]]; then
  write_status "failed" 1 "review_state"
  echo "[codex-task] review state file not found: $STATE_FILE" >&2
  exit 1
fi

review_cycle="$(state_value review_cycle)"
[[ "$review_cycle" =~ ^[0-9]+$ ]] || review_cycle=0
if git rev-parse --verify --quiet origin/main >/dev/null 2>&1; then
  review_base="origin/main"
else
  review_base="main"
fi
changed_files="$LOG_DIR/codex-review-issue-$issue-changed-files.txt"
git diff --name-only "$review_base...HEAD" > "$changed_files"

review_cycle=$((review_cycle + 1))
review_prompt="$TASK_DIR/issue-$issue-review-$review_cycle-prompt.md"
review_json="$LOG_DIR/codex-review-issue-$issue-$review_cycle.json"
review_report="$PROJECT_DIR/REVIEW-$issue-$review_cycle.md"
discord_message="$LOG_DIR/codex-review-issue-$issue-$review_cycle-discord.md"
fix_prompt="$TASK_DIR/issue-$issue-review-$review_cycle-fix.md"
write_review_prompt "$review_cycle" "$review_prompt"
write_status "running" 0 "review"

review_attempt_log="$(mktemp "$LOG_DIR/codex-review-issue-$issue-attempt.XXXXXX")"
run_review_codex "$review_prompt" "$review_json" "$review_attempt_log"
review_status=$?
cat "$review_attempt_log" >> "$EVENT_LOG"
if [[ "$review_status" -ne 0 ]]; then
  if is_timeout_status "$review_status"; then
    write_status "retryable" "$review_status" "review"
    rm -f "$review_attempt_log"
    echo "[codex-task] issue #$issue review timed out after ${CODEX_REVIEW_TIMEOUT_SECONDS}s; next scheduled loop will retry"
    exit 0
  fi
  if has_transient_agent_limit "$review_attempt_log"; then
    write_status "retryable" "$review_status" "review"
    rm -f "$review_attempt_log"
    echo "[codex-task] issue #$issue review deferred by usage limit; next scheduled loop will retry"
    exit 0
  fi
  rm -f "$review_attempt_log"
  write_status "failed" "$review_status" "review"
  notify_failure
  exit "$review_status"
fi
rm -f "$review_attempt_log"

p1_count="$(format_review "$review_cycle" "$review_json" "$review_report" "$discord_message" "$fix_prompt")" || {
  write_status "failed" 1 "review"
  notify_failure
  exit 1
}

escalation_reasons="$(classify_review_risk "$review_json" "$changed_files" "$review_cycle")" || {
  write_status "failed" 1 "review_risk"
  exit 1
}
if [[ -n "$escalation_reasons" ]]; then
  escalation_message="$LOG_DIR/codex-review-issue-$issue-$review_cycle-claude.md"
  python3 "$REVIEW_RESULT_TOOL" escalation \
    --review-message "$discord_message" \
    --output "$escalation_message" \
    --issue "$issue" \
    --branch "$branch" \
    --reasons "$escalation_reasons" \
    --bot-id "$CLAUDE_BOT_ID"
  post_claude_escalation "$escalation_message" || {
    write_status "failed" 1 "claude_notify"
    exit 1
  }
  update_review_state "$review_cycle" "CLAUDE_REVIEW" || {
    write_status "failed" 1 "review_state"
    exit 1
  }
  write_status "escalated" 0 "claude_review"
  echo "[codex-task] issue #$issue escalated to conditional Claude review"
  exit 0
fi

if [[ "$p1_count" -eq 0 ]]; then
  message_id=""
  if [[ "${AUTO_LOOP_DISABLE_DISCORD:-0}" != "1" ]]; then
    message_id="$(post_review "$discord_message")" || {
      write_status "failed" 1 "review_notify"
      exit 1
    }
  fi
  if [[ -f "$STATE_FILE" && -n "$message_id" ]]; then
    update_review_state "$review_cycle" "APPROVED" "$message_id" || {
      write_status "failed" 1 "review_state"
      exit 1
    }
  fi
  write_status "completed" 0 "review"
  echo "[codex-task] issue #$issue implemented and independently reviewed on $branch"
  exit 0
fi

write_status "failed" 1 "review_routing"
echo "[codex-task] P1 review reached low-risk path unexpectedly" >&2
exit 1
