# Auto-Loop Learning Policy

The learning journal is `.auto-loop/lessons.md`. It supplements operational logs; it does not replace
`.auto-loop/work-status.md`, task JSON, tests, review files, Git history, or issue/PR discussion.

## Read

At the start of a coordinator or implementation turn, read the most recent 120 lines when the
journal exists. Apply relevant directives, but verify that they still match the current code.

## Record

Record a lesson only when at least one condition is true:

- an unexpected failure required a non-obvious workaround;
- the root cause was materially different from the initial hypothesis;
- a rejected approach is likely to be retried by a future agent;
- a tool, API, scheduler, or repository constraint is reusable beyond the current task;
- a review found a recurring class of defect or a missing invariant.

Do not record routine success, phase transitions, raw command output, task-specific facts already
captured by an issue or review, speculation without evidence, or generic advice.

Use `scripts/record-auto-loop-lesson.sh`. Write one narrow lesson per invocation with concrete
evidence and an actionable future directive. The helper suppresses duplicate issue/phase/title
entries and serializes concurrent appends.

