# Codex 작업 지시: M2.1 벤치마크 Gold 재설계 (#1 — 데이터/스키마/모델만)

## 배경 (왜 이걸 하나)

현재 `data/benchmark/gold.json`은 `Claim` 모델을 그대로 재사용하고, **청크당 대표 claim 1개**만 라벨돼 있다.
`evaluator.py`는 이를 `(claim_subject, direction, magnitude_qualifier)` 3-tuple의 **전역 multiset 교집합**으로 채점한다.

이 조합 때문에 지표가 모델 실력을 반영하지 못한다(측정해서 확인된 사실):

1. **precision 구조적 상한**: gold 10개 vs 모델 추출 42개 → 모든 추출이 완벽해도 `precision ≤ 10/42 = 0.238`. 실제로 0.19를 받았다. precision이 추출 정확도가 아니라 개수 비율을 잰다.
2. **청크 경계 무시 → 전체 실패 은폐**: chunk-07은 스키마 실패로 0 claim인데, gold-07의 tuple이 **chunk-01**의 동일 tuple로 매칭돼 TP로 잡힌다. 실제 per-chunk recall은 0.6인데 0.8로 보고됨.
3. **단일 라벨 강제**: 한 사실이 여러 subject에 타당하게 매핑될 수 있는데(예: 영업이익 증가 = FINANCIAL_HEALTH이자 DEMAND_SIGNAL) 단일 정답만 인정.
4. **magnitude 주관성**: 4단계 척도 exact-match라 79% vs 85% 같은 미세 차이가 TP→FP로 뒤집힌다.
5. **gold 자체 모순**: 예) gross margin "increased"를 `direction: DECREASE`로 라벨.
6. **numeric_consistency_rate=1.0 무의미**: 모델 self-report boolean만 카운트(실제 숫자 검증 없음).

이 작업(#1)은 위 문제를 풀 수 있는 **fact 단위 gold 데이터와 스키마/모델/검증 테스트**를 만든다.
**평가 로직(evaluator/benchmark) 변경은 별도 작업(#2)이며 이번 범위가 아니다.**

## 스코프 (엄격히 지킬 것)

- **추가만 한다(additive-only).** 다음 기존 파일은 **수정 금지**: `data/benchmark/gold.json`, `src/dcf_engine/extraction/evaluator.py`, `src/dcf_engine/extraction/benchmark.py`, `src/dcf_engine/claim.py`.
- 새로 만들 것:
  1. `data/benchmark/gold_facts.json` — fact 단위, 10개 청크 전부, exhaustive.
  2. `src/dcf_engine/extraction/gold.py` — Pydantic 모델 + 로더 + 인변량 검증.
  3. `tests/extraction/test_gold_facts.py` (경로는 기존 테스트 구조에 맞춰 조정) — 아래 invariant 전부 테스트.
- `make verify` (ruff + mypy --strict + pytest, **coverage ≥ 80%**) 100% 통과.
- **TDD**: 실패 테스트 먼저 → 모델/데이터로 통과. 구조 변경(refactor:)과 동작 추가(feat:) 커밋 분리.

## 설계: gold_facts.json 스키마 (schema_version 2)

```jsonc
{
  "schema_version": 2,
  "label_status": "draft_pending_user_freeze",
  "source_filing": {                       // 기존 gold.json의 source_filing 그대로 복사
    "company": "NVIDIA CORP",
    "form": "10-Q",
    "accession": "0001045810-26-000052",
    "filing_date": "2026-05-20",
    "period_end": "2026-04-26",
    "url": "https://www.sec.gov/Archives/edgar/data/1045810/000104581026000052/nvda-20260426.htm"
  },
  "magnitude_bands": {                      // 객관적 임계값 — eval(#2)이 읽어 쓴다
    "basis": "abs_relative_pct_change",
    "WEAK":     {"min": 0,  "max": 10},     // [0,10)
    "MODERATE": {"min": 10, "max": 30},     // [10,30)
    "STRONG":   {"min": 30, "max": 70},     // [30,70)
    "EXTREME":  {"min": 70, "max": null}    // [70, inf)
  },
  "labeling_rule": "Facts are chunk-scoped and exhaustive. A predicted claim matches a gold fact when it is grounded in the same evidence_span / numeric_facts. Multiple predicted claims may map to one fact (many-to-one). subject is correct if it is in allowed_subjects. direction is derived from current vs prior values. magnitude is derived from magnitude_bands. Numeric exactness, direction, magnitude, and subject are scored as separate axes, not as one exact-match tuple.",
  "facts_by_chunk": {
    "chunk-01-revenue-overview": [ /* GoldFact[] */ ]
    // ... 모든 청크
  }
}
```

### GoldFact 필드

| 필드 | 타입 | 의미 / 규칙 |
|---|---|---|
| `fact_id` | str | `fact-<NN>-<MM>` (NN=청크 번호, MM=일련번호). 청크 내 유일. |
| `canonical_statement` | str | 사람이 읽는 정규 진술 1문장. |
| `evidence_span` | str | **청크 본문에서 그대로 잘라낸 verbatim 부분문자열** (헤더 `#` 줄 제외, 공백 정규화 후 substring 이어야 함). grounding 앵커. |
| `allowed_subjects` | `ClaimSubject[]` | **비어있지 않은** 집합, 중복 없음. 타당한 subject를 전부 나열(multi-label). |
| `direction` | `ClaimDirection` | INCREASE/DECREASE/NEUTRAL. current vs prior 수치가 있으면 그와 일치해야 함. |
| `magnitude_qualifier` | `MagnitudeQualifier` | `magnitude_basis=numeric`이면 change_pct를 `magnitude_bands`에 넣은 결과와 일치해야 함. |
| `magnitude_basis` | `"numeric" \| "qualitative"` | numeric=수치 기반(밴드로 결정론적), qualitative=정성 판단(±1 밴드 허용은 #2에서). |
| `acceptable_natures` | `ClaimNature[]` | 비어있지 않은 집합. 타당한 nature 전부(예: REALIZED, RISK_FLAG). |
| `period` | `{current: str, prior: str \| null}` | 회계 라벨. 예: `{"current":"Q1 FY2027","prior":"Q1 FY2026"}`. (chunk-01 FY 오라벨 잡는 용도) |
| `numeric_facts` | `NumericFact[]` | 청크에 존재하는 수치들. 정성 사실은 `[]` 가능. |
| `macro_variable` | `MacroVariable \| null` | `allowed_subjects`에 `MACRO_EXPOSURE`가 있으면 필수, 아니면 null. |
| `salience` | `"primary" \| "secondary"` | headline급 = primary, 보조/구성요소/드라이버 = secondary. (#2의 가중 recall용) |

### NumericFact 필드

| 필드 | 타입 | 규칙 |
|---|---|---|
| `metric` | str | 예: `"Compute & Networking revenue"`. |
| `value` | float | **청크 본문에 등장하는 수치여야 함**(grounding 테스트 대상). |
| `unit` | `"USD_BN" \| "USD_MN" \| "PCT" \| "PCT_POINT" \| "SHARES_MN" \| "USD_PER_SHARE" \| "RATIO" \| "COUNT"` | |
| `period` | `"current" \| "prior" \| "change" \| "change_pct"` | 값이 가리키는 시점/성격. |

### 핵심 라벨링 규칙 (granularity — precision 문제의 본질 수정)

- **한 경제적 관계 = 한 fact.** 같은 지표의 절대값과 증가율은 **하나의 fact** 안 `numeric_facts`에 함께 담는다(절대값/증가율을 별개 claim으로 쪼개지 않는다).
  - 예: R&D는 `{abs current 6.321, abs prior 3.989, change 2.332, change_pct 58}`을 한 fact로.
- **구성요소와 합계는 별개 fact**지만, 합계가 단순 합산 파생이면 `salience: secondary` 고려(단 headline 총매출/총비용은 primary 가능).
- **원인/드라이버도 별개 fact**로(예: gross margin 증가 ↔ 전년 H20 charge로 인한 전년 마진 하락은 서로 다른 두 fact, direction도 다름).
- 청크에 있는 valuation 관련 사실을 **빠짐없이(exhaustive)** 라벨한다.

## 완성 예시 2개 (이 포맷·granularity 그대로 나머지 청크 작성)

### chunk-01-revenue-overview (본문 수치 기준)

```json
[
  {
    "fact_id": "fact-01-01",
    "canonical_statement": "Compute & Networking revenue increased 88% year over year to $74.550 billion in Q1 FY2027.",
    "evidence_span": "Compute & Networking revenue of $74.550 billion compared with $39.589 billion, a year-over-year increase of $34.961 billion, or 88%",
    "allowed_subjects": ["DEMAND_SIGNAL", "FINANCIAL_HEALTH"],
    "direction": "INCREASE",
    "magnitude_qualifier": "EXTREME",
    "magnitude_basis": "numeric",
    "acceptable_natures": ["REALIZED"],
    "period": {"current": "Q1 FY2027", "prior": "Q1 FY2026"},
    "numeric_facts": [
      {"metric": "Compute & Networking revenue", "value": 74.550, "unit": "USD_BN", "period": "current"},
      {"metric": "Compute & Networking revenue", "value": 39.589, "unit": "USD_BN", "period": "prior"},
      {"metric": "Compute & Networking revenue", "value": 34.961, "unit": "USD_BN", "period": "change"},
      {"metric": "Compute & Networking revenue", "value": 88, "unit": "PCT", "period": "change_pct"}
    ],
    "macro_variable": null,
    "salience": "primary"
  },
  {
    "fact_id": "fact-01-02",
    "canonical_statement": "Graphics revenue increased 58% year over year to $7.065 billion in Q1 FY2027.",
    "evidence_span": "Graphics revenue was $7.065 billion compared with $4.473 billion, a year-over-year increase of $2.592 billion, or 58%",
    "allowed_subjects": ["DEMAND_SIGNAL", "FINANCIAL_HEALTH"],
    "direction": "INCREASE",
    "magnitude_qualifier": "STRONG",
    "magnitude_basis": "numeric",
    "acceptable_natures": ["REALIZED"],
    "period": {"current": "Q1 FY2027", "prior": "Q1 FY2026"},
    "numeric_facts": [
      {"metric": "Graphics revenue", "value": 7.065, "unit": "USD_BN", "period": "current"},
      {"metric": "Graphics revenue", "value": 4.473, "unit": "USD_BN", "period": "prior"},
      {"metric": "Graphics revenue", "value": 2.592, "unit": "USD_BN", "period": "change"},
      {"metric": "Graphics revenue", "value": 58, "unit": "PCT", "period": "change_pct"}
    ],
    "macro_variable": null,
    "salience": "secondary"
  },
  {
    "fact_id": "fact-01-03",
    "canonical_statement": "Total revenue increased 85% year over year to $81.615 billion in Q1 FY2027.",
    "evidence_span": "Total revenue was $81.615 billion compared with $44.062 billion, an increase of $37.553 billion, or 85%",
    "allowed_subjects": ["DEMAND_SIGNAL", "FINANCIAL_HEALTH"],
    "direction": "INCREASE",
    "magnitude_qualifier": "EXTREME",
    "magnitude_basis": "numeric",
    "acceptable_natures": ["REALIZED"],
    "period": {"current": "Q1 FY2027", "prior": "Q1 FY2026"},
    "numeric_facts": [
      {"metric": "Total revenue", "value": 81.615, "unit": "USD_BN", "period": "current"},
      {"metric": "Total revenue", "value": 44.062, "unit": "USD_BN", "period": "prior"},
      {"metric": "Total revenue", "value": 37.553, "unit": "USD_BN", "period": "change"},
      {"metric": "Total revenue", "value": 85, "unit": "PCT", "period": "change_pct"}
    ],
    "macro_variable": null,
    "salience": "primary"
  },
  {
    "fact_id": "fact-01-04",
    "canonical_statement": "Compute & Networking operating income increased 142% year over year to $53.335 billion in Q1 FY2027.",
    "evidence_span": "Compute & Networking operating income was $53.335 billion compared with $22.054 billion, up 142%",
    "allowed_subjects": ["FINANCIAL_HEALTH"],
    "direction": "INCREASE",
    "magnitude_qualifier": "EXTREME",
    "magnitude_basis": "numeric",
    "acceptable_natures": ["REALIZED"],
    "period": {"current": "Q1 FY2027", "prior": "Q1 FY2026"},
    "numeric_facts": [
      {"metric": "Compute & Networking operating income", "value": 53.335, "unit": "USD_BN", "period": "current"},
      {"metric": "Compute & Networking operating income", "value": 22.054, "unit": "USD_BN", "period": "prior"},
      {"metric": "Compute & Networking operating income", "value": 142, "unit": "PCT", "period": "change_pct"}
    ],
    "macro_variable": null,
    "salience": "primary"
  },
  {
    "fact_id": "fact-01-05",
    "canonical_statement": "Graphics operating income increased 79% year over year to $2.941 billion in Q1 FY2027.",
    "evidence_span": "Graphics operating income was $2.941 billion compared with $1.640 billion, up 79%",
    "allowed_subjects": ["FINANCIAL_HEALTH"],
    "direction": "INCREASE",
    "magnitude_qualifier": "EXTREME",
    "magnitude_basis": "numeric",
    "acceptable_natures": ["REALIZED"],
    "period": {"current": "Q1 FY2027", "prior": "Q1 FY2026"},
    "numeric_facts": [
      {"metric": "Graphics operating income", "value": 2.941, "unit": "USD_BN", "period": "current"},
      {"metric": "Graphics operating income", "value": 1.640, "unit": "USD_BN", "period": "prior"},
      {"metric": "Graphics operating income", "value": 79, "unit": "PCT", "period": "change_pct"}
    ],
    "macro_variable": null,
    "salience": "secondary"
  },
  {
    "fact_id": "fact-01-06",
    "canonical_statement": "Total reportable segment operating income increased 138% year over year to $56.276 billion in Q1 FY2027.",
    "evidence_span": "Total reportable segment operating income was $56.276 billion compared with $23.694 billion, up 138%",
    "allowed_subjects": ["FINANCIAL_HEALTH"],
    "direction": "INCREASE",
    "magnitude_qualifier": "EXTREME",
    "magnitude_basis": "numeric",
    "acceptable_natures": ["REALIZED"],
    "period": {"current": "Q1 FY2027", "prior": "Q1 FY2026"},
    "numeric_facts": [
      {"metric": "Total reportable segment operating income", "value": 56.276, "unit": "USD_BN", "period": "current"},
      {"metric": "Total reportable segment operating income", "value": 23.694, "unit": "USD_BN", "period": "prior"},
      {"metric": "Total reportable segment operating income", "value": 138, "unit": "PCT", "period": "change_pct"}
    ],
    "macro_variable": null,
    "salience": "primary"
  }
]
```

> 주의: 79%는 밴드상 EXTREME(≥70)이다. 기존 결과의 "STRONG"은 주관 오류였다 — 밴드대로 결정론적으로 라벨할 것.

### chunk-06-operating-expenses (granularity 규칙 시연: 절대값+증가율 = 한 fact)

```json
[
  {
    "fact_id": "fact-06-01",
    "canonical_statement": "Research and development expense increased 58% year over year to $6.321 billion in Q1 FY2027.",
    "evidence_span": "Research and development expense was $6.321 billion for the three months ended April 26, 2026, compared with $3.989 billion for the three months ended April 27, 2025, an increase of $2.332 billion, or 58%",
    "allowed_subjects": ["COST_SIGNAL"],
    "direction": "INCREASE",
    "magnitude_qualifier": "STRONG",
    "magnitude_basis": "numeric",
    "acceptable_natures": ["REALIZED"],
    "period": {"current": "Q1 FY2027", "prior": "Q1 FY2026"},
    "numeric_facts": [
      {"metric": "R&D expense", "value": 6.321, "unit": "USD_BN", "period": "current"},
      {"metric": "R&D expense", "value": 3.989, "unit": "USD_BN", "period": "prior"},
      {"metric": "R&D expense", "value": 2.332, "unit": "USD_BN", "period": "change"},
      {"metric": "R&D expense", "value": 58, "unit": "PCT", "period": "change_pct"}
    ],
    "macro_variable": null,
    "salience": "primary"
  },
  {
    "fact_id": "fact-06-02",
    "canonical_statement": "Sales, general and administrative expense increased 25% year over year to $1.300 billion in Q1 FY2027.",
    "evidence_span": "Sales, general and administrative expense was $1.300 billion compared with $1.041 billion, an increase of $259 million, or 25%",
    "allowed_subjects": ["COST_SIGNAL"],
    "direction": "INCREASE",
    "magnitude_qualifier": "MODERATE",
    "magnitude_basis": "numeric",
    "acceptable_natures": ["REALIZED"],
    "period": {"current": "Q1 FY2027", "prior": "Q1 FY2026"},
    "numeric_facts": [
      {"metric": "SG&A expense", "value": 1.300, "unit": "USD_BN", "period": "current"},
      {"metric": "SG&A expense", "value": 1.041, "unit": "USD_BN", "period": "prior"},
      {"metric": "SG&A expense", "value": 259, "unit": "USD_MN", "period": "change"},
      {"metric": "SG&A expense", "value": 25, "unit": "PCT", "period": "change_pct"}
    ],
    "macro_variable": null,
    "salience": "secondary"
  },
  {
    "fact_id": "fact-06-03",
    "canonical_statement": "Total operating expenses increased 52% year over year to $7.621 billion in Q1 FY2027.",
    "evidence_span": "Total operating expenses were $7.621 billion compared with $5.030 billion, an increase of $2.591 billion, or 52%",
    "allowed_subjects": ["COST_SIGNAL"],
    "direction": "INCREASE",
    "magnitude_qualifier": "STRONG",
    "magnitude_basis": "numeric",
    "acceptable_natures": ["REALIZED"],
    "period": {"current": "Q1 FY2027", "prior": "Q1 FY2026"},
    "numeric_facts": [
      {"metric": "Total operating expenses", "value": 7.621, "unit": "USD_BN", "period": "current"},
      {"metric": "Total operating expenses", "value": 5.030, "unit": "USD_BN", "period": "prior"},
      {"metric": "Total operating expenses", "value": 2.591, "unit": "USD_BN", "period": "change"},
      {"metric": "Total operating expenses", "value": 52, "unit": "PCT", "period": "change_pct"}
    ],
    "macro_variable": null,
    "salience": "primary"
  },
  {
    "fact_id": "fact-06-04",
    "canonical_statement": "Compute and infrastructure costs rose 112% year over year, a primary driver of R&D growth.",
    "evidence_span": "a 112% increase in compute and infrastructure",
    "allowed_subjects": ["COST_SIGNAL"],
    "direction": "INCREASE",
    "magnitude_qualifier": "EXTREME",
    "magnitude_basis": "numeric",
    "acceptable_natures": ["REALIZED"],
    "period": {"current": "Q1 FY2027", "prior": "Q1 FY2026"},
    "numeric_facts": [
      {"metric": "compute and infrastructure cost", "value": 112, "unit": "PCT", "period": "change_pct"}
    ],
    "macro_variable": null,
    "salience": "secondary"
  },
  {
    "fact_id": "fact-06-05",
    "canonical_statement": "Compensation and benefits rose 31% year over year, reflecting employee growth.",
    "evidence_span": "a 31% increase in compensation and benefits",
    "allowed_subjects": ["COST_SIGNAL"],
    "direction": "INCREASE",
    "magnitude_qualifier": "STRONG",
    "magnitude_basis": "numeric",
    "acceptable_natures": ["REALIZED"],
    "period": {"current": "Q1 FY2027", "prior": "Q1 FY2026"},
    "numeric_facts": [
      {"metric": "compensation and benefits", "value": 31, "unit": "PCT", "period": "change_pct"}
    ],
    "macro_variable": null,
    "salience": "secondary"
  },
  {
    "fact_id": "fact-06-06",
    "canonical_statement": "Engineering development materials costs rose 204% year over year for new product introductions.",
    "evidence_span": "a 204% increase in engineering development materials for new product introductions",
    "allowed_subjects": ["COST_SIGNAL"],
    "direction": "INCREASE",
    "magnitude_qualifier": "EXTREME",
    "magnitude_basis": "numeric",
    "acceptable_natures": ["REALIZED"],
    "period": {"current": "Q1 FY2027", "prior": "Q1 FY2026"},
    "numeric_facts": [
      {"metric": "engineering development materials", "value": 204, "unit": "PCT", "period": "change_pct"}
    ],
    "macro_variable": null,
    "salience": "secondary"
  }
]
```

> chunk-06이 모델 9개 → gold 6개로 줄어든 이유: 절대값과 증가율을 한 fact로 합쳤기 때문. 이게 count 기반 precision 폭발을 막는 핵심.

## 나머지 청크 작성 지침 (chunk 02,03,04,05,07,08,09,10)

`data/benchmark/chunks/*.txt` 본문을 읽고 위 포맷으로 exhaustive하게 작성. 특히:

- **chunk-04 geographic mix**: 해외 매출 비중 42%→22% 감소는 `MARKET_STRUCTURE`와 `DEMAND_SIGNAL` 둘 다 `allowed_subjects`에 넣어라(기존 gold가 단일 subject로 FN 유발했음). 단위는 `PCT`, `period: change`는 22-42 같은 음수 대신 current=22, prior=42로.
- **chunk-05 gross margin**: gross margin 60.5%→74.9% **증가**는 `direction: INCREASE`(기존 gold의 DECREASE는 오류). 단위 `PCT`. 그리고 "전년 H20 $4.5B charge로 전년 마진이 눌렸다"는 **별도 fact**(`direction: DECREASE`, `allowed_subjects: ["COST_SIGNAL"]`, value 4.5 USD_BN). magnitude는 pp 변화라 `magnitude_basis: "qualitative"`로 두고 STRONG 부여(밴드는 상대%change에만 적용).
- **chunk-07 investment-gains-tax**: other income/이자/세금 관련 사실. 금리 노출이 핵심이면 `allowed_subjects`에 `MACRO_EXPOSURE`를 넣고 그 fact에 `macro_variable: "RATE"`를 반드시 채울 것(아니면 null). (참고: 모델이 이 청크에서 비-MACRO subject에 macro_variable='RATE'를 넣어 스키마 실패했던 청크다.)
- **chunk-08 capital-return**: 자사주 매입 $20.2B(108M주)와 $80B 추가 승인은 `CAPITAL_ALLOCATION`. direction은 prompt.py 정의("measured subject의 사실적 방향")에 따라 자본환원 규모 증가 = `INCREASE`. 배당 $0.01→$0.25 인상도 별도 fact. `SHARES_MN`, `USD_PER_SHARE` 단위 사용.
- **chunk-09 / chunk-10**: 정성 risk 사실 위주 → `numeric_facts`가 비거나(`[]`) 부분적. `magnitude_basis: "qualitative"`. H200 25% 관세는 수치 있음(`PCT`).
- direction은 항상 "측정 대상의 사실적 방향"(valuation 영향 아님)으로, `prompt.py` 정의와 일치시킬 것.

## src/dcf_engine/extraction/gold.py (모델 + 로더)

- `claim.py`의 `ClaimSubject`, `ClaimNature`, `ClaimDirection`, `MagnitudeQualifier`, `MacroVariable` 타입을 **재사용**(import).
- Pydantic v2, `model_config = ConfigDict(frozen=True)`, mypy strict 통과.
- 모델 구성:
  - `NumericFact(metric, value, unit, period)` — `unit`/`period`는 위 Literal.
  - `FactPeriod(current: str, prior: str | None)`.
  - `MagnitudeBand(min: float, max: float | None)` + `MagnitudeBands` (4밴드 + `basis`).
  - `GoldFact(...)` — 위 필드 전부. validator:
    - `allowed_subjects` 비어있지 않음 + 중복 없음.
    - `acceptable_natures` 비어있지 않음.
    - `MACRO_EXPOSURE ∈ allowed_subjects` ⇔ `macro_variable is not None` (양방향).
    - `magnitude_basis == "numeric"`이면 `numeric_facts`에 `period == "change_pct"`인 항목이 있어야 함.
  - `GoldFactSet(schema_version, label_status, source_filing, magnitude_bands, labeling_rule, facts_by_chunk: dict[str, list[GoldFact]])`.
    - `SourceFiling`은 `evaluator.py`에 이미 있으나 evaluator를 import하면 결합도가 생기니, **gold.py에 동일 필드의 SourceFiling을 자체 정의**하거나 공용 모듈로 분리(둘 중 택1, evaluator.py는 수정 금지이므로 자체 정의 권장).
- `load_gold_facts(path: Path) -> GoldFactSet` (json 파싱).
- 헬퍼 `band_for_pct(bands: MagnitudeBands, pct: float) -> MagnitudeQualifier` (절대값 기준 밴드 매핑) — #2 evaluator가 쓸 공용 함수이므로 여기 둔다.

## tests (TDD — 먼저 실패 테스트부터)

`tests/extraction/test_gold_facts.py`:

1. `load_gold_facts(Path("data/benchmark/gold_facts.json"))`가 성공하고 `schema_version == 2`.
2. `facts_by_chunk`의 키가 `data/benchmark/chunks/`의 10개 청크 stem과 **정확히 일치**.
3. 각 청크에 fact ≥ 1개.
4. **fact_id 유일성**(청크 내) + prefix가 청크 번호와 일치.
5. **grounding**: 각 fact의 `evidence_span`이 해당 청크 본문(헤더 `#` 줄 제외, 공백 정규화)의 부분문자열.
6. **numeric grounding**: 각 `numeric_fact.value`가 청크 본문에 등장(정수/소수 표기 매칭; 천단위 콤마·`$`·`billion/million` 허용하는 정규화 헬퍼로 비교).
7. **magnitude 일관성**: `magnitude_basis == "numeric"`인 fact는 `change_pct` 값을 `band_for_pct`에 넣은 결과 == `magnitude_qualifier`.
8. **direction 일관성**: current/prior 수치가 둘 다 있으면 `current > prior ⇒ INCREASE`, `<⇒ DECREASE`, `==⇒ NEUTRAL`.
9. **multi-label 존재 검증**: 최소한 chunk-01·chunk-04에 `allowed_subjects` 길이 ≥ 2인 fact가 존재(회귀 방지).
10. `band_for_pct` 경계값 단위 테스트(9.99→WEAK, 10→MODERATE, 30→STRONG, 70→EXTREME).
11. MACRO_EXPOSURE ⇔ macro_variable 양방향 validator 테스트(잘못된 조합이 ValidationError).

> 5·6번 grounding 테스트가 chunk-01 FY 오라벨 같은 실수와 환각 숫자를 컴파일 타임에 걸러주는 안전망이다.

## 완료 기준 (DoD)

- `make verify` 통과 (ruff 0 warning, mypy --strict 0 error, pytest 전부 green, coverage ≥ 80%).
- `git status` clean.
- 기존 `gold.json`/`evaluator.py`/`benchmark.py`/`claim.py` **무변경**.
- 최종 보고: **Plan / Files Changed / Commands Run / Test Results / Remaining Risks**.
- Remaining Risks에 "label_status=draft이므로 유저 freeze 전까지 fact 내용은 잠정"과 "#2(evaluator/benchmark를 gold_facts.json으로 전환 + grounding 기반 precision/coverage recall 구현)가 후속 필요"를 명시.

## 이번 작업에서 하지 말 것 (#2로 미룸)

- evaluator의 매칭/지표 로직 변경.
- benchmark.py가 gold_facts.json을 쓰도록 전환.
- 기존 gold.json 삭제/대체.
- 프롬프트(prompt.py)·Claim 스키마 변경.
