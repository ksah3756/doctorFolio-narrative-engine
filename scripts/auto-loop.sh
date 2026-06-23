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
AUTO_LOOP_RUNNER="${AUTO_LOOP_RUNNER:-auto}" # auto | claude | codex

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
  runner="codex"
fi

if [[ "$phase" == "awaiting_pr" ]]; then
  log "phase awaiting_pr: 10분 PR 승인 poller가 처리, LLM 호출 생략"
  exit 0
fi
if [[ "$phase" == "awaiting_claude_review" ]]; then
  log "phase awaiting_claude_review: Discord 조건부 Claude 리뷰가 처리, 정각 LLM 호출 생략"
  exit 0
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
