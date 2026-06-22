# Opus Review Prompt: Extraction-Driven Valuation Routing

우리는 공시 chunk에서 LLM이 claim을 추출하고, 그 claim을 DCF Monte Carlo valuation assumption에 반영하는 엔진을 만들고 있습니다.

현재 파이프라인은 대략 이렇습니다.

1. 이미 분할된 공시 chunk 10개를 입력으로 제공
2. DeepSeek 또는 Claude Haiku가 각 chunk에서 structured claim 추출
3. claim을 gold_facts와 scorecard로 평가
4. claim을 deterministic routing으로 factor state로 변환
5. factor state가 Monte Carlo assumption mean에 shift를 줌
6. DCF-style fair value distribution 산출

현재 extraction benchmark 결과:

DeepSeek:
- total claims: 46
- schema_validation_rate: 1.0
- grounded_precision: 0.739
- coverage_recall: 0.633
- primary_coverage_recall: 0.692
- numeric_grounding_rate: 1.0
- cost_per_chunk_usd: 0.000408
- true_positives: 31
- false_negatives: 18

Haiku:
- schema_validation_rate: 0.8
- grounded_precision: 0.676
- coverage_recall: 0.408
- primary_coverage_recall: 0.5
- cost_per_chunk_usd: 0.00693
- schema failures on chunk-07 and chunk-09

현재 DeepSeek claim 분포:
- subject: COST_SIGNAL 12, FINANCIAL_HEALTH 11, DEMAND_SIGNAL 10, CAPITAL_ALLOCATION 5, others small
- direction: INCREASE 34, DECREASE 8, NEUTRAL 4
- magnitude: STRONG 18, MODERATE 16, EXTREME 7, WEAK 5
- nature: REALIZED 38, STRUCTURAL 3, RISK_FLAG 3, GUIDANCE 2

DeepSeek가 뽑은 대표 claim:
- Compute & Networking revenue increased 88% YoY to $74.55B
- Graphics revenue increased 58% YoY to $7.065B
- Total revenue increased 85% YoY to $81.615B
- Compute & Networking operating income increased 142% YoY
- Gross margin increased to 74.9% from 60.5%
- R&D expense increased 58%
- SG&A increased 25%
- Total operating expenses increased 52%
- Repurchased 108M shares for $20.2B
- Board approved additional $80B share repurchase authorization
- H200 China license allowed small shipments, but no revenue yet
- H200 shipments subject to 25% tariff
- NVIDIA was effectively foreclosed from China data center computing market
- Export controls may materially adversely impact business

현재 routing/Monte Carlo smoke test 결과:
- baseline, no claims:
  - median fair value: $3.60T
  - P10/P90: $2.55T / $5.30T
  - DEFAULT_PROBABILITY mean: 1.5%
- existing hardcoded spike claims:
  - median fair value: $3.99T
  - P10/P90: $2.48T / $7.35T
  - DEFAULT_PROBABILITY mean: 1.8%
- DeepSeek extracted claims 전체 사용:
  - median fair value: $1.44T
  - P10/P90: $0.54T / $3.49T
  - DEFAULT_PROBABILITY mean: 51.8%

DeepSeek 전체 claim으로 생성된 factor:

```json
{
  "DemandStrength": 2.52,
  "CompetitiveAdvantage": -0.25,
  "FinancialStrength": 3.0,
  "OperatingEfficiency": -2.80,
  "MacroCondition": -0.82
}
```

문제라고 보는 점:

1. extraction 자체는 어느 정도 쓸 만하지만, extracted claim 전체를 valuation input으로 바로 넣는 게 위험합니다.
2. claim granularity가 너무 잘게 쪼개져 있어 같은 경제적 사실이 여러 번 factor에 누적됩니다.
3. FINANCIAL_HEALTH / INCREASE 같은 claim이 많이 쌓이면서 factor가 saturation에 걸리고, DEFAULT_PROBABILITY 같은 assumption이 비정상적으로 움직입니다.
4. COST_SIGNAL / INCREASE claim도 R&D, SG&A, total opex 등 같은 비용 압력을 여러 번 세서 OperatingEfficiency를 과하게 낮춥니다.
5. 일회성 investment gain, interest income, dividend increase, customer concentration 같은 claim이 DCF assumption shift에 직접 들어가면 안 될 수 있습니다.
6. 현재 routing은 claim 개수와 magnitude 기반으로 factor를 누적하고, factor가 assumption mean을 shift합니다. 이 구조는 LLM이 claim을 더 많이 쪼갤수록 valuation이 흔들립니다.
7. DEFAULT_PROBABILITY는 narrative claim으로 크게 움직이면 안 됩니다. balance sheet/credit ratio 기반 모델이 아니면 거의 고정 또는 약한 cap이 필요해 보입니다.

내가 생각하는 해결 방향:

1. claim -> factor 전에 valuation-relevance filter를 둡니다.
   - grounded claim만 통과
   - valuation-relevant / context-only / discard 분류
   - routing 대상 claim만 factor로 보냄

2. claim 단위가 아니라 economic driver 단위로 dedupe/grouping합니다.
   예:
   - Q1 FY2027 revenue acceleration
   - Data center demand / Blackwell ramp
   - gross margin recovery
   - opex pressure
   - China export control risk
   - tariff pressure
   - capital return / buyback
   - customer concentration risk

3. routing은 claim-count accumulation이 아니라 driver-level capped impact로 바꿉니다.
   예:
   - Revenue growth driver: REVENUE_CAGR +0~4pp
   - Gross margin recovery: OPERATING_MARGIN +0~3pp
   - Opex pressure: OPERATING_MARGIN -0~2pp
   - China export risk: REVENUE_CAGR -0~3pp, MARKET_SHARE -0~2pp
   - Tariff pressure: OPERATING_MARGIN -0~1.5pp
   - Capital return: share count / equity bridge side, not operating assumption
   - Financial health: DEFAULT_PROBABILITY direct impact mostly disabled or capped extremely low

4. DEFAULT_PROBABILITY는 narrative claim에서 거의 분리합니다.
   - base default probability from balance sheet / credit metrics
   - narrative only adds tiny risk premium with hard cap
   - investment gains or operating income increases should not drive default probability materially

5. Add guardrail tests:
   - With NVDA-like positive operating claims, default probability must not jump from 1.5% to >5%
   - Repeating the same claim 5 times should not materially change factor impact after dedupe
   - R&D + SG&A + total opex in same chunk should compress to one opex pressure driver
   - China export risk should affect revenue/market share more than default probability
   - Buyback authorization should not increase DemandStrength directly

Questions for you:

1. Is the proposed intermediate layer best modeled as `Claim -> EconomicDriver -> FactorState -> AssumptionState`, or should it be `Claim -> AssumptionDelta` directly?
2. How would you design the economic driver taxonomy for DCF use without making it too complex?
3. What filtering rules would you apply to decide which extracted claims become valuation inputs?
4. How should duplicate/overlapping claims be merged?
5. What caps or calibration rules would you set for factor-to-assumption impact?
6. How would you prevent DEFAULT_PROBABILITY and WACC from being distorted by narrative extraction?
7. What tests would you require before trusting extraction-driven Monte Carlo results?
8. Are there any conceptual mistakes in my proposed solution?
