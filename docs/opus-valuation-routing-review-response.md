# Opus Review Response: Extraction-Driven Valuation Routing

> 리뷰 대상: `docs/opus-valuation-routing-review-prompt.md`
> 리뷰어: Opus (실제 엔진 코드 정독 후 작성 — routing/factor/loading/bridge/monte_carlo/distributions/nvda_spike)
> 결론 한 줄: **프롬프트의 진단은 대체로 옳지만, 헤드라인 숫자(default 51.8%, fair value $1.44T 붕괴)의 진짜 원인은 claim 과다·saturation이 아니라 `beta_from_moments`의 경계 수치 붕괴다. 수정 우선순위를 바꿔야 한다.**

---

## 0. TL;DR — 수정 우선순위

1. **`distributions.beta_from_moments` 경계 붕괴 수정** (+회귀 테스트). 이게 안 되면 아래가 무의미.
2. **DEFAULT_PROBABILITY / WACC를 narrative에서 분리** (balance-sheet base + hard-capped premium).
3. **COST routing의 매출/마진 네팅** (비용 절대 증가 ≠ 효율 저하).
4. **Claim → EconomicDriver dedup + assumption 공간(pp) cap**.

프롬프트의 해결책(필터·dedup·driver cap)은 4번에 해당하며 방향이 옳다. 다만 1·2를 먼저 하지 않으면, 정당하게 강한 factor만으로도 동일한 폭주가 재발한다.

---

## 1. 🔴 Critical: default 51.8%는 `beta_from_moments` 붕괴다

DeepSeek factors: `FinancialStrength=3.0, OperatingEfficiency=-2.80, MacroCondition=-0.82`.

추적:

1. `loading.py` `DEFAULT_PROBABILITY` loading에 `FinancialStrength: -0.9`. 강한 양(+) 재무 factor가 mu_shift를 크게 음수로:
   ```
   mu_shift ≈ -0.9·3.0 + -0.1·(-2.80) + -0.2·(-0.82) + -0.2·0 = -2.256
   ```
2. `monte_carlo._shifted_mu`: `mu = base_mu + mu_shift·shift_scale.center = 0.015 + (-2.256·0.05) = -0.098`
   → `loading.apply_constraints`가 `[1e-6, 1-1e-6]`로 클램프 → **mu = 1e-6** (의도: "좋은 회사 → 부도확률 ↓", 방향은 올바름).
3. `distributions.beta_from_moments(mu=1e-6, sigma=0.008)` (distributions.py:21-26):
   ```
   variance      = min(0.008², 1e-6·(1-1e-6)·0.99) = 9.9e-7
   concentration = 1e-6·(1-1e-6)/9.9e-7 - 1 ≈ 0.0101
   alpha = max(1e-6·0.0101, 0.1) = 0.1     # ← floor에 걸림
   beta  = max(~0.0101,     0.1) = 0.1     # ← floor에 걸림
   ```
   → **`beta(0.1, 0.1)`**, 평균 = `0.1/(0.1+0.1) = 0.5`, 0/1에 몰린 U자형 분포.

**즉, 부도확률을 0으로 낮추려는 시도가 `sigma(0.008)`가 그 작은 mu에서 실현 불가능할 만큼 커서 `max(·, 0.1)` floor 때문에 평균 0.5로 뒤집힌다.** floor가 없었다면 평균은 ~1e-6로 정상이었다.

검증 — baseline(claim 0개, mu=0.015)은 정상:
```
variance = min(6.4e-5, 0.01463) = 6.4e-5
concentration = 0.014775/6.4e-5 - 1 ≈ 229.9
alpha = 0.015·229.9 = 3.45,  beta = 0.985·229.9 = 226.4
mean = 3.45/229.9 = 0.015  ✓
```
버그는 **mu가 경계(1e-6 또는 1-1e-6)로 밀릴 때만** 발동한다.

### 가치 붕괴 연결
equity bridge(`bridge.py`)는 `distress_adjusted = (1-p)·GC + p·(0.25·GC)`.
p≈0.5 → `0.5·GC + 0.5·0.25·GC = 0.625·GC` → 약 37.5% 헤어컷. 여기에 OPERATING_MARGIN 압축 + WACC 상승이 겹쳐 $3.6T → $1.44T.

### 왜 프롬프트의 진단이 부분적으로 빗나갔나
"narrative claim이 default prob을 움직인다"는 관찰은 맞다. 그러나 **loading 공식상 narrative가 만들 수 있는 default mu 상한은 ~0.255**(|loading|합 ~1.6 × factor cap 3 × scale 0.05 + base)이다. 51.8%는 공식으로는 **도달 불가능한 값** → 분포 변환 버그 없이는 설명되지 않는다. 따라서 routing cap만으로는 못 막는다.

### 수정안
- inversion 전에 `sigma`를 해당 mu에서 실현 가능한 범위로 클램프: `sigma_eff = min(sigma, sqrt(mu·(1-mu))·k)` (k<1).
- 또는 alpha/beta를 0.1로 floor하지 말고 **concentration에 하한**을 두고 alpha/beta는 비율을 보존.
- 회귀 테스트(§7)로 박을 것.

---

## 2. 🟠 프롬프트가 맞게 본 것 (코드 확인)

- **#4 OperatingEfficiency = -2.80**: 사실. R&D / SG&A / total opex / compute infra / eng materials / compensation 등 다수 `COST_SIGNAL/INCREASE`가 `ROUTING["COST_SIGNAL"]={"OperatingEfficiency":-0.7,"MacroCondition":-0.2}`로 누적. `routing.py:64`의 `saturation = 1/(1+count·0.3)` 감쇄가 있어도 -3 cap 근처까지 쌓임.
  - **더 심각한 의미 오류**: 매출 +85%, gross margin 60.5%→74.9% **확대** 분기인데 "비용 증가"를 곧 "효율 저하"로 라우팅. 비용은 매출/마진과 **네팅**되어야 함. 같은 filing의 마진 확대 claim과 정면 모순.
- **#3 factor saturation**: 사실. `FINANCIAL_HEALTH` claim 11개 → `FinancialStrength`가 +3.0 **cap 도달**(`routing.py:70`). cap 도달 자체가 경고 신호.
- **#2 granularity 중복**: 사실. 추가로 **factor 간 이중계상**: 매출증가(→DemandStrength)와 영업이익증가(→FinancialStrength)는 같은 경제 사실인데 서로 다른 factor를 동시에 밀어 REVENUE_CAGR·OPERATING_MARGIN을 중복 부양.
- **#6 count 기반 누적의 취약성**: 사실. `route_claims_to_factors`의 감쇄는 **claim 리스트 순서 의존**(먼저 온 claim이 full weight)이라 임의적.

---

## 3. 질문별 답변

### Q1. `Claim → EconomicDriver → FactorState → AssumptionState` vs `Claim → AssumptionDelta` 직접?
중간 계층 유지가 맞다. 직접 매핑은 dedup·cap·relevance를 걸 자리가 없고 추출 taxonomy와 valuation input을 강결합한다. Factor 계층은 이미 lifecycle 민감도·불확실성 전파(`factor.factor_uncertainty`)를 인코딩하므로 유지. **EconomicDriver 계층이 dedup/cap/필터가 사는 곳.**

### Q2. EconomicDriver taxonomy 설계
8~12개로 작게. 프롬프트 목록이 좋다. 각 driver에 `{허용 direction, 대상 assumption 집합, pp 단위 영향 cap}`을 묶어라.
예: `revenue_acceleration`, `dc_demand_blackwell`, `margin_recovery`, `opex_pressure`, `china_export_risk`, `tariff_pressure`, `capital_return`, `customer_concentration`. (claim N → driver 1, many-to-one)

### Q3. 어떤 claim이 valuation input이 되나 (필터)
1. **grounded만 통과** (#1/#2 scorecard 재사용).
2. `valuation-relevant / context-only / discard` 3분류.
3. **non-recurring 플래그**(investment gain, interest income) → operating assumption 진입 금지.
4. customer concentration·dividend → context-only 또는 capital-structure만.
   → claim_nature + subject + driver 매핑으로 **결정론적** 분류.

### Q4. 중복/겹침 claim 병합
`(driver, direction)`로 그룹핑 후 그룹 내 **합산 금지 — max magnitude(또는 evidence-weighted) 채택**. 같은 지표의 절대값+증가율은 한 driver로 collapse(#1 gold 규칙과 동일 철학). cross-chunk 동일 driver도 병합.

### Q5. factor→assumption cap/calibration
영향 cap을 **factor 공간(±3)이 아니라 assumption 공간(pp)** 에 둘 것. 프롬프트 표(REVENUE_CAGR +0~4pp, OPERATING_MARGIN ±, …)가 정확히 옳다. 현재는 factor에서 cap 후 loading이 다시 곱해 assumption mu가 경계로 밀리고, 그게 §1 버그를 유발한다.

### Q6. DEFAULT_PROBABILITY / WACC 왜곡 방지
1. **분포 버그 수정**(§1) — 1순위. 안 하면 나머지 무의미.
2. **DEFAULT_PROBABILITY를 narrative에서 분리**: base는 balance sheet/credit ratio, narrative는 hard-cap된 작은 risk premium만. `loading.py`의 `DEFAULT_PROBABILITY: {FinancialStrength:-0.9, ...}`를 대폭 축소하거나 제거.
3. **WACC**도 narrative는 좁은 band 내 nudge만.

### Q7. 신뢰 전 필요한 테스트
프롬프트의 guardrail 5개 + 추가:
- **분포 feasibility (§1 회귀)**: 임의 mu에서 sigma가 실현 가능 범위로 클램프되고, beta 평균이 입력 mu의 ±tol 이내 — 경계 mu에서 평균 0.5로 안 튐.
- **단조성**: 양(+) 영업 claim이 늘수록 default prob이 **절대 증가하지 않음**.
- **dedup 멱등성**: 같은 claim ×5 ≈ ×1.
- **golden company sanity**: NVDA 호실적 분기 → fair value가 baseline($3.6T) 아래로 안 떨어짐.
- **non-recurring 불변**: investment gain claim이 operating margin/CAGR을 안 움직임.
- **default cap**: 어떤 narrative 입력에서도 default mean이 balance-sheet 트리거 없이 일정 상한(예: 5~10%) 초과 금지.

### Q8. 제안의 개념적 오류
1. **가장 큰 것 — 분포 계층 버그를 놓침**(§1). narrative cap만으로는 default 폭주를 못 막는다(정당하게 강한 factor도 경계로 밀려 동일 버그).
2. cap을 암묵적으로 factor 공간에 두려 함 → assumption 공간(pp)에 둬야 함(Q5).
3. **mean-reversion은 이미 구현됨**(`loading.apply_mean_reversion`: REVENUE_CAGR/OPERATING_MARGIN/ROIC/S2C). 재발명 말고 활용.
4. `opex_pressure`를 별도 driver로 두는 건 맞지만 **매출/마진과 네팅하는 규칙**을 명시해야 함. 안 그러면 driver 단위로 줄여도 부호가 여전히 틀림(§2).
5. capital return을 operating이 아니라 equity bridge로 보내는 직관은 **정확**.
6. factor 샘플링 sigma(`_sample_factors`, growth=0.5)가 factor cap(±3) 대비 커서, **claim 0개에서도** 샘플 노이즈만으로 assumption이 크게 흔들림 — calibration 검토 필요.

---

## 4. 권장 실행 순서 (TDD)

1. `test_distributions.py`: 경계 mu에서 beta 평균이 mu에 수렴함을 단언하는 **실패 테스트** → `beta_from_moments` 수정.
2. `test_default_probability_guardrail.py`: 강한 양(+) 영업 claim에서 default mean이 baseline 근처 유지(단조성·상한).
3. EconomicDriver 계층 도입(`claim → driver` dedup, assumption-space cap).
4. COST routing 네팅 규칙 + driver별 guardrail 테스트(프롬프트 5종).
5. golden company 회귀: NVDA 호실적 → fair value ≥ baseline.

각 단계는 `make verify`(ruff + mypy --strict + pytest, coverage ≥ 80%) 통과 + refactor/feat 커밋 분리.

---

## 5. Remaining Risks / 미해결
- 이 리뷰의 §1 산술은 DeepSeek factor JSON과 코드 기준 재구성이다. 정확한 51.8%는 `beta_from_moments` 회귀 테스트로 **재현 후 확정**할 것.
- "DeepSeek claim 전체 사용" 하니스(프롬프트 57~81행)는 리포지토리에서 직접 확인하지 못했다. 위 분석은 `nvda_spike`/`monte_carlo`/`loading` 경로 기준이며, 별도 하니스가 다른 경로를 쓰면 차이가 있을 수 있다.
- DEFAULT_PROBABILITY base를 balance-sheet에서 가져오려면 credit metric 입력이 필요(현재 `company` dict에 없음).
