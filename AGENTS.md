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
- Explicit Consent: 사용자 plan 승인 전 코딩 금지.
- Test Strategy: plan에 작성할 테스트 케이스 명세 포함.

### Phase 2: Implementation (Strict TDD)

1. **Red:** 실패하는 테스트 먼저.
2. **Green:** 통과시키는 최소 코드.
3. **Refactor:** 테스트 유지하며 정리.
4. **Commit:** 테스트와 구현은 TDD 사이클 반영하여 분리 커밋.
5. **Verify:** `make verify` 통과 후 `git status` clean 확인.

| Role            | Owner  | Action                                   |
| --------------- | ------ | ---------------------------------------- |
| **Implementer** | Codex  | TDD → `make verify` → commit             |
| **Reviewer**    | Claude | Review against standards → `REVIEW-N.md` |
| **Git Manager** | Claude | PR 생성/머지 (APPROVED 후)               |

### Auto-loop 명령어 (`ㄱㄱ` / `go`)

Discord auto-loop에서 사용자의 `ㄱㄱ`(또는 `go`)는 **직전 봇 메시지의 게이트를 승인하고 다음 단계로 진행**하라는 뜻. 매번 Discord 기록을 뒤질 필요 없이 직전 메시지가 가리키는 단계를 실행하면 됨. 게이트는 둘:

1. **`📋 다음 작업 제안` 직후** → 제안된 작업을 Codex에 위임 시작 (이슈 생성 → `feat/N-slug` 브랜치 → `scripts/dispatch-codex-task.sh`).
2. **`✅ 리뷰 통과` 직후** → PR 생성 + 머지 (`gh pr create` → `gh pr merge`).

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

Reviewer (Claude) 평가 기준:

1. **TDD Adherence**: Red 테스트가 먼저 커밋되었나?
2. **Tidy First**: 구조/동작 변경 분리되었나?
3. **Safety**: 외부 데이터 입력 validation (pydantic). 분포 모수 NaN/inf 방지.
4. **Numerical Correctness**: 확률분포 모수 역산이 invariant를 지키는가?
5. **Prioritization**:
   - **P1 (Blocker)**: 누락 테스트, verify 실패, 수치 오류.
   - **P2 (Nitpick)**: 선택 개선.
