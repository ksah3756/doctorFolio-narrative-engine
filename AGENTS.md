# Agent Guidelines: dcf-narrative-engine

Narrative-driven DCF valuation engine. 정성 정보를 정량 입력 분포로 변환하는 Python 엔진.

## 1. Definition of Done (DoD)

Before any task is "Done":

- **Verification:** `make verify` (lint + typecheck + tests) must pass 100%.
- **Quality:** ruff zero warnings, mypy strict zero errors, pytest 100% pass, coverage ≥ 80%.
- **TDD Proof:** Red test 커밋이 Green 구현 커밋보다 시간순 앞에 있어야 함 (commit history로 증명).
- **Reporting:** Plan / Files Changed / Commands Run / Test Results / Remaining Risks.

## 2. Workflow & Execution Protocol

**GitHub Issues = Single Source of Truth.** No coding without an issue.

### Phase 1: Planning (Strict Separation)

- Ambiguity Check: 모호하면 100% 정렬까지 질문.
- Explicit Consent: 수동 작업은 사용자 plan 승인 전 코딩 금지. 단, auto-loop는
  `idle`에서 계획을 Discord에 알린 뒤 승인 대기 없이 구현으로 진행.
- Test Strategy: plan에 작성할 테스트 케이스 명세 포함.

### Phase 2: Implementation (Strict TDD)

1. **Red:** 실패하는 테스트 먼저.
2. **Green:** 통과시키는 최소 코드.
3. **Refactor:** 테스트 유지하며 정리.
4. **Commit:** 테스트와 구현은 TDD 사이클 반영하여 분리 커밋.
5. **Verify:** `make verify` 통과 후 `git status` clean 확인. **작업은 반드시 커밋까지 끝낸다** — staged-only/미커밋으로 완료 보고 금지. (auto-loop dispatch는 `run-codex-task.sh`가 미커밋이면 completed를 차단한다.)

| Role | Owner | Action |
| --- | --- | --- |
| **State Router** | Shell | 상태별 실행 주체 선택; 대기 상태의 불필요한 LLM 호출 차단 |
| **Planner** | Codex | `idle`에서 작업 선정·계획 알림 후 즉시 구현 dispatch |
| **Implementer** | Codex (fresh session) | TDD → `make verify` → commit |
| **Reviewer** | Codex (separate fresh session) | Read-only 독립 리뷰 → `REVIEW-<issue>-<cycle>.md` |
| **Escalation Reviewer** | Claude | 수치 의미·provider·큰 아키텍처·P1·불확실성·반복 실패·명시 요청만 리뷰 |
| **PR Gate** | Shell | 10분 승인 폴링 → PR 생성·머지 → `idle` 초기화 |

### Auto-loop 핵심 프로세스

```text
idle
  → Codex 계획을 Discord에 알림(승인 대기 없음)
  → issue/branch 생성 및 Codex 구현 dispatch
  → 별도 Codex 세션 리뷰
      ├─ 저위험 + P1 0: 리뷰 결과 알림 → awaiting_pr
      └─ 조건부 escalation: awaiting_claude_review → 다음 loop에서 Claude 재시도
  → awaiting_pr: Shell이 10분마다 현재 리뷰 메시지 이후의 정확한 ㄱㄱ/go만 확인
  → PR 생성·머지 → idle
```

- `awaiting_pr`에서는 LLM을 호출하지 않는다.
- `ㄱㄱ`/`go`는 현재 리뷰의 PR 생성·머지 승인에만 사용한다. 과거 승인을 재사용하지
  않도록 리뷰 메시지 ID, issue, branch, phase를 함께 검증한다.
- 일반 Codex 리뷰 결과는 봇 mention 없이 전송하고, Claude mention은 조건부
  escalation일 때만 사용한다.

## 3. Tech Stack

- Language: Python 3.12 (strict typing via mypy)
- Package: uv (또는 poetry, pip-tools)
- Numerics: numpy + scipy
- Models: pydantic v2
- Testing: pytest + hypothesis (property-based for 분포 역산)
- Lint/Format: ruff
- LLM (M2): DeepSeek V4 Flash via OpenAI-compatible SDK
- Graph (M2): neo4j-python-driver
- Macro Data (M2): FRED API, ECOS API

## 4. Code Standards

- Separation of Concerns: 함께 실행되지 않는 코드는 분리.
- Single Responsibility: 모듈/함수당 한 책임.
- Predictability: 일관된 반환 타입과 명명. `Any` 금지(필요시 `TypeAlias` + 명시).
- DRY: 3회 이상 반복 후에만 추출.
- Refactor Trigger:
  - 파일 > 200줄 / 함수 > 50줄
  - 호출 깊이 > 3
- **Deep Modules**: 단순한 인터페이스가 풍부한 구현을 감추도록. shallow wrapper 금지.
- 주요 로직의 경우 구현 의도가 드러나도록 간단한 내용의 한글 주석 추가

### TDD & Testing Rules

- Test First: 실패 테스트 없이 production 코드 금지.
- Coverage: 비즈니스 로직(`distributions.py`, `routing.py`, `loading.py`, `monte_carlo.py`)이 핵심.
- Small Steps: 한 테스트, 한 통과, 다음으로.
- No Mocking Overkill: pure function은 실제 입출력으로.
- Property-based: 분포 모수 역산은 hypothesis로 invariant 검증 (e.g. "복원된 mu, sigma가 원본 ±tolerance 내").

### Tidy First Strategy

- Structural change (rename/extract) vs behavioral change (new logic) — 커밋 분리.
- Prefixes: `refactor:` (structural), `feat:` / `fix:` (behavioral).
- Intent-based messages: 왜 바뀌었는지를 설명.

### Design Principles & Conventions

- pydantic BaseModel for data 모델 (Claim, FactorState, AssumptionState).
- TypedDict / Literal로 finite states 표현 (e.g. `Literal["young", "growth", "mature", "decline"]`).
- 부작용 없는 pure function 선호. I/O는 boundary에만.
- 상수 테이블은 module top-level에 `Final` 타입으로.

### State Management

- 엔진은 stateless 권장. 상태는 명시적으로 dataclass/pydantic 모델로 전달.
- Mutable global state 금지.

### Anti-Patterns (Strictly Forbidden)

- `Any` 무분별 사용
- 무의미한 wrapper class
- magic number (모든 상수는 명명된 테이블)
- LLM 호출에 retry 없이 직접 의존 (M2)

## 5. Review Mandate

Reviewer (독립 Codex 세션) 평가 기준:

1. **TDD Adherence**: Red 테스트가 먼저 커밋되었나?
2. **Tidy First**: 구조/동작 변경 분리되었나?
3. **Safety**: 외부 데이터 입력 validation (pydantic). 분포 모수 NaN/inf 방지.
4. **Numerical Correctness**: 확률분포 모수 역산이 invariant를 지키는가?
5. **Prioritization**:
   - **P1 (Blocker)**: 누락 테스트, verify 실패, 수치 오류.
   - **P2 (Nitpick)**: 선택 개선.

Claude 리뷰는 수치 의미, 외부 provider, 큰 아키텍처, P1, 반복 수정 실패,
요구사항 충돌·사용자 선택, 명시적 요청에만 조건부로 호출한다.
