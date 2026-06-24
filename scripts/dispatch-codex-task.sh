#!/usr/bin/env bash

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="${AUTO_LOOP_PROJECT_DIR:-$(cd "$SCRIPT_DIR/.." && pwd)}"
RUNNER="$SCRIPT_DIR/run-codex-task.sh"

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
    *)
      echo "Usage: $0 --issue <number> --branch <feat/N-slug> --prompt-file <path> [--review-only]" >&2
      exit 2
      ;;
  esac
done

[[ "$issue" =~ ^[0-9]+$ ]] || { echo "invalid issue" >&2; exit 2; }
[[ -x "$RUNNER" ]] || { echo "runner is not executable: $RUNNER" >&2; exit 2; }
[[ -f "$prompt_file" ]] || { echo "prompt file not found: $prompt_file" >&2; exit 2; }
command -v tmux >/dev/null 2>&1 || { echo "tmux is required" >&2; exit 2; }

session="codex-issue-$issue"
if tmux has-session -t "$session" 2>/dev/null; then
  echo "Codex task already running in tmux session: $session" >&2
  exit 1
fi

runner_args=("$RUNNER" --issue "$issue" --branch "$branch" --prompt-file "$prompt_file")
if [[ "$review_only" -eq 1 ]]; then
  runner_args+=(--review-only)
fi
printf -v command '%q ' "${runner_args[@]}"
tmux new-session -d -s "$session" -c "$PROJECT_DIR" "$command"
echo "[codex-dispatch] started $session for $branch"
