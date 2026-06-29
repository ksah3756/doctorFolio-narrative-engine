#!/usr/bin/env bash
# Tests for scripts/lib/worktree.sh::resolve_task_worktree
# Run: bash tests/test_worktree_dispatch.sh
set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
# shellcheck source=/dev/null
source "$REPO_ROOT/scripts/lib/worktree.sh"

fail=0
check() { # check <desc> <cmd...>
  local desc="$1"; shift
  if "$@"; then echo "  ok: $desc"; else echo "  FAIL: $desc" >&2; fail=1; fi
}

make_project() { # make_project <dir> -> a git repo with origin/main + main checked out
  local proj="$1"
  local remote="$proj.remote"
  git init -q --bare "$remote"
  git init -q "$proj"
  git -C "$proj" remote add origin "$remote"
  git -C "$proj" -c user.email=t@t -c user.name=t commit -q --allow-empty -m init
  git -C "$proj" branch -M main
  git -C "$proj" push -q origin main
  git -C "$proj" fetch -q origin
}

TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

# --- Test A: new branch -> isolated worktree created, project HEAD untouched ---
PROJ="$TMP/a/proj"; mkdir -p "$TMP/a"; make_project "$PROJ"
head_before="$(git -C "$PROJ" rev-parse HEAD)"
wt="$(resolve_task_worktree "$PROJ" "feat/76-demo")"
check "A: prints a worktree path" test -n "$wt"
check "A: worktree dir exists" test -d "$wt"
check "A: worktree is at ../worktrees/<slug>" test "$wt" = "$(cd "$PROJ/.." && pwd -P)/worktrees/feat-76-demo"
check "A: branch created" git -C "$PROJ" show-ref --verify --quiet refs/heads/feat/76-demo
check "A: project HEAD unchanged" test "$(git -C "$PROJ" rev-parse HEAD)" = "$head_before"
check "A: branch checked out in the new worktree" test "$(git -C "$wt" branch --show-current)" = "feat/76-demo"

# --- Test B: second call -> idempotent reuse, same path, no error ---
wt2="$(resolve_task_worktree "$PROJ" "feat/76-demo")"; rc=$?
check "B: reuse exit 0" test "$rc" -eq 0
check "B: reuse returns same path" test "$wt2" = "$wt"

# --- Test C: branch checked out in PROJECT_DIR -> ejected to a worktree ---
PROJ2="$TMP/c/proj"; mkdir -p "$TMP/c"; make_project "$PROJ2"
git -C "$PROJ2" switch -q -c feat/76-inroot
wt3="$(resolve_task_worktree "$PROJ2" "feat/76-inroot")"
check "C: ejected to dedicated worktree" test -d "$wt3"
check "C: project_dir moved off the branch" test "$(git -C "$PROJ2" branch --show-current)" = "main"
check "C: branch now lives in the worktree" test "$(git -C "$wt3" branch --show-current)" = "feat/76-inroot"

if [[ "$fail" -ne 0 ]]; then echo "TESTS FAILED" >&2; exit 1; fi
echo "ALL TESTS PASSED"
