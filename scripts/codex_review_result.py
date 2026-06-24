#!/usr/bin/env python3

from __future__ import annotations

import argparse
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import TypedDict, cast


class Finding(TypedDict):
    location: str
    issue: str
    fix: str


class Review(TypedDict):
    status: str
    summary: str
    risk_level: str
    claude_escalation: bool
    escalation_reasons: list[str]
    p1_findings: list[Finding]
    p2_findings: list[Finding]


NUMERIC_CORE_FILES = {
    "src/dcf_engine/assumption.py",
    "src/dcf_engine/bridge.py",
    "src/dcf_engine/distributions.py",
    "src/dcf_engine/factor.py",
    "src/dcf_engine/lifecycle.py",
    "src/dcf_engine/loading.py",
    "src/dcf_engine/mature_case.py",
    "src/dcf_engine/monte_carlo.py",
    "src/dcf_engine/routing.py",
}
PROVIDER_FILES = {
    "pyproject.toml",
    "src/dcf_engine/extraction/client.py",
}


def _frontmatter(lines: list[str]) -> tuple[int, dict[str, str]]:
    end = next(index for index in range(1, len(lines)) if lines[index] == "---")
    values: dict[str, str] = {}
    for line in lines[1:end]:
        key, separator, raw = line.partition(":")
        if separator:
            values[key] = raw.split("#", 1)[0].strip()
    return end, values


def update_state(args: argparse.Namespace) -> None:
    path = Path(args.state_file)
    lines = path.read_text().splitlines()
    end, values = _frontmatter(lines)
    if (
        values.get("phase") != "implementing"
        or values.get("issue") != args.issue
        or values.get("branch") != args.branch
    ):
        raise SystemExit("current auto-loop task changed while review was running")

    replacements = {
        "review_cycle": str(args.cycle),
        "status": args.status,
        "updated": datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
    }
    if args.status == "APPROVED":
        if not args.message_id.isdigit():
            raise SystemExit("approved review is missing a Discord message id")
        replacements["phase"] = "awaiting_pr"
        replacements["pr_approval_message_id"] = args.message_id
    elif args.status == "CLAUDE_REVIEW":
        replacements["phase"] = "awaiting_claude_review"
        replacements["pr_approval_message_id"] = "null"

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


def _read_review(path: Path) -> Review:
    payload = json.loads(path.read_text())
    required = {
        "status",
        "summary",
        "risk_level",
        "claude_escalation",
        "escalation_reasons",
        "p1_findings",
        "p2_findings",
    }
    if not isinstance(payload, dict) or set(payload) != required:
        raise SystemExit("review result has unexpected fields")
    status = payload["status"]
    p1 = payload["p1_findings"]
    p2 = payload["p2_findings"]
    if status not in {"APPROVED", "NEEDS_REVISION"}:
        raise SystemExit("invalid review status")
    if (
        not isinstance(payload["summary"], str)
        or not isinstance(p1, list)
        or not isinstance(p2, list)
        or payload["risk_level"] not in {"LOW", "HIGH"}
        or not isinstance(payload["claude_escalation"], bool)
        or not isinstance(payload["escalation_reasons"], list)
    ):
        raise SystemExit("invalid review result types")
    if (status == "APPROVED") != (len(p1) == 0):
        raise SystemExit("review status and P1 count disagree")
    for finding in [*p1, *p2]:
        if not isinstance(finding, dict) or set(finding) != {"location", "issue", "fix"}:
            raise SystemExit("invalid review finding")
        if not all(isinstance(value, str) and value for value in finding.values()):
            raise SystemExit("review finding values must be non-empty strings")
    if not all(isinstance(reason, str) and reason for reason in payload["escalation_reasons"]):
        raise SystemExit("invalid escalation reasons")
    if payload["risk_level"] == "HIGH" and not payload["claude_escalation"]:
        raise SystemExit("high-risk review must request Claude escalation")
    if payload["claude_escalation"] != bool(payload["escalation_reasons"]):
        raise SystemExit("Claude escalation flag and reasons disagree")
    return cast(Review, payload)


def classify_risk(args: argparse.Namespace) -> None:
    review = _read_review(Path(args.review_json))
    changed_files = {
        line.strip() for line in Path(args.changed_files).read_text().splitlines() if line.strip()
    }
    reasons = set(review["escalation_reasons"])
    if review["p1_findings"]:
        reasons.add("p1_finding")
    if changed_files & NUMERIC_CORE_FILES:
        reasons.add("numeric_semantics")
    if changed_files & PROVIDER_FILES:
        reasons.add("external_provider")
    production_files = {path for path in changed_files if path.startswith("src/")}
    if len(production_files) >= 8:
        reasons.add("architecture_change")
    if args.force:
        reasons.add("explicit_request")
    if args.cycle >= 2 and review["p1_findings"]:
        reasons.add("repeated_fix_failure")
    print(",".join(sorted(reasons)))


def _report_findings(items: list[Finding]) -> list[str]:
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


def _discord_message(review: Review, issue: str, branch: str, cycle: int) -> str:
    p1 = review["p1_findings"]
    p2 = review["p2_findings"]
    lines = [
        f"[Codex Review] #{issue} 독립 리뷰 {cycle}차",
        f"결과: P1 {len(p1)}건 / P2 {len(p2)}건",
        review["summary"],
    ]
    for label, items in (("P1", p1), ("P2", p2)):
        for item in items:
            lines.append(f"- {label} {item['location']}: {item['issue']} → {item['fix']}")
    tail: list[str] = []
    if not p1:
        tail = [
            f"브랜치: {branch}",
            "PR 생성+머지를 승인하려면 이 메시지 이후 `ㄱㄱ` 또는 `go`만 보내세요.",
        ]
    elif cycle >= 3:
        tail = ["3회 리뷰 후에도 P1이 남아 자동 재작업을 중단합니다."]
    body = "\n".join(lines)
    suffix = "\n".join(tail)
    separator = "\n" if suffix else ""
    limit = 1900 - len(separator) - len(suffix)
    if len(body) > limit:
        body = body[: max(0, limit - 45)].rstrip() + "\n(전체 결과: repo의 REVIEW 파일 참조)"
    return (body + separator + suffix).replace("<@", "@")


def write_escalation(args: argparse.Namespace) -> None:
    review_lines = Path(args.review_message).read_text().splitlines()
    review_message = "\n".join(
        line
        for line in review_lines
        if not line.startswith("브랜치: ") and "PR 생성+머지를 승인하려면" not in line
    )
    prefix = [
        f"<@{args.bot_id}>",
        f"[auto-loop] #{args.issue} 조건부 Claude 리뷰 요청",
        f"브랜치: {args.branch}",
        f"사유: {args.reasons}",
        "Codex 독립 리뷰 결과:",
    ]
    suffix = [
        "프로젝트 루트에서 scripts/auto-loop-prompt.md의 "
        "phase: awaiting_claude_review 절차를 실행하세요.",
    ]
    fixed = "\n".join([*prefix, *suffix])
    review_limit = 1900 - len(fixed) - 2
    if len(review_message) > review_limit:
        review_message = (
            review_message[: max(0, review_limit - 45)].rstrip()
            + "\n(전체 결과: repo의 REVIEW 파일 참조)"
        )
    message = "\n".join([*prefix, review_message, *suffix])
    Path(args.output).write_text(message)


def format_review(args: argparse.Namespace) -> None:
    review = _read_review(Path(args.review_json))
    p1 = review["p1_findings"]
    p2 = review["p2_findings"]
    verdict = "APPROVE" if not p1 else "REVISE"
    report_lines = [
        "---",
        f"cycle: {args.cycle}",
        f"branch: {args.branch}",
        f"status: {review['status']}",
        f"risk_level: {review['risk_level']}",
        f"claude_escalation: {str(review['claude_escalation']).lower()}",
        f"escalation_reasons: {','.join(review['escalation_reasons']) or 'none'}",
        f"p1_count: {len(p1)}",
        f"p2_count: {len(p2)}",
        "---",
        f"## Summary\n{review['summary']}",
        "## P1 (must fix)",
        *_report_findings(p1),
        "## P2 (optional)",
        *_report_findings(p2),
        "## Implementer Response",
        "<!-- Codex implementer fills this -->",
        f"## Verdict: {verdict}",
    ]
    report = "\n".join(report_lines) + "\n"
    Path(args.review_report).write_text(report)
    Path(args.discord_message).write_text(
        _discord_message(review, args.issue, args.branch, args.cycle)
    )
    if p1:
        Path(args.fix_prompt).write_text(
            f"Issue #{args.issue} branch {args.branch}의 독립 Codex 리뷰 P1을 수정하세요.\n\n"
            f"{report}\n"
            "P1만 범위 내에서 strict TDD로 수정하고 make verify를 통과시키세요. "
            "테스트와 구현 커밋을 분리하고 Lore commit protocol을 따르세요. "
            "종료 전 반드시 변경을 커밋하며 PR은 생성하지 마세요.\n"
        )
    print(len(p1))


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest="command", required=True)
    state = subparsers.add_parser("state")
    for name in ("state_file", "issue", "branch", "cycle", "status", "message_id"):
        state.add_argument(f"--{name.replace('_', '-')}", required=True)
    state.set_defaults(handler=update_state)

    formatter = subparsers.add_parser("format")
    for name in (
        "review_json",
        "review_report",
        "discord_message",
        "fix_prompt",
        "issue",
        "branch",
    ):
        formatter.add_argument(f"--{name.replace('_', '-')}", required=True)
    formatter.add_argument("--cycle", type=int, required=True)
    formatter.set_defaults(handler=format_review)

    risk = subparsers.add_parser("risk")
    risk.add_argument("--review-json", required=True)
    risk.add_argument("--changed-files", required=True)
    risk.add_argument("--cycle", type=int, required=True)
    risk.add_argument("--force", action="store_true")
    risk.set_defaults(handler=classify_risk)

    escalation = subparsers.add_parser("escalation")
    for name in ("review_message", "output", "issue", "branch", "reasons", "bot_id"):
        escalation.add_argument(f"--{name.replace('_', '-')}", required=True)
    escalation.set_defaults(handler=write_escalation)
    return parser


def main() -> None:
    args = _parser().parse_args()
    args.handler(args)


if __name__ == "__main__":
    main()
