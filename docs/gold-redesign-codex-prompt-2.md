# Codex 작업 지시: M2.1 벤치마크 Evaluator/Benchmark 재설계 (#2 — 매칭/지표 로직)

## 선행 조건

**#1(`docs/gold-redesign-codex-prompt.md`)이 머지된 상태에서 시작한다.** 즉 다음이 이미 존재:
- `data/benchmark/gold_facts.json` (schema_version 2, fact 단위, 10청크 exhaustive)
- `src/dcf_engine/extraction/gold.py` (`GoldFact`, `NumericFact`, `GoldFactSet`, `load_gold_facts`, `band_for_pct`, `MagnitudeBands` 등)

## 배경 (왜 이걸 하나)

현재 `evaluator.py`의 `evaluate_extraction`은 `(claim_subject, direction, magnitude_qualifier)` 3-tuple을 **전역 multiset 교집합**으로 채점한다. 이게 만든 문제(측정으로 확인됨):

- precision이 추출 정확도가 아니라 **gold/추출 개수 비율**을 잰다 (gold 10 vs 추출 42 → 완벽해도 precision ≤ 0.238).
- 매칭이 **청크 경계를 무시**해서, chunk-07이 0 claim(스키마 실패)인데도 chunk-01의 동일 tuple로 매칭돼 TP가 된다. 실제 per-chunk recall 0.6이 0.8로 보고됨 (**전체 실패가 은폐**).
- magnitude exact-match라 79% vs 85% 같은 미세 차이가 TP→FP.
- `numeric_consistency_rate`는 모델 self-report boolean만 카운트해서 항상 1.0 (무의미).
- 실패 청크의 `latency_ms=0`이 p50 median을 오염.

#2는 채점을 **축 분해(scorecard) + 청크 단위 매칭 + grounding 기반 precision**으로 바꾼다.

## 핵심 설계: 평가 축 분해

| 축 | 지표 | 정의 | gold 의존 |
|---|---|---|---|
| 환각(=precision) | `grounded_precision` | grounding 통과한 claim / 전체 claim | ✗ (청크 본문만) |
| 누락(=recall) | `coverage_recall` | 청크 단위 매칭된 gold fact / 전체 gold fact | ✓ |
| 누락(핵심만) | `primary_coverage_recall` | 매칭된 primary fact / 전체 primary fact | ✓ |
| 숫자 | `numeric_grounding_rate` | 숫자가 청크에 실재하는 claim / 숫자 포함 claim | ✗ |
| 방향 | `direction_accuracy` | 매칭쌍 중 `claim.direction == fact.direction` | matched만 |
| 규모 | `magnitude_accuracy` | 매칭쌍 중 magnitude 허용범위 내 | matched만 |
| 분류 | `subject_accuracy` | 매칭쌍 중 `claim.claim_subject ∈ fact.allowed_subjects` | matched만 |
| 중복 | `redundancy_rate` | grounding 통과했지만 TP로 안 잡힌 claim / grounding 통과 claim | ✓(exhaustive 가정) |

핵심 원칙: **claim이 많이 나오는 건 벌하지 않는다(grounded면 precision 유지). 근거 없는 것(환각)만 precision을 깎는다.** 과분해는 `redundancy_rate`로 관찰만 한다.

## 스코프

수정 대상:
- `src/dcf_engine/extraction/evaluator.py` — 새 매칭/지표 함수 추가, 낡은 multiset 로직 교체.
- `src/dcf_engine/extraction/benchmark.py` — `gold_facts.json` 소비, `BenchmarkResult`/결과 페이로드 필드 교체, p50/cost 계산 수정.
- 삭제: `data/benchmark/gold.json` (구 버전), 구 `GoldLabels`/`load_gold_labels`/`evaluate_extraction`(multiset)/`numeric_consistency_rate`(self-report) — 사용처 제거 후 삭제.
- 관련 기존 테스트 갱신.

**변경 금지**: `src/dcf_engine/claim.py`, `src/dcf_engine/extraction/prompt.py`, `src/dcf_engine/extraction/gold.py`(#1 산출물 — 단 공용 헬퍼 추가는 허용), `data/benchmark/chunks/*`, `data/benchmark/gold_facts.json`.

의존성: **scipy는 이미 설치돼 있다**(`scipy.optimize.linear_sum_assignment` 사용 가능). 새 외부 의존성 추가 금지.

## 매칭 알고리즘 (청크 단위, 1:1 assignment)

청크별로 독립 수행 (전역 합산 금지 — 이게 chunk-07 은폐 버그의 원인).

1. 해당 청크의 예측 claim 리스트 `P`, gold fact 리스트 `G`.
2. 점수 행렬 `S[i][j] = match_score(P[i], G[j])`:
   ```
   match_score = 0.6 * numeric_overlap + 0.4 * text_overlap
   numeric_overlap = |nums(claim) ∩ nums(fact)| / max(1, |nums(fact)|)
   text_overlap    = jaccard(tokens(claim.claim_text), tokens(fact.evidence_span ∪ fact.canonical_statement))
   ```
   - `nums(x)`: 텍스트/numeric_facts에서 정규화한 수치 집합 (천단위 콤마·`$`·`billion`/`million` 정규화, 부동소수 비교는 상대오차 1e-6 또는 소수 3자리 반올림).
   - `tokens`: 소문자화, 영숫자 토큰, 불용어 제거(간단한 set이면 충분).
3. `linear_sum_assignment`로 최대 점수 매칭(비용 = -S). 
4. 매칭쌍 중 `S ≥ τ` (τ = 0.45, 모듈 상수로 노출)만 **TP**로 인정. τ 미만은 미매칭 처리.
5. 결과:
   - `matched_pairs`: [(claim, fact)] (S≥τ)
   - `covered_facts`: matched_pairs의 fact 집합 → `coverage_recall = |covered_facts| / |G_all|` (전 청크 합산)
   - `unmatched_facts` → FN.

## Grounding 판정 (precision 축, gold 무관)

claim 하나가 청크 본문에 근거하는가:
```
grounded(claim, chunk_text):
    nums = nums(claim.claim_text)
    if nums:                      # 숫자 있으면 전부 청크에 존재해야 함
        return nums ⊆ nums(chunk_text)
    else:                         # 정성 claim은 토큰 겹침으로 판정
        return jaccard(tokens(claim.claim_text), tokens(chunk_text)) >= 0.30
```
- `grounded_precision = grounded_claims / total_claims`
- `numeric_grounding_rate = (숫자 전부 청크에 존재하는 claim) / (숫자 포함 claim)`  (분모 0이면 1.0)
- 실패 청크(claims=[])는 분모에 claim 0 기여.

> 이 grounding이 chunk-01의 FY 오라벨 같은 건 못 잡지만(숫자 자체는 청크에 존재) — 그건 #1의 gold `period` 기반 별도 점검 영역. 여기선 숫자/환각만 본다.

## 매칭쌍 conditional 지표

`matched_pairs`에 대해서만:
- `direction_accuracy`: `claim.direction == fact.direction` 비율.
- `magnitude_accuracy`: fact.`magnitude_basis == "numeric"`이면 **정확 일치**, `"qualitative"`면 **±1 밴드 허용**(WEAK<MODERATE<STRONG<EXTREME 순서 인덱스 차 ≤ 1).
- `subject_accuracy`: `claim.claim_subject ∈ fact.allowed_subjects` 비율.
- 분모(매칭쌍 수)가 0이면 각 지표 1.0 (`_ratio` 규약 따름).

## redundancy

```
redundancy_rate = (grounded_claims - |matched_pairs|) / max(1, grounded_claims)
```
exhaustive gold 가정 하에, grounding은 통과했지만 별도 fact를 커버하지 못한 잉여(과분해) claim 비율. **벌점 아님, 관찰용.**

## benchmark.py 수정

- `gold = load_gold_facts(gold_path)`로 교체, `gold_path` 기본값을 `gold_facts.json`로.
- `_run_live`의 실패 청크 처리는 유지(전체 중단 금지).
- **latency p50: 성공 청크만**(`schema_valid and latency_ms > 0`)으로 median. 성공 청크 0개면 0.0.
- **cost**: 합산은 유지하되 `cost_per_chunk_usd`는 그대로 `/chunk_count`. 단 실패 청크 토큰이 client에서 0으로 기록돼 **비용 과소집계**가 남아있음을 결과/리스크에 명시(이번 범위에서 client 수정은 안 함).
- `BenchmarkResult` 필드 교체:
  ```
  model, prompt_version, chunk_count, schema_validation_rate,
  grounded_precision, coverage_recall, primary_coverage_recall,
  numeric_grounding_rate, direction_accuracy, magnitude_accuracy,
  subject_accuracy, redundancy_rate,
  true_positives, false_negatives,        # 청크 단위 매칭 기준
  total_cost_usd, cost_per_chunk_usd, latency_ms_p50, result_path
  ```
  - `precision`/`recall`/`numeric_consistency_rate`(구) 필드 제거.
- 결과 JSON 페이로드(`_result_payload`)도 위 지표로 교체. `responses[]` 구조(claims/error/schema_valid/usage/latency)는 유지.

## evaluator.py 수정

- 신규:
  ```python
  @dataclass(frozen=True)
  class Scorecard:
      true_positives: int
      false_negatives: int
      total_claims: int
      grounded_claims: int
      coverage_recall: float
      primary_coverage_recall: float
      grounded_precision: float
      numeric_grounding_rate: float
      direction_accuracy: float
      magnitude_accuracy: float
      subject_accuracy: float
      redundancy_rate: float

  def score_extraction(
      *,
      gold: GoldFactSet,
      responses: Sequence[ExtractionResponse],   # chunk_id별 claims 포함
      chunk_texts: Mapping[str, str],
  ) -> Scorecard: ...
  ```
  - 청크 단위로 `responses[chunk_id].claims` ↔ `gold.facts_by_chunk[chunk_id]` 매칭.
  - gold에 있는 청크가 responses에 없으면 그 fact 전부 FN.
- 헬퍼: `normalize_numbers(text) -> set[float]`, `tokenize(text) -> set[str]`, `match_claims_to_facts(...)`, `is_grounded(...)`. 매직넘버(가중치 0.6/0.4, τ=0.45, grounding 0.30)는 모듈 상수.
- 구 `evaluate_extraction`, `EvaluationMetrics`, `numeric_consistency_rate`, `GoldLabels`, `load_gold_labels` 제거(사용처 정리 후).
- `_ratio`, `read_json_object`는 재사용 가능.

## 테스트 (TDD — 실패 테스트 먼저)

`tests/extraction/test_scorecard.py` (+ 기존 evaluator 테스트 갱신):

1. **단위 — normalize_numbers**: `"$74.550 billion"`, `"39,589"`, `"88%"` 등 정규화 동치.
2. **단위 — match_score/threshold**: 같은 숫자+텍스트 claim↔fact는 S≥τ, 무관한 쌍은 S<τ.
3. **단위 — is_grounded**: 청크 숫자 포함 claim=grounded, 청크에 없는 숫자(환각) claim=not grounded, 정성 claim 토큰겹침 경계.
4. **단위 — magnitude_accuracy 허용범위**: numeric basis는 한 칸 차이도 fail, qualitative는 ±1 통과/±2 fail.
5. **단위 — subject multi-label**: `claim_subject`가 `allowed_subjects`에 있으면 통과.
6. **청크 단위 격리(회귀 핵심)**: chunk-A의 claim이 chunk-B의 fact와 매칭되지 않음을 합성 데이터로 검증.
7. **통합 — chunk-07 은폐 회귀 방지**: 저장된 Haiku 결과(`data/benchmark/results/claude-haiku-4-5-20251001__20260607T081235Z.json`)의 `responses`를 읽어 `ExtractionResponse`로 재구성 → `score_extraction(gold, responses, chunk_texts)` 실행 → **chunk-07 facts가 FN으로 잡혀 `coverage_recall`이 구 recall(0.8)보다 낮음**을 assert. (전체 실패가 더 이상 은폐되지 않음을 증명)
8. **통합 — grounded_precision이 구 precision(0.19)보다 현저히 높음**: 대부분 claim이 grounding 통과(과분해를 벌하지 않음)하므로 precision이 개수에 좌우되지 않음을 assert.
9. `score_extraction` 결과의 모든 비율 지표가 [0,1].

> 7·8번이 이 작업의 존재 이유다. 반드시 통과시킬 것.

## 완료 기준 (DoD)

- `make verify` 통과 (ruff 0 / mypy --strict 0 / pytest green / coverage ≥ 80%).
- `git status` clean. 구조 변경(refactor:)과 동작 변경(feat:) 커밋 분리.
- 구 `gold.json`·구 multiset 로직·self-report `numeric_consistency_rate` 완전 제거(잔존 import 없음).
- 최종 보고: **Plan / Files Changed / Commands Run / Test Results / Remaining Risks**.
  - Remaining Risks에 명시: (1) 실패 청크 토큰 0 기록으로 cost 과소집계 잔존(client 수정 필요 — 후속), (2) grounding이 휴리스틱(숫자/토큰 기반)이라 의미적 환각·FY 오라벨은 못 잡음 — LLM-judge/period 대조는 후속, (3) gold `label_status=draft`라 freeze 전까지 지표 절대값은 잠정.

## 하지 말 것

- `claim.py`/`prompt.py`/`gold.py`/`gold_facts.json`/`chunks` 변경.
- 새 외부 의존성 추가(scipy/pydantic/표준 라이브러리만).
- LLM-as-judge·임베딩 도입(휴리스틱 매칭으로 충분 — 후속 작업).
- 모델 재실행이나 live API 호출.
