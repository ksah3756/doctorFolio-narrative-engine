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
STATE_FILE="$PROJECT_DIR/.auto-loop/work-status.md"
REVIEW_SCHEMA="$SCRIPT_DIR/codex-review-schema.json"
MAX_REVIEW_CYCLES=3

[[ -x "$CODEX_BIN" ]] || die "codex binary not found: ${CODEX_BIN:-unset}"
command -v git >/dev/null 2>&1 || die "git is required"
command -v make >/dev/null 2>&1 || die "make is required"
command -v python3 >/dev/null 2>&1 || die "python3 is required"
[[ -f "$REVIEW_SCHEMA" ]] || die "review schema not found: $REVIEW_SCHEMA"

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

  STATE_FILE="$STATE_FILE" ISSUE="$issue" BRANCH="$branch" CYCLE="$cycle" \
    REVIEW_STATUS="$status" MESSAGE_ID="$message_id" python3 - <<'PY'
import os
from datetime import UTC, datetime
from pathlib import Path

path = Path(os.environ["STATE_FILE"])
lines = path.read_text().splitlines()
end = next(index for index in range(1, len(lines)) if lines[index] == "---")

values: dict[str, str] = {}
for line in lines[1:end]:
    key, separator, raw = line.partition(":")
    if separator:
        values[key] = raw.split("#", 1)[0].strip()

if (
    values.get("phase") != "implementing"
    or values.get("issue") != os.environ["ISSUE"]
    or values.get("branch") != os.environ["BRANCH"]
):
    raise SystemExit("current auto-loop task changed while review was running")

status = os.environ["REVIEW_STATUS"]
replacements = {
    "review_cycle": os.environ["CYCLE"],
    "status": status,
    "updated": datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
}
if status == "APPROVED":
    message_id = os.environ["MESSAGE_ID"]
    if not message_id.isdigit():
        raise SystemExit("approved review is missing a Discord message id")
    replacements["phase"] = "awaiting_pr"
    replacements["pr_approval_message_id"] = message_id

seen: set[str] = set()
for index in range(1, end):
    key = lines[index].split(":", 1)[0]
    if key not in replacements:
        continue
    comment = ""
    if "#" in lines[index]:
        comment = "  #" + lines[index].split("#", 1)[1]
    lines[index] = f"{key}: {replacements[key]}{comment}"
    seen.add(key)
for key, value in replacements.items():
    if key not in seen:
        lines.insert(end, f"{key}: {value}")
        end += 1

temporary = path.with_suffix(".tmp")
temporary.write_text("\n".join(lines) + "\n")
temporary.replace(path)
PY
}

write_review_prompt() {
  local cycle="$1"
  local review_prompt="$2"
  cat > "$review_prompt" <<EOF
You are a fresh, independent Codex code-review agent. Review issue #$issue on branch
$branch against origin/main. The implementation agent's context is intentionally not
available to you.

Inspect the full diff and commit history. Follow AGENTS.md, including TDD ordering,
Tidy First, input safety, numerical correctness, NaN/inf protection, and the Lore
commit protocol. Verification already passed in the outer runner; use read-only
commands for any additional evidence. Do not modify files, create commits, push, or
create a PR.

Classify only release-blocking correctness defects, missing required tests, failed
verification implications, or numerical errors as P1. Classify optional improvements
as P2. Return only JSON matching the provided schema. APPROVED requires zero P1
findings; otherwise return NEEDS_REVISION. This is review cycle $cycle.
EOF
}

format_review() {
  local cycle="$1"
  local review_json="$2"
  local review_report="$3"
  local discord_message="$4"
  local fix_prompt="$5"

  REVIEW_JSON="$review_json" REVIEW_REPORT="$review_report" DISCORD_MESSAGE="$discord_message" \
    FIX_PROMPT="$fix_prompt" ISSUE="$issue" BRANCH="$branch" CYCLE="$cycle" python3 - <<'PY'
import json
import os
from pathlib import Path

payload = json.loads(Path(os.environ["REVIEW_JSON"]).read_text())
required = {"status", "summary", "p1_findings", "p2_findings"}
if set(payload) != required:
    raise SystemExit("review result has unexpected fields")
status = payload["status"]
p1 = payload["p1_findings"]
p2 = payload["p2_findings"]
if status not in {"APPROVED", "NEEDS_REVISION"}:
    raise SystemExit("invalid review status")
if not isinstance(payload["summary"], str) or not isinstance(p1, list) or not isinstance(p2, list):
    raise SystemExit("invalid review result types")
if (status == "APPROVED") != (len(p1) == 0):
    raise SystemExit("review status and P1 count disagree")
for finding in [*p1, *p2]:
    if not isinstance(finding, dict) or set(finding) != {"location", "issue", "fix"}:
        raise SystemExit("invalid review finding")

cycle = int(os.environ["CYCLE"])
issue = os.environ["ISSUE"]
branch = os.environ["BRANCH"]
verdict = "APPROVE" if not p1 else "REVISE"

def report_findings(items: list[dict[str, str]]) -> list[str]:
    if not items:
        return ["- 없음"]
    lines: list[str] = []
    for item in items:
        lines.extend(
            [
                f"- [ ] `{item['location']}` — {item['issue']}",
                f"  - 수정: {item['fix']}",
            ]
        )
    return lines

report_lines = [
    "---",
    f"cycle: {cycle}",
    f"branch: {branch}",
    f"status: {status}",
    f"p1_count: {len(p1)}",
    f"p2_count: {len(p2)}",
    "---",
    f"## Summary\n{payload['summary']}",
    "## P1 (must fix)",
    *report_findings(p1),
    "## P2 (optional)",
    *report_findings(p2),
    "## Implementer Response",
    "<!-- Codex implementer fills this -->",
    f"## Verdict: {verdict}",
]
Path(os.environ["REVIEW_REPORT"]).write_text("\n".join(report_lines) + "\n")

discord_lines = [
    f"[Codex Review] #{issue} 독립 리뷰 {cycle}차",
    f"결과: P1 {len(p1)}건 / P2 {len(p2)}건",
    payload["summary"],
]
for label, items in (("P1", p1), ("P2", p2)):
    for item in items:
        discord_lines.append(f"- {label} {item['location']}: {item['issue']} → {item['fix']}")
tail: list[str] = []
if not p1:
    tail = [
        f"브랜치: {branch}",
        "PR 생성+머지를 승인하려면 이 메시지 이후 `ㄱㄱ` 또는 `go`만 보내세요.",
    ]
elif cycle >= 3:
    tail = ["3회 리뷰 후에도 P1이 남아 자동 재작업을 중단합니다."]
body = "\n".join(discord_lines)
suffix = "\n".join(tail)
separator = "\n" if suffix else ""
limit = 1900 - len(separator) - len(suffix)
if len(body) > limit:
    body = body[: max(0, limit - 45)].rstrip() + "\n(전체 결과: repo의 REVIEW 파일 참조)"
message = body + separator + suffix
Path(os.environ["DISCORD_MESSAGE"]).write_text(message)

if p1:
    report = Path(os.environ["REVIEW_REPORT"]).read_text()
    Path(os.environ["FIX_PROMPT"]).write_text(
        f"Issue #{issue} branch {branch}의 독립 Codex 리뷰 P1을 수정하세요.\n\n"
        f"{report}\n"
        "P1만 범위 내에서 strict TDD로 수정하고 make verify를 통과시키세요. "
        "테스트와 구현 커밋을 분리하고 Lore commit protocol을 따르세요. "
        "종료 전 반드시 변경을 커밋하며 PR은 생성하지 마세요.\n"
    )
print(len(p1))
PY
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

print(json.dumps({"content": Path(sys.argv[1]).read_text()}))
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

: > "$EVENT_LOG"

# workspace-write 샌드박스는 기본적으로 `!**/.git/**`로 .git을 read-only로 막아
# Codex 커밋이 EPERM으로 실패한다(#9·#11·#14 재발). 프로젝트 .git을 명시적으로
# writable_roots에 넣어 이 배제를 무력화하고 TDD 커밋(Red/Green 분리)을 허용한다.
git_writable_root="sandbox_workspace_write.writable_roots=[\"$PROJECT_DIR/.git\"]"

run_implementation "$prompt_file" || exit $?

review_cycle="$(state_value review_cycle)"
[[ "$review_cycle" =~ ^[0-9]+$ ]] || review_cycle=0

while [[ "$review_cycle" -lt "$MAX_REVIEW_CYCLES" ]]; do
  review_cycle=$((review_cycle + 1))
  review_prompt="$TASK_DIR/issue-$issue-review-$review_cycle-prompt.md"
  review_json="$LOG_DIR/codex-review-issue-$issue-$review_cycle.json"
  review_report="$PROJECT_DIR/REVIEW-$review_cycle.md"
  discord_message="$LOG_DIR/codex-review-issue-$issue-$review_cycle-discord.md"
  fix_prompt="$TASK_DIR/issue-$issue-review-$review_cycle-fix.md"
  write_review_prompt "$review_cycle" "$review_prompt"
  write_status "running" 0 "review"

  "$CODEX_BIN" --ask-for-approval never exec \
    --cd "$PROJECT_DIR" \
    --sandbox read-only \
    --ephemeral \
    -c 'model_reasoning_effort="high"' \
    --output-schema "$REVIEW_SCHEMA" \
    --output-last-message "$review_json" \
    - < "$review_prompt" >> "$EVENT_LOG" 2>&1
  review_status=$?
  if [[ "$review_status" -ne 0 ]]; then
    write_status "failed" "$review_status" "review"
    notify_failure
    exit "$review_status"
  fi

  p1_count="$(format_review "$review_cycle" "$review_json" "$review_report" "$discord_message" "$fix_prompt")" || {
    write_status "failed" 1 "review"
    notify_failure
    exit 1
  }

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

  post_review "$discord_message" >/dev/null || {
    write_status "failed" 1 "review_notify"
    exit 1
  }
  next_review_status="NEEDS_REVISION"
  if [[ "$review_cycle" -ge "$MAX_REVIEW_CYCLES" ]]; then
    next_review_status="ESCALATED"
  fi
  update_review_state "$review_cycle" "$next_review_status" || {
    write_status "failed" 1 "review_state"
    exit 1
  }
  if [[ "$review_cycle" -ge "$MAX_REVIEW_CYCLES" ]]; then
    write_status "failed" 1 "review_escalated"
    exit 1
  fi
  run_implementation "$fix_prompt" || exit $?
done
