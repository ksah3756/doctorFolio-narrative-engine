#!/usr/bin/env bash
# Worktree resolution for codex-task dispatch.
#
# resolve_task_worktree <project_dir> <branch>
#   Ensures <branch> exists and is checked out in a DEDICATED git worktree,
#   never by switching the shared PROJECT_DIR checkout. Prints the absolute
#   worktree path on stdout. Idempotent: a second call for the same branch
#   reuses the existing worktree.
#
#   - Branch missing      -> created from origin/main (falls back to current HEAD).
#   - Branch in a worktree-> that worktree path is reused.
#   - Branch in PROJECT_DIR itself -> PROJECT_DIR is moved back to main, then the
#                            branch is ejected into a dedicated worktree.
#   - New worktree        -> added at <project_dir>/../worktrees/<branch-with-/-as-->.
#
# Returns non-zero (and leaves a message on stderr) on any failure.

resolve_task_worktree() {
  local project_dir="$1" branch="$2"
  local slug="${branch//\//-}"
  local project_abs wt_dir existing

  # Physical (symlink-resolved) path: git reports worktrees this way, so all
  # comparisons and the returned path must use the same canonical form.
  project_abs="$(cd "$project_dir" && pwd -P)" || return 1

  # 1. Ensure the branch exists.
  if ! git -C "$project_dir" show-ref --verify --quiet "refs/heads/$branch"; then
    if git -C "$project_dir" rev-parse --verify --quiet origin/main >/dev/null 2>&1; then
      git -C "$project_dir" branch "$branch" origin/main >/dev/null 2>&1 || return 1
    else
      git -C "$project_dir" branch "$branch" >/dev/null 2>&1 || return 1
    fi
  fi

  # 2. If the branch is already checked out in some worktree, reuse it —
  #    unless that worktree is PROJECT_DIR itself, in which case eject it.
  existing="$(git -C "$project_dir" worktree list --porcelain | awk -v b="refs/heads/$branch" '
    /^worktree /{wt=substr($0,10)}
    /^branch /{if (substr($0,8)==b){print wt; exit}}')"
  if [[ -n "$existing" ]]; then
    if [[ "$existing" != "$project_abs" ]]; then
      printf '%s\n' "$existing"
      return 0
    fi
    # Branch lives in the shared checkout: move PROJECT_DIR off it first.
    if git -C "$project_dir" show-ref --verify --quiet refs/heads/main; then
      git -C "$project_dir" switch main >/dev/null 2>&1 || return 1
    else
      git -C "$project_dir" switch --detach >/dev/null 2>&1 || return 1
    fi
  fi

  # 3. Add a dedicated worktree.
  wt_dir="$(cd "$project_dir/.." && pwd -P)/worktrees/$slug"
  if [[ -e "$wt_dir" ]]; then
    echo "resolve_task_worktree: path exists but branch not registered there: $wt_dir" >&2
    return 1
  fi
  git -C "$project_dir" worktree add "$wt_dir" "$branch" >/dev/null 2>&1 || return 1
  printf '%s\n' "$wt_dir"
}
