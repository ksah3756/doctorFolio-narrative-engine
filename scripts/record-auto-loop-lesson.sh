#!/usr/bin/env bash

set -euo pipefail

usage() {
  echo "Usage: $0 --issue <number|none> --phase <phase> --title <text> --context <text> --lesson <text> --directive <text> --evidence <text>" >&2
}

issue=""
phase=""
title=""
context=""
lesson=""
directive=""
evidence=""

while [[ $# -gt 0 ]]; do
  case "$1" in
    --issue) issue="${2:-}"; shift 2 ;;
    --phase) phase="${2:-}"; shift 2 ;;
    --title) title="${2:-}"; shift 2 ;;
    --context) context="${2:-}"; shift 2 ;;
    --lesson) lesson="${2:-}"; shift 2 ;;
    --directive) directive="${2:-}"; shift 2 ;;
    --evidence) evidence="${2:-}"; shift 2 ;;
    -h|--help) usage; exit 0 ;;
    *) usage; exit 2 ;;
  esac
done

[[ "$issue" == "none" || "$issue" =~ ^[0-9]+$ ]] || { echo "invalid issue" >&2; exit 2; }
[[ -n "$phase" && -n "$title" && -n "$context" && -n "$lesson" && -n "$directive" && -n "$evidence" ]] || {
  usage
  exit 2
}

sanitize() {
  local value="$1"
  value="${value//$'\r'/ }"
  value="${value//$'\n'/ }"
  value="${value//-->/-- >}"
  printf '%s' "$value"
}

issue="$(sanitize "$issue")"
phase="$(sanitize "$phase")"
title="$(sanitize "$title")"
context="$(sanitize "$context")"
lesson="$(sanitize "$lesson")"
directive="$(sanitize "$directive")"
evidence="$(sanitize "$evidence")"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="${AUTO_LOOP_PROJECT_DIR:-$(cd "$SCRIPT_DIR/.." && pwd)}"
JOURNAL_DIR="$PROJECT_DIR/.auto-loop"
JOURNAL_FILE="$JOURNAL_DIR/lessons.md"
LOCK_DIR="$JOURNAL_DIR/lessons.lock"
ENTRY_KEY="issue:$issue|phase:$phase|title:$title"

mkdir -p "$JOURNAL_DIR"

attempt=0
until mkdir "$LOCK_DIR" 2>/dev/null; do
  attempt=$((attempt + 1))
  if [[ "$attempt" -ge 50 ]]; then
    echo "lesson journal lock timeout" >&2
    exit 1
  fi
  sleep 0.1
done
trap 'rmdir "$LOCK_DIR" 2>/dev/null || true' EXIT

if [[ ! -f "$JOURNAL_FILE" ]]; then
  printf '%s\n\n' '# Auto-Loop Lessons' > "$JOURNAL_FILE"
  printf '%s\n\n' 'Only reusable, evidence-backed lessons belong here. Routine status changes stay in work-status.md and logs.' >> "$JOURNAL_FILE"
fi

marker="<!-- lesson-key: $ENTRY_KEY -->"
if grep -Fq -- "$marker" "$JOURNAL_FILE"; then
  echo "[auto-loop-lesson] duplicate skipped: $title"
  exit 0
fi

timestamp="$(date -u '+%Y-%m-%dT%H:%M:%SZ')"
issue_label="$issue"
if [[ "$issue" != "none" ]]; then
  issue_label="#$issue"
fi

{
  printf '%s\n' "$marker"
  printf '## %s — %s\n\n' "$timestamp" "$title"
  printf -- '- Issue: %s\n' "$issue_label"
  printf -- '- Phase: %s\n' "$phase"
  printf -- '- Context: %s\n' "$context"
  printf -- '- Lesson: %s\n' "$lesson"
  printf -- '- Future directive: %s\n' "$directive"
  printf -- '- Evidence: %s\n\n' "$evidence"
} >> "$JOURNAL_FILE"

echo "[auto-loop-lesson] recorded: $title"
