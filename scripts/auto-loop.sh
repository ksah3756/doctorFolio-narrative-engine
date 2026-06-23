#!/usr/bin/env bash
#
# auto-loop.sh — launchd가 매시 정각 호출하는 래퍼 (dcf-narrative-engine).
# 헤드리스 에이전트를 1회 실행해 .auto-loop/work-status.md 기반 상태머신을 1단계 전진시킨다.
#
# 가드: 동시 실행 방지(lockdir, 55분 stale 자동복구), 전 구간 로깅.
# 권한: --dangerously-skip-permissions (헤드리스라 사람이 프롬프트를 못 누름).
#       파괴적 동작 차단은 프롬프트(auto-loop-prompt.md)의 가드레일이 담당.

set -uo pipefail

# launchd는 최소 PATH로 실행되므로 직접 보강.
# (gh/node/omc/tmux/make=/opt|/usr, claude=~/.local, bun=~/.bun ← Discord MCP가 `bun run`으로 뜸)
export PATH="/opt/homebrew/bin:$HOME/.local/bin:$HOME/.bun/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin"

# 스크립트 위치 기준으로 프로젝트 루트 도출 (이식성)
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
cd "$PROJECT_DIR" || exit 1

CLAUDE_PROMPT_FILE="$SCRIPT_DIR/auto-loop-prompt.md"
CODEX_PROMPT_FILE="$SCRIPT_DIR/codex-auto-loop-prompt.md"
MCP_CONFIG="$SCRIPT_DIR/auto-loop-mcp.json"   # 헤드리스 -p 모드는 discord 플러그인 MCP를 자동 로드하지 않음 → 명시 주입
LOG_DIR="$PROJECT_DIR/.auto-loop/logs"
LOG_FILE="$LOG_DIR/auto-loop.log"
LOCK_DIR="$PROJECT_DIR/.auto-loop/auto-loop.lock"
STATUS_FILE="$PROJECT_DIR/.auto-loop/work-status.md"
CLAUDE_BIN="${CLAUDE_BIN:-$HOME/.local/bin/claude}"
CODEX_BIN="${CODEX_BIN:-$(command -v codex 2>/dev/null || true)}"
CURL_BIN="${CURL_BIN:-$(command -v curl 2>/dev/null || true)}"
PYTHON_BIN="${PYTHON_BIN:-$(command -v python3 2>/dev/null || true)}"
AUTO_LOOP_RUNNER="${AUTO_LOOP_RUNNER:-auto}" # auto | claude | codex
DISCORD_CHANNEL_ID="1491801767141445655"
DISCORD_USER_ID="1131404924094251099"
DISCORD_ENV_FILE="${DISCORD_ENV_FILE:-$HOME/.claude/channels/discord/.env}"

mkdir -p "$LOG_DIR"

log() { echo "[$(date '+%Y-%m-%d %H:%M:%S')] $*" >>"$LOG_FILE"; }

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
  ' "$STATUS_FILE" 2>/dev/null
}

current_phase() {
  state_value "phase"
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

# Return 0 when the designated user posted after the phase timestamp, 1 when
# no relevant message exists, and 2 when preflight cannot make a safe decision.
has_new_discord_user_message() {
  local since="$1"
  local token messages

  [[ -n "$since" && "$since" != "null" ]] || return 2
  [[ -x "$CURL_BIN" && -x "$PYTHON_BIN" ]] || return 2
  token="$(discord_token)"
  [[ -n "$token" ]] || return 2

  messages="$(
    "$CURL_BIN" -sS --fail --max-time 15 \
      -H "Authorization: Bot $token" \
      "https://discord.com/api/v10/channels/$DISCORD_CHANNEL_ID/messages?limit=100"
  )" || return 2

  printf '%s' "$messages" | "$PYTHON_BIN" -c '
import json
import sys
from datetime import datetime


def parse_timestamp(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("timezone is required")
    return parsed


try:
    payload = json.load(sys.stdin)
    since = parse_timestamp(sys.argv[1])
except (json.JSONDecodeError, TypeError, ValueError):
    raise SystemExit(2)

if not isinstance(payload, list):
    raise SystemExit(2)

user_id = sys.argv[2]
for message in payload:
    if not isinstance(message, dict):
        continue
    author = message.get("author")
    if not isinstance(author, dict) or str(author.get("id")) != user_id:
        continue
    if author.get("bot") is True:
        continue
    timestamp = message.get("timestamp")
    if not isinstance(timestamp, str):
        continue
    try:
        if parse_timestamp(timestamp) > since:
            raise SystemExit(0)
    except ValueError:
        continue

raise SystemExit(1)
' "$since" "$DISCORD_USER_ID"
}

has_claude_session_limit() {
  grep -qiE "session limit|usage limit|resets [0-9]+[ap]m|You've hit your session limit" "$1"
}

run_claude() {
  local prompt
  prompt="$(cat "$CLAUDE_PROMPT_FILE")"
  "$CLAUDE_BIN" \
    --dangerously-skip-permissions \
    --mcp-config "$MCP_CONFIG" \
    -p "$prompt"
}

run_codex() {
  local prompt
  prompt="$(cat "$CODEX_PROMPT_FILE")"
  "$CODEX_BIN" exec \
    --dangerously-bypass-approvals-and-sandbox \
    --dangerously-bypass-hook-trust \
    --cd "$PROJECT_DIR" \
    -c 'model_reasoning_effort="high"' \
    -c 'mcp_servers.discord.command="bun"' \
    -c 'mcp_servers.discord.args=["run", "--cwd", "/Users/kimsangho/.claude/plugins/cache/claude-plugins-official/discord/0.0.4", "--shell=bun", "--silent", "start"]' \
    "$prompt"
}

# --- 동시 실행 방지 (mkdir은 원자적) ---
if ! mkdir "$LOCK_DIR" 2>/dev/null; then
  if [ -d "$LOCK_DIR" ]; then
    lock_age=$(( $(date +%s) - $(stat -f %m "$LOCK_DIR" 2>/dev/null || echo 0) ))
    if [ "$lock_age" -gt 3300 ]; then
      log "stale lock(${lock_age}s) 제거 후 재획득"
      rmdir "$LOCK_DIR" 2>/dev/null
      mkdir "$LOCK_DIR" 2>/dev/null || { log "lock 재획득 실패, 종료"; exit 0; }
    else
      log "이미 실행 중(lock age ${lock_age}s), 이번 발화 건너뜀"
      exit 0
    fi
  fi
fi
trap 'rmdir "$LOCK_DIR" 2>/dev/null' EXIT

# --- 사전 점검 ---
if [ ! -f "$STATUS_FILE" ]; then log "상태파일 없음: $STATUS_FILE"; exit 0; fi
if [ ! -f "$CLAUDE_PROMPT_FILE" ]; then log "Claude 프롬프트 파일 없음: $CLAUDE_PROMPT_FILE"; exit 0; fi
if [ ! -f "$CODEX_PROMPT_FILE" ]; then log "Codex 프롬프트 파일 없음: $CODEX_PROMPT_FILE"; exit 0; fi

# --- 실행 ---
phase="$(current_phase)"
runner="$AUTO_LOOP_RUNNER"

if [ "$runner" = "auto" ]; then
  runner="claude"
fi

if [[ "$phase" == "awaiting_pr" ]]; then
  waiting_since="$(state_value "updated")"
  has_new_discord_user_message "$waiting_since"
  preflight_status=$?
  if [[ "$preflight_status" -eq 1 ]]; then
    log "phase $phase: 새 Discord 유저 메시지 없음, LLM 호출 생략"
    exit 0
  fi
  if [[ "$preflight_status" -eq 2 ]]; then
    log "phase $phase: Discord preflight 실패, 기존 LLM 경로로 폴백"
  else
    log "phase $phase: 새 Discord 유저 메시지 감지, LLM 실행"
  fi
fi

log "===== auto-loop 발화 시작 (phase ${phase:-unknown}, runner $runner) ====="
export MCP_TIMEOUT="${MCP_TIMEOUT:-60000}"

tmp_log="$(mktemp "$LOG_DIR/auto-loop-run.XXXXXX")"

case "$runner" in
  claude)
    if [ ! -x "$CLAUDE_BIN" ]; then log "claude 바이너리 없음: $CLAUDE_BIN"; exit 0; fi
    run_claude >"$tmp_log" 2>&1
    status=$?
    cat "$tmp_log" >>"$LOG_FILE"
    if [ "$status" -ne 0 ] && has_claude_session_limit "$tmp_log"; then
      if [ -x "$CODEX_BIN" ]; then
        log "Claude 세션 리미트 감지, Codex fallback 실행"
        run_codex >>"$LOG_FILE" 2>&1
        status=$?
      else
        log "Codex 바이너리 없음: $CODEX_BIN"
      fi
    fi
    ;;
  codex)
    if [ ! -x "$CODEX_BIN" ]; then log "Codex 바이너리 없음: $CODEX_BIN"; exit 0; fi
    run_codex >"$tmp_log" 2>&1
    status=$?
    cat "$tmp_log" >>"$LOG_FILE"
    ;;
  *)
    log "알 수 없는 AUTO_LOOP_RUNNER: $AUTO_LOOP_RUNNER"
    status=0
    ;;
esac

rm -f "$tmp_log"

log "===== auto-loop 발화 종료 (exit $status) ====="
exit 0
