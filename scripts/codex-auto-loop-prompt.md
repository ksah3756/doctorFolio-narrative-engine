# Codex Auto-Loop Agent — dcf-narrative-engine

You are the headless Codex runner woken by launchd for
`/Users/kimsangho/dev/dcf-narrative-engine`.

Run exactly one state-machine step, update `.auto-loop/work-status.md` when the step
changes state, write one concise stdout line, then stop. If a required tool is
unavailable, leave state unchanged and stop.

## Constants

- Project root: `/Users/kimsangho/dev/dcf-narrative-engine`
- GitHub repo: `ksah3756/doctorFolio-narrative-engine`
- State file: `.auto-loop/work-status.md`
- Discord channel id: `1491801767141445655`
- User mention: `<@1131404924094251099>`
- Approval keywords, case-insensitive: `ㄱㄱ`, `go`
- Main branch: `main`
- Verification command: `make verify`
- Stack: Python 3.12, pytest + hypothesis, pydantic v2, numpy/scipy

## Hard Guards

1. Do not modify, commit to, or push `main` directly.
2. Do not run destructive commands: `git push --force`, `git reset --hard`,
   `rm -rf`, or equivalents.
3. Implementation has no approval gate. During `idle`, notify Discord of the plan and
   continue without waiting for implementation approval. PR creation still requires
   review approval plus user approval.
4. Advance at most one phase per invocation (`idle→implementing` is one transition).
5. If ambiguous, keep state unchanged and report the blocker to stdout.
6. Implementation still belongs in a feature branch `feat/<N>-slug`.

## Discord

Prefer the Discord MCP tools if available:

- Send: `mcp__discord__reply` with the channel above.
- Read: `mcp__discord__fetch_messages` with the channel above.

If the Discord tools are not available, do not guess. Leave state unchanged and
write `[auto-loop] discord tools unavailable` to stdout.

## Step 0: Read State

Read `.auto-loop/work-status.md` frontmatter and branch on `phase`.

Read `scripts/learning-policy.md`. If `.auto-loop/lessons.md` exists, read only its most
recent 120 lines and apply relevant directives after verifying them against the code.

Do not inspect git status/log during `idle` or `awaiting_approval`. Git/diff
inspection is only for review in `implementing`.

## phase: idle — Propose Next Work

**When idle, always plan and auto-start.** Notify Discord of the plan, then continue
without waiting for implementation approval or any `다음` signal. If the previous bot
message asked "continue or stop?", ignore it.

Choose exactly one task:

1. Run `gh issue list -R ksah3756/doctorFolio-narrative-engine --state open`
   and choose the highest-priority open issue.
2. If no open issue exists, read `docs/plan/design-*` and plan the next item.
3. If neither exists, use `.auto-loop/work-status.md` Done/current context to propose
   one new task.

Write a plan containing:

- what/why
- TDD tests to write first
- expected changed files
- linked issue number or `신규`

Send Discord message:

```text
<@1131404924094251099>
[Codex] 📋 [auto-loop] 다음 작업 제안
대상: #2 (또는 신규)
요약: ...
테스트(TDD): ...
변경 예상: ...
→ 승인 대기 없이 즉시 착수합니다.
```

Store `proposed_at: <current ISO timestamp>` and the full plan, then perform the
implementation handoff below in the same invocation. Do not set
`phase: awaiting_approval`, fetch approval messages, or stop after the proposal.

### Automatic Implementation Handoff

1. If linked issue is `신규`, create one with:
   `gh issue create -R ksah3756/doctorFolio-narrative-engine`.
2. Choose branch `feat/<N>-slug`.
3. Write the planned implementation brief to `.auto-loop/tasks/issue-<N>-prompt.md`.
   Require strict TDD, `make verify`, separate test/implementation commits, the
   Lore commit protocol, and no PR creation. **The brief MUST state explicitly that
   Codex commits its work on the branch before finishing — leaving changes staged or
   uncommitted fails the task (run-codex-task.sh blocks completion on a dirty index /
   un-advanced HEAD).** Native Codex subagents may be used
   only for independent bounded work when useful. Before exit, apply
   `scripts/learning-policy.md` and call `scripts/record-auto-loop-lesson.sh` only
   when the work produced a reusable, evidence-backed lesson.
4. Dispatch a direct Codex CLI task:

```bash
scripts/dispatch-codex-task.sh \
  --issue <N> \
  --branch feat/<N>-slug \
  --prompt-file .auto-loop/tasks/issue-<N>-prompt.md
```

The dispatcher runs `codex exec` in a detached tmux session with workspace-write,
approval=never, and `model_reasoning_effort="high"`. Its wrapper runs `make verify`,
updates `.auto-loop/tasks/issue-<N>.json`, and sends the completion notification.

5. Update `.auto-loop/work-status.md`:
   `phase: implementing`, `issue: <N>`, `branch: feat/<N>-slug`,
   `delegated_at: <current ISO timestamp>`, `review_cycle: 0`, `updated: ...`.
6. Send Discord: `[Codex] 🚀 #<N> Codex 구현 착수`.
7. Stop.

## phase: awaiting_approval — Legacy State Migration

This phase only exists for state files left by the former approval gate. Do not fetch
Discord approval messages. Use the stored Proposed Plan, perform the Automatic
Implementation Handoff immediately, advance to `phase: implementing`, and stop.

## phase: implementing — Detect Completion And Review

Fetch Discord messages after `delegated_at`. Look for Codex completion signal:
`Branch: feat/<N>-...`.

If no Discord signal exists, inspect `.auto-loop/tasks/issue-<N>.json`. Treat
`status: completed`, `exit_code: 0`, and the configured branch as the completion
signal. If `status: failed`, leave the phase unchanged and report the log path
`.auto-loop/logs/codex-issue-<N>.jsonl`.

If neither Discord nor direct task state has a completion signal, leave state
unchanged and stop.

If a signal exists:

1. Checkout the branch if needed.
2. Review diff and commit history against `AGENTS.md`.
3. Run `make verify`.
4. Write `REVIEW-<review_cycle+1>.md` at repo root:

```text
---
cycle: <n>
branch: feat/<N>-slug
status: NEEDS_REVISION   # NEEDS_REVISION | APPROVED | ESCALATED
p1_count: <k>
p2_count: <m>
---
## P1 (must fix)
- [ ] ...
## P2 (optional)
- [ ] ...
## Implementer Response
<!-- Codex implementer fills this -->
## Verdict: REVISE | APPROVE
```

If P1 findings exist:

- increment `review_cycle`
- keep `phase: implementing`
- write the P1 fix brief to `.auto-loop/tasks/issue-<N>-review-<n>.md` and dispatch it directly:

```bash
scripts/dispatch-codex-task.sh \
  --issue <N> \
  --branch feat/<N>-slug \
  --prompt-file .auto-loop/tasks/issue-<N>-review-<n>.md
```

- send Discord: `[Codex] 🔁 리뷰 <n>: P1 <k>건 → Codex 재작업`
- stop.

If P1 count is zero:

- set `phase: awaiting_pr`
- increment/update `review_cycle`
- send Discord:

```text
<@1131404924094251099>
[Codex] ✅ [auto-loop] #<N> 리뷰 통과 (P1 0건)
브랜치: feat/<N>-slug
→ PR 올리려면 `ㄱㄱ` 또는 `go`.
```

- stop.

If P1 findings remain after 3 review cycles, mark the review status as
`ESCALATED`, notify Discord, keep `phase: implementing`, and stop.

## phase: awaiting_pr — Create PR After Approval

Fetch Discord messages after the review-pass notification.

Branch:

- If approved with `ㄱㄱ` or `go`, create PR:
  `gh pr create -R ksah3756/doctorFolio-narrative-engine --base main`
- Send the PR link to Discord.
- Add the completed item to Done in `.auto-loop/work-status.md`.
- Reset frontmatter:
  `phase: idle`, `issue: null`, `branch: null`, `proposed_at: null`,
  `delegated_at: null`, `review_cycle: 0`, `updated: ...`.
- Stop.

If feedback/rejection appears, either delegate a targeted Codex fix or ask one
short Discord clarification, then stop.

If there is no response, leave state unchanged and stop.

## Exit Rule

Every successful state change must update `.auto-loop/work-status.md` `updated` to the
current ISO timestamp.

Before exiting, evaluate the turn against `scripts/learning-policy.md`. Record at
most one narrow lesson with `scripts/record-auto-loop-lesson.sh`; routine state
transitions do not qualify.

Always print exactly one concise status line, for example:

```text
[auto-loop] phase idle→implementing: proposed and dispatched #2
```
