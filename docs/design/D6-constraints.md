# D6 constraints (`backend/agent/constraints.py`)

기준: D0-contract(타입·ReasonCode·집계 규칙 그대로), DESIGN §3·§5. 순수 함수만(LLM·네트워크·I/O·로깅·전역 상태 없음). 호출은 D8(judge/lookup), 문장화는 D9.

## 1. 공개 함수

```python
SAMPLE_MIN = 3                      # 산업평균 표본 부족 기준(n<3)

def compute_metric_row(raw: dict, *, fiscal_period: str | None,
                       actual_basis: str | None, estimated: bool) -> MetricRow
def industry_avg_per(rows: Mapping[str, MetricRow]) -> dict      # {"value": float|None, "n": int}
def evaluate(row: MetricRow, constraints: list[Constraint],
             industry_avg: dict[str, dict]) -> Verdict | None
def failure_verdict(reason: Literal["NO_FINANCIALS","FETCH_FAILED"]) -> Verdict
def is_sample_short(n: int) -> bool                              # n < SAMPLE_MIN (D9가 "표본 부족" 표기에 사용)
```

- `raw` 키(전부 `float|int|None`): `revenue, op_profit, net_income, assets, equity, debt_ratio, mkt_cap`. get_financials `data`의 원천값 그대로. **시가총액 출처 = get_financials `data.mkt_cap`**; 이것이 None이면 D8이 후보 행(`find_stock_candidates.mkt_cap`)으로 채운 뒤 넘긴다(채움은 D8 책임, D6는 `raw["mkt_cap"]`만 본다).
- 단위: 금액 원천값(revenue~equity, mkt_cap)은 **서로 같은 통화 단위**여야 한다(D4가 보장, 가정). D6는 단위 변환 안 함. 비율은 분자·분모 단위가 상쇄되어 무관.
- `compute_metric_row`: `values`에 원천 5개(revenue/op_profit/net_income/assets/equity)와 mkt_cap, Metric 7종을 채움(원천 `debt_ratio`는 `DEBT_RATIO` 키로만 보존). `fiscal_period/actual_basis/estimated`는 **가공 없이 그대로 복사**(get_financials `data.fiscal_period`, `meta.actual_basis`, `meta.estimated`를 D8이 전달). get_financials 실패 종목은 MetricRow를 만들지 않고 `failure_verdict`만 쓴다(D8).
- `industry_avg_per`: 입력은 후보 집합 중 재무 조회에 성공한 종목들의 MetricRow. 결과를 D8이 `state["industry_avg"]["PER"]`에 저장.
- `evaluate`: `industry_avg`는 D0의 `{"PER": {"value","n"}}` 구조. 제약이 0개면 `None`(판정하지 않음, D0). 예외를 던지지 않는다.
- 입력의 NaN/inf/bool은 None과 동일 취급하며 §2 사유 규칙을 그대로 따른다(mkt_cap NaN → PER/PBR/PSR이 `NO_MARKET_CAP`, 그 외 원천 NaN → `NO_FINANCIALS`).

## 2. 지표 식·단위·판단불가 매핑

곱셈을 먼저 한다(`a*100/b`, PER류는 `a/b`): 정수 입력에서 반올림 오차를 한 번으로 제한(경계값 안정). 결과는 반올림하지 않는다.

| Metric | 식 | 단위 | 판단불가(값=None, `reasons[metric]`) |
|---|---|---|---|
| PER | mkt_cap / net_income | 배 | 아래 공통 우선순위 + net_income ≤ 0 → `NEGATIVE_EARNINGS` |
| PBR | mkt_cap / equity | 배 | equity ≤ 0 → `NEGATIVE_EQUITY` |
| PSR | mkt_cap / revenue | 배 | revenue ≤ 0 → `NONPOSITIVE_REVENUE` |
| ROE | net_income*100 / equity | % | equity ≤ 0 → `NEGATIVE_EQUITY` |
| ROA | net_income*100 / assets | % | assets ≤ 0 → `NONPOSITIVE_ASSETS` |
| OPM | op_profit*100 / revenue | % | revenue ≤ 0 → `NONPOSITIVE_REVENUE` |
| DEBT_RATIO | 원천 `debt_ratio` 그대로(이미 %) | % | equity ≤ 0 → `NEGATIVE_EQUITY` |

**공통 우선순위(지표별로 첫 해당만 기록)**: ① 해당 식에 필요한 재무 원천값이 None → `NO_FINANCIALS` ② PER/PBR/PSR에서 mkt_cap이 None 또는 ≤0 → `NO_MARKET_CAP` ③ 부호 조건(표의 코드).
- DEBT_RATIO: 부채총계가 get_financials 출력에 없으므로 **API 제공값만 사용**(DESIGN §5 "API가 주면 그 값"). equity가 None이면 부호 검사 생략, `debt_ratio`가 None이면 `NO_FINANCIALS`. **equity ≤ 0이면 API값이 있어도 None(`NEGATIVE_EQUITY`)이 되는 것이 의도**이며, 이는 DESIGN §5에 없는 D6 추가 규칙이다(요청 4).
- ROE/ROA는 순이익이 음수·0이어도 **계산한다**(적자 ROE는 음수 그대로). 판단불가는 분모 조건뿐. PER만 순이익 ≤ 0이 판단불가.
- OPM은 영업적자면 음수 그대로(판단불가 아님). 기간 연환산 없음(연간 데이터, §4).
- 제약 `value`의 단위는 지표 단위와 동일(ROE 10 = 10%, PER 15 = 15배; D7이 % 기호 제거 후 숫자로 저장한다고 **가정**).

## 3. 산업 평균 PER

- 표본 = 입력 rows 중 `values["PER"]`가 None이 아닌 종목(PER 산출 불가 종목 = 순이익≤0·시총 없음·재무 없음은 제외). 값은 `statistics.median`(원값 정밀도). `n` = 표본 수.
- `n==0` → `{"value": None, "n": 0}`. `n≥1` → `{"value": 중앙값, "n": n}`. D0 구조 외 필드를 추가하지 않는다. "표본 부족" 표기는 D9가 `is_sample_short(n)`(n<3)로 파생하며 **n 1~2도 value는 비교에 사용**한다(D0 규칙: 사유 코드는 n=0만). 판단 대상 종목 자신도 표본에 포함(후보 집합 기준).
- 후보 집합은 재계획으로 확장되면 D8이 누적 rows 전체로 **재계산**한다(D6는 상태 없음).

## 4. evaluate 규칙

1. 제약별 비교 대상값 `t` = `value`, 또는 `ref=="industry_avg"`면 `industry_avg[metric]["value"]`(PER 외 metric이어도 동일 조회, 없으면 None).
2. 제약 하나의 결과: 지표값 `v=row.values.get(metric)`. v가 None → 불확정, 사유 `row.reasons.get(metric,"NO_FINANCIALS")`. ref인데 `t`가 None → 불확정, 사유 `INDUSTRY_AVG_UNAVAILABLE`. **불량 제약**(value·ref 둘 다 None 또는 둘 다 값, value가 NaN/inf/bool, ref가 `"industry_avg"` 아님, op가 `>=`/`<=` 아님)도 TypeError 없이 불확정 처리하며 사유는 `NO_FINANCIALS`(맞는 코드가 없어 쓰는 가정, 요청 5). 불확정 사유는 이 순서(지표값 → 임계값)로 모두 기록하고 비교는 건너뛴다. 그 외 비교: `>=`면 `v >= t`, `<=`면 `v <= t`. **같으면 충족**, 허용오차·반올림 없음(원값 비교). 충족/불충족.
3. 집계(D0): 불충족이 하나라도 있으면 `FAIL`, `reasons=["THRESHOLD_NOT_MET"]+불확정 사유 합집합`, `failed=불충족 metric`; FAIL 없이 불확정이 있으면 `UNDECIDABLE`, `reasons=합집합`, `failed=[]`; 전부 충족이면 `PASS`, `reasons=[]`, `failed=[]`. 합집합·`failed`는 제약 순서 기준 첫 등장 순으로 중복 제거.
4. `failure_verdict(r)` = `{status:UNDECIDABLE, reasons:[r], failed:[]}`. 매핑(D8): NO_FINANCIALS→`NO_FINANCIALS`, API_ERROR/RATE_LIMIT/TIMEOUT/INVALID_INPUT→`FETCH_FAILED`.

## 5. MetricRow.reasons 채우기

`compute_metric_row`가 §2 규칙으로 값이 None이 된 **Metric 7종**에만 `reasons[metric]=code` 기록. 값이 있으면 키 없음(기본 `{}`). 원천 키(revenue 등)가 None인 경우는 Metric이 아니므로 기록하지 않는다(해당 Metric 쪽에 `NO_FINANCIALS` 등으로 나타남). D9는 이 dict로 lookup 지표별 사유 안내.

## 6. 단위 테스트 대표 케이스

기본 raw R0: revenue 1000, op_profit 100, net_income 80, assets 2000, equity 800, debt_ratio 150, mkt_cap 1600 → PER 20, PBR 2, PSR 1.6, ROE 10, ROA 4, OPM 10, DEBT_RATIO 150, reasons `{}`.

| # | 입력 | 기대 |
|---|---|---|
| 1 | R0 | 위 값, reasons `{}`; fiscal_period/actual_basis/estimated 그대로 |
| 2 | R0 + [ROE>=10] | PASS(경계, 같으면 충족) |
| 3 | R0 + [ROE>=10.0001] | FAIL, `[THRESHOLD_NOT_MET]`, failed `[ROE]` |
| 4 | R0 + [PER<=20, PBR<=2] | PASS |
| 5 | net_income=0 | PER None(`NEGATIVE_EARNINGS`), ROE=0, ROA=0 |
| 6 | net_income=-50 | PER None(`NEGATIVE_EARNINGS`), ROE=-6.25, ROA=-2.5 |
| 7 | equity=0 (및 -100) | PBR·ROE·DEBT_RATIO None, 전부 `NEGATIVE_EQUITY` |
| 8 | revenue=0 | OPM·PSR None, `NONPOSITIVE_REVENUE` |
| 9 | assets=0 | ROA None, `NONPOSITIVE_ASSETS` |
| 10 | mkt_cap=None | PER·PBR·PSR None, `NO_MARKET_CAP`; ROE/ROA/OPM/DEBT_RATIO 정상 |
| 11 | net_income=None | PER·ROE·ROA None, `NO_FINANCIALS` |
| 12 | op_profit=-100 | OPM=-10(음수 그대로); [OPM>=5] FAIL; [OPM>=-10] PASS |
| 13 | industry_avg_per PER {A:10,B:20,C:30,D:None} | `{value:20, n:3}`, `is_sample_short(3)=False` |
| 14 | PER {A:10,B:20} / {A:10,B:20,C:30,D:40} | `{15, 2}`(표본 부족) / `{25, 4}` |
| 15 | 전원 PER None | `{value:None, n:0}` |
| 16 | PER=20, [PER<=ref], avg `{20,n:3}` | PASS(경계) |
| 17 | PER=20, [PER<=ref], avg `{15,n:2}` | FAIL(비교는 수행, 표기는 D9) |
| 18 | PER=20, [PER<=ref], avg `{None,0}` | UNDECIDABLE, `[INDUSTRY_AVG_UNAVAILABLE]`, failed `[]` |
| 19 | constraints=[] | `None` |
| 20 | R0에서 net_income=-50(ROE=-6.25, PER None), [ROE>=10, PER<=15] | FAIL, `[THRESHOLD_NOT_MET, NEGATIVE_EARNINGS]`, failed `[ROE]` |
| 21 | R0에서 net_income=-50, [PER<=ref], avg `{None,0}` | UNDECIDABLE, `[NEGATIVE_EARNINGS, INDUSTRY_AVG_UNAVAILABLE]` |
| 22 | R0에서 mkt_cap=None, [ROE>=10, PBR<=3] | ROE 충족, PBR 불확정 → UNDECIDABLE, `[NO_MARKET_CAP]`, failed `[]` |
| 23 | `failure_verdict("FETCH_FAILED")` | UNDECIDABLE, `[FETCH_FAILED]`, failed `[]` |
| 24 | net_income=NaN | PER·ROE·ROA None, `NO_FINANCIALS` |
| 25 | mkt_cap=NaN | PER·PBR·PSR None, `NO_MARKET_CAP` |
| 26 | R0 + [ROE>=None(value·ref 둘 다 None)] | UNDECIDABLE, `[NO_FINANCIALS]`, failed `[]`(예외 없음) |
| 27 | R0(ROE=10) + [ROE>=5, PBR<=1] (PBR=2) | FAIL, `[THRESHOLD_NOT_MET]`, failed `[PBR]` |

## 7. 위임·가정 정리

- 표시용 반올림·"표본 부족"/"후보 집합 기준"/기간 문구: D9(반올림 자릿수 D9). 단위 일치 보장: D4. mkt_cap 보충·ReasonCode 매핑 호출: D8.
- 가정: ① 재무는 연간 단일 기준이라 ROE/ROA 연환산 없음(DESIGN §3 실호출 결과) ② `estimated` 판정은 D4/ToolMeta가 이미 확정 ③ NaN/inf/bool을 None과 동일 취급하는 것과 불량 Constraint의 `NO_FINANCIALS` 대체는 D0 명시가 없는 D6 판단 ④ 기대값 정확 비교는 정수 입력 케이스에서만 보장(실수 입력은 `pytest.approx`).

## 상위 계약에 대한 요청

1. **ReasonCode에 "원천 재무값 None" 전용 코드가 없다.** 현재 `NO_FINANCIALS`(ErrorCode와 같은 이름)를 재사용한다. 의미 혼선이 우려되면 D0에 `MISSING_SOURCE_VALUE` 추가를 요청(미반영이면 현 매핑 유지).
2. **n=1~2 표본에서 PER<=산업평균은 자기 자신과의 비교가 되어 사실상 무의미**하다. D0 규칙대로 판정하지만, 원하면 n<3을 `INDUSTRY_AVG_UNAVAILABLE`로 취급하도록 D0 변경 필요(승인 사항).
3. get_financials 출력에 부채총계가 없어 DEBT_RATIO를 API값에 의존한다. D4가 `debt_ratio` None을 줄 수 있음을 계약에 명시해 달라(D4 확인 사항).
4. DEBT_RATIO는 equity ≤ 0이면 API값이 있어도 판단불가(`NEGATIVE_EQUITY`)로 처리한다. DESIGN §5에 없는 D6 추가 규칙이므로 승인 필요(PBR/ROE와의 일관성 목적).
5. D0 불변식을 어긴 Constraint(값/ref 불량)에 해당하는 ReasonCode가 없어 `NO_FINANCIALS`로 대체한다. 전용 코드(예: `INVALID_CONSTRAINT`)가 필요하면 D0 추가 요청.
