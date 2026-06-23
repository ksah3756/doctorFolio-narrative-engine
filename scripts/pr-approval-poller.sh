#!/usr/bin/env bash

set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="${AUTO_LOOP_PROJECT_DIR:-$(cd "$SCRIPT_DIR/.." && pwd)}"
STATE_FILE="$PROJECT_DIR/.auto-loop/work-status.md"
LOG_DIR="$PROJECT_DIR/.auto-loop/logs"
LOG_FILE="$LOG_DIR/pr-approval-poller.log"
LOCK_DIR="$PROJECT_DIR/.auto-loop/auto-loop.lock"

CURL_BIN="${CURL_BIN:-$(command -v curl 2>/dev/null || true)}"
GH_BIN="${GH_BIN:-$(command -v gh 2>/dev/null || true)}"
GIT_BIN="${GIT_BIN:-$(command -v git 2>/dev/null || true)}"
PYTHON_BIN="${PYTHON_BIN:-$(command -v python3 2>/dev/null || true)}"

REPOSITORY="ksah3756/doctorFolio-narrative-engine"
DISCORD_CHANNEL_ID="1491801767141445655"
DISCORD_USER_ID="1131404924094251099"
DISCORD_BOT_ID="1491798466660139148"
DISCORD_ENV_FILE="${DISCORD_ENV_FILE:-$HOME/.claude/channels/discord/.env}"

mkdir -p "$LOG_DIR"

log() {
  echo "[$(date '+%Y-%m-%d %H:%M:%S')] $*" >>"$LOG_FILE"
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

discord_token() {
  local token="${DISCORD_BOT_TOKEN:-}"
  if [[ -z "$token" && -r "$DISCORD_ENV_FILE" ]]; then
    token="$(sed -n 's/^DISCORD_BOT_TOKEN=//p' "$DISCORD_ENV_FILE" | head -1)"
    token="${token#\"}"
    token="${token%\"}"
    token="${token#\'}"
    token="${token%\'}"
  fi
  printf '%s' "$token"
}

notify_merged() {
  local token="$1"
  local issue="$2"
  local pr_number="$3"
  local pr_url="$4"
  local text payload

  text="<@$DISCORD_USER_ID>
✅ [auto-loop] #$issue PR #$pr_number 생성 및 머지 완료
$pr_url
다음 정각 auto-loop에서 신규 작업을 시작합니다."
  payload="$("$PYTHON_BIN" -c 'import json, sys; print(json.dumps({"content": sys.argv[1]}))' "$text")"
  "$CURL_BIN" -sS --fail --max-time 15 \
    -X POST \
    -H "Authorization: Bot $token" \
    -H "Content-Type: application/json" \
    -d "$payload" \
    "https://discord.com/api/v10/channels/$DISCORD_CHANNEL_ID/messages" \
    >/dev/null 2>&1 || log "#${issue}: merge 알림 전송 실패"
}

reset_state_after_merge() {
  local issue="$1"
  local pr_number="$2"
  STATE_FILE="$STATE_FILE" ISSUE="$issue" PR_NUMBER="$pr_number" "$PYTHON_BIN" - <<'PY'
import os
from datetime import UTC, datetime
from pathlib import Path

path = Path(os.environ["STATE_FILE"])
issue = os.environ["ISSUE"]
pr_number = os.environ["PR_NUMBER"]
lines = path.read_text().splitlines()

values = {
    "phase": "idle",
    "status": "null",
    "issue": "null",
    "branch": "null",
    "proposed_at": "null",
    "delegated_at": "null",
    "review_cycle": "0",
    "escalated_at": "null",
    "updated": datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
    "pr_approval_message_id": "null",
}

frontmatter_end = next(index for index in range(1, len(lines)) if lines[index] == "---")
seen: set[str] = set()
for index in range(1, frontmatter_end):
    key = lines[index].split(":", 1)[0]
    if key in values:
        comment = ""
        if "#" in lines[index]:
            comment = "  #" + lines[index].split("#", 1)[1]
        lines[index] = f"{key}: {values[key]}{comment}"
        seen.add(key)
for key, value in values.items():
    if key not in seen:
        lines.insert(frontmatter_end, f"{key}: {value}")
        frontmatter_end += 1

done_line = f"- #{issue} — merged (PR #{pr_number}), issue closed."
current_index = lines.index("## 🔧 Current")
done_index = lines.index("## ✅ Done")
if not any(line.startswith(f"- #{issue} ") for line in lines[done_index + 1 : current_index]):
    lines.insert(current_index, done_line)
    lines.insert(current_index + 1, "")

current_index = lines.index("## 🔧 Current")
next_heading = next(
    (index for index in range(current_index + 1, len(lines)) if lines[index].startswith("## ")),
    len(lines),
)
lines[current_index + 1 : next_heading] = [
    "- phase=idle → 다음 auto-loop 발화에서 신규 작업을 자동 제안·착수한다.",
    "",
]

temporary = path.with_suffix(".tmp")
temporary.write_text("\n".join(lines) + "\n")
temporary.replace(path)
PY
}

[[ -f "$STATE_FILE" ]] || { log "상태파일 없음"; exit 0; }
[[ "$(state_value phase)" == "awaiting_pr" ]] || exit 0

issue="$(state_value issue)"
branch="$(state_value branch)"
anchor="$(state_value pr_approval_message_id)"

if [[ ! "$issue" =~ ^[0-9]+$ || ! "$branch" =~ ^feat/${issue}-[a-z0-9][a-z0-9-]*$ ]]; then
  log "awaiting_pr 상태값 불완전, 종료"
  exit 0
fi
if [[ ! "$anchor" =~ ^[0-9]+$ ]]; then
  log "#${issue}: review message anchor 없음, 승인 거부"
  exit 0
fi
if [[ ! -x "$CURL_BIN" || ! -x "$GH_BIN" || ! -x "$GIT_BIN" || ! -x "$PYTHON_BIN" ]]; then
  log "#${issue}: 필수 실행파일 없음, 종료"
  exit 0
fi

if ! mkdir "$LOCK_DIR" 2>/dev/null; then
  exit 0
fi
trap 'rmdir "$LOCK_DIR" 2>/dev/null' EXIT

token="$(discord_token)"
if [[ -z "$token" ]]; then
  log "#${issue}: Discord token 없음, 종료"
  exit 0
fi

messages="$(
  "$CURL_BIN" -sS --fail --max-time 15 \
    -H "Authorization: Bot $token" \
    "https://discord.com/api/v10/channels/$DISCORD_CHANNEL_ID/messages?after=$anchor&limit=100"
)" || { log "#${issue}: Discord 조회 실패"; exit 0; }

approval_message_id="$(
  printf '%s' "$messages" | "$PYTHON_BIN" -c '
import json
import re
import sys

try:
    payload = json.load(sys.stdin)
    anchor = int(sys.argv[1])
except (json.JSONDecodeError, TypeError, ValueError):
    raise SystemExit(2)
if not isinstance(payload, list):
    raise SystemExit(2)

user_id = sys.argv[2]
bot_id = re.escape(sys.argv[3])
approved: list[int] = []
for message in payload:
    if not isinstance(message, dict):
        continue
    author = message.get("author")
    if not isinstance(author, dict):
        continue
    if str(author.get("id")) != user_id or author.get("bot") is True:
        continue
    try:
        message_id = int(str(message.get("id")))
    except ValueError:
        continue
    if message_id <= anchor:
        continue
    content = str(message.get("content", ""))
    content = re.sub(rf"<@!?{bot_id}>", "", content).strip().casefold()
    if content in {"ㄱㄱ", "go"}:
        approved.append(message_id)

if not approved:
    raise SystemExit(1)
print(min(approved))
' "$anchor" "$DISCORD_USER_ID" "$DISCORD_BOT_ID"
)"
approval_status=$?
if [[ "$approval_status" -eq 1 ]]; then
  exit 0
fi
if [[ "$approval_status" -ne 0 ]]; then
  log "#${issue}: Discord 응답 파싱 실패, 승인 거부"
  exit 0
fi

# Re-read the gate under the shared lock so a stale poll result cannot mutate a
# newer task that reached awaiting_pr while the Discord request was in flight.
if [[ "$(state_value phase)" != "awaiting_pr" || "$(state_value issue)" != "$issue" || \
      "$(state_value branch)" != "$branch" || "$(state_value pr_approval_message_id)" != "$anchor" ]]; then
  log "#${issue}: 승인 gate 변경 감지, 종료"
  exit 0
fi

cd "$PROJECT_DIR" || exit 0
"$GIT_BIN" show-ref --verify --quiet "refs/heads/$branch" || {
  log "#${issue}: 로컬 브랜치 없음: $branch"
  exit 0
}
"$GIT_BIN" push -u origin "$branch" >>"$LOG_FILE" 2>&1 || {
  log "#${issue}: branch push 실패"
  exit 0
}

pr_json="$("$GH_BIN" pr list -R "$REPOSITORY" --head "$branch" --state all --limit 1 --json number,state,url,isDraft)" || {
  log "#${issue}: PR 조회 실패"
  exit 0
}
pr_record="$(printf '%s' "$pr_json" | "$PYTHON_BIN" -c '
import json, sys
payload = json.load(sys.stdin)
if payload:
    pr = payload[0]
    print(pr["number"], pr["state"], pr["url"], str(pr.get("isDraft", False)).lower(), sep="\t")
')" || { log "#${issue}: PR 응답 파싱 실패"; exit 0; }

if [[ -z "$pr_record" ]]; then
  title="$("$GIT_BIN" log -1 --format=%s "$branch")"
  pr_url="$("$GH_BIN" pr create -R "$REPOSITORY" --base main --head "$branch" --title "$title" --body "Closes #$issue")" || {
    log "#${issue}: PR 생성 실패"
    exit 0
  }
  pr_number="${pr_url##*/}"
  pr_state="OPEN"
  pr_draft="false"
else
  IFS=$'\t' read -r pr_number pr_state pr_url pr_draft <<<"$pr_record"
fi

if [[ "$pr_state" == "CLOSED" ]]; then
  log "#${issue}: 기존 PR #$pr_number closed without merge, 자동 머지 거부"
  exit 0
fi
if [[ "$pr_state" != "MERGED" ]]; then
  if [[ "$pr_draft" == "true" ]]; then
    "$GH_BIN" pr ready "$pr_number" -R "$REPOSITORY" >>"$LOG_FILE" 2>&1 || exit 0
  fi
  "$GH_BIN" pr merge "$pr_number" -R "$REPOSITORY" --merge >>"$LOG_FILE" 2>&1 || {
    log "#${issue}: PR #$pr_number merge 실패"
    exit 0
  }
fi

merged_json="$("$GH_BIN" pr view "$pr_number" -R "$REPOSITORY" --json state,number,url)" || exit 0
merged_record="$(printf '%s' "$merged_json" | "$PYTHON_BIN" -c '
import json, sys
pr = json.load(sys.stdin)
print(pr["state"], pr["number"], pr["url"], sep="\t")
')" || exit 0
IFS=$'\t' read -r merged_state pr_number pr_url <<<"$merged_record"
if [[ "$merged_state" != "MERGED" ]]; then
  log "#${issue}: PR #$pr_number merge 미확인, 상태 유지"
  exit 0
fi

reset_state_after_merge "$issue" "$pr_number" || {
  log "#${issue}: merge 후 상태 초기화 실패"
  exit 0
}
notify_merged "$token" "$issue" "$pr_number" "$pr_url"
log "#${issue}: approval $approval_message_id → PR #$pr_number 생성·머지, idle 초기화"
