# D7a 결과 개수 요청(top_n) 반영 (`analyze.py`, `judge.py`, `respond.py`, `constraints.py`에 소규모 추가)

기준: D0 §4·§6-1·§6-5, D3(`candidate_limit`), D6, D7 §3·§4, D8 §5(공시), D9 §5-4·§5-7·§5-8, D10, 구현 코드(`state.py`, `nodes/analyze·judge·respond·execute·plan.py`, `memory.py`). 의사코드이며 구현 아님. **D0·D1·DESIGN·기존 설계 문서는 수정하지 않고 §2 요청만 올린다(사용자 승인 필요).**

## 1. 문제와 결론

- 실서버 시험: "반도체 중 PER 낮은 종목 3개" → 개수 무시, 후보 20 중 PASS 7개 전부 출력, 공시 요청 시 PASS 7개 전부에 공시 호출·출력.
- 원인: 개수 표현을 담을 필드가 없고(analyze JSON·Conditions), respond는 PASS 전부·후보(시총 내림차순) 순서로 출력, judge는 PASS 전부에 공시 호출.
- 결론(최소안): **`Conditions`에 선택 필드 `top_n` 1개 추가**(D0 변경 요청 1건). analyze가 추출·검증하고, judge(공시 호출 대상)와 respond(표·공시·초안)가 같은 순위 함수 `rank_passed`로 상위 N개만 다룬다. 후보 조회 limit·판정·재계획 규칙은 **불변**. `top_n` 없으면 모든 동작이 현재와 동일.

## 2. 표현 위치 (D0 변경 불가피 여부)

| 대안 | 평가 |
|---|---|
| 기존 필드 재사용 (`SearchScope.candidate_limit`, `Constraint`, `pending_metrics`) | 불가. `candidate_limit`은 plan이 재계산하는 **조회** 상한이고 재계획 ③이 덮어씀. 개수는 출력 상한이라 의미가 다르며, 같은 값으로 두면 "3개"가 후보를 3개로 줄여 판정이 왜곡됨(D3는 시총 상위 N만 남김) |
| analyze가 반환하는 별도 키(AgentState 밖) | 불가. LangGraph는 `AgentState`에 없는 키를 버린다 |
| 새 `AgentState.top_n` 필드 | D0 변경이며 후속 턴 유지를 위해 D10 저장 대상 목록도 늘어남(`memory.py` 수정 필요) |
| **`Conditions.top_n` (채택)** | D0 변경 1건. "사용자가 말한 값만" 담는 Conditions의 정의에 부합. D10은 `conditions`를 통째로 deepcopy 저장하므로 **메모리 코드 변경 0**, 병합 규칙(§6-1)도 기존 경로 재사용 |

### 상위 계약 요청 (제안만, 사용자 승인 후 D0에 반영)

D0 §4 `Conditions` 블록에 다음 한 줄을 추가한다.

```python
class Conditions(TypedDict):
    ...                          # 기존 6개 필드 그대로
    top_n: NotRequired[int | None]   # 출력 상한(PASS 종목 중 정렬 후 상위 N개). 키 없음/None=제한 없음(현재 동작). 사용자가 개수를 말했을 때만 키를 둔다. 후보 조회 limit과 무관
```

D0 §6-1 표 아래에 다음 문장을 추가한다.
"`top_n`은 industry/market/base_date와 같은 줄로 취급한다: 새 screen은 초기화(키 없음) 후 이번 메시지 값만, 후속 병합·refine은 이전 값 유지 + 새로 추출된 값만 덮어쓰기, lookup/report_save/chitchat은 그대로(불변)."

- `NotRequired`로 두어 `default_conditions()`·기존 테스트·기존 메모리 값이 수정 없이 유효(키 부재 = None). 읽는 쪽은 `cond.get("top_n")`.
- 승인되지 않으면 이 기능은 구현하지 않는다(대안 없음).

## 3. 의미 정의

| 항목 | 정의 |
|---|---|
| 개수 = 출력 상한 | **판정 완료 후 PASS 종목을 정렬하여 상위 N개만 표·공시·초안에 사용**. PASS 판정·`n_pass`·재계획 규칙 1~6은 N과 무관하게 전체 후보 기준(불변) |
| 후보 조회 limit과의 관계 | **무관**. `top_n`은 `candidate_limit`/plan의 `search_scope`를 바꾸지 않는다. 후보를 N개로 줄이면 "N개 중 PASS"가 되어 오답이므로 항상 기본(20) 후보를 전부 판정한 뒤 자른다. `limit_up`(재계획 ③)도 그대로 |
| 정렬 기준 | `top_n`이 있을 때만 적용. **기준 지표 = `conditions.constraints`의 첫 제약의 metric**. 방향은 그 제약의 `op`: `>=`는 값 내림차순(높을수록 좋음), `<=`는 오름차순(낮을수록 좋음). 값 `None`은 맨 뒤, 동률은 후보 순서(시총 내림차순, D3) 유지(안정 정렬). `ref="industry_avg"` 제약도 지표값 기준이라 동일. D9의 기존 규칙("후보 순서=시총 내림차순")은 `top_n`이 없을 때 그대로 유지 |
| N보다 PASS가 적을 때 | 전부 출력하고 한 줄 안내: "요청하신 {N}개보다 적은 {p}개만 조건을 충족했습니다." 재계획은 하지 않는다(규칙 5: `n_pass≥1`이면 answered. 개수 부족으로 재계획하려면 D0 규칙 변경 필요 → 범위 밖) |
| N 초과분이 있을 때 | 표 아래 한 줄: "조건을 충족한 종목이 {p-N}개 더 있습니다. ({지표 한글명} {높은/낮은} 순 상위 {N}개만 표시)" |
| `top_n == p` | 안내 문구 없음 |
| 공시 | **출력되는 상위 N개 종목에만** 호출·표기. judge가 `rank_passed(...)[:N]`에만 `search_disclosures` 호출(호출 수도 줄어듦: D8 §9의 "PASS 상위 N건 제한" 상의 항목을 이것으로 해소) |
| 제외 종목 줄 | 불변(FAIL/UNDECIDABLE만). N 초과 PASS는 제외 종목이 아니므로 위 "더 있습니다" 한 줄로만 알린다 |
| `report_draft` | 화면에 출력된 상위 N개만 담는다(저장 가능 범위 = 사용자가 본 종목; D9 §5-8의 "충족 종목 전부"를 `top_n` 있을 때만 "출력 종목"으로 축소). `top_n` 없으면 PASS 전부(현재 동일) |
| `last_result_summary` | `top_codes = 출력 종목[:TOP_N]`(순위 순), `pass_count = p`(전체 PASS 수). 형식 불변 |
| limit/error/asked | `top_n` 영향 없음(표 없음) |
| 기본 출력 개수 제한 | **새로 두지 않는다**(회귀 방지). 선택 제안은 §8 |

## 4. 메모리·후속 턴 (D0 §6-1 규칙 그대로)

| 상황 | 동작 |
|---|---|
| 새 screen | `base=default_conditions()`(키 없음) → 이번 메시지에 개수가 있을 때만 `top_n` 설정. 이전 턴 개수 승계 없음 |
| 후속 병합(직전 asked) | 이전 `top_n` 유지(예: "PER 낮은 종목 3개" → 업종 되묻기 → "반도체"에서 3 유지), 새로 추출되면 덮어씀 |
| refine | 이전 `top_n` 유지, 새로 추출되면 덮어씀. "5개로 바꿔줘" = `intent=refine`, `top_n=5`, 나머지 이전 조건 그대로(전체 파이프라인 재실행; 이전 결과 재사용 없음 — 단순화) |
| 개수만 있는 입력 | `extracted`에 `top_n`을 포함한다. ① 직전 asked + `prev_intent∈{screen,refine}`: LLM이 chitchat이면 기존 규칙대로 `prev_intent`로 승격(조건 병합). ② 그 밖에 LLM이 chitchat으로 분류했고 `top_n`만 추출됐으며(industry/constraints/pending/bare_number 없음) `prev_intent∈{screen,refine}`이면 `intent="refine"`으로 보정(신규 코드 규칙 1개). ③ `prev_intent=None`이면 조건 없음 → 기존 규칙대로 asked(`missing=[industry,threshold]`), 단 `top_n`은 병합된 conditions에 저장되어 이후 답변에서 유지됨 |
| lookup/report_save/chitchat | conditions 불변 → `top_n` 불변 |
| 해제 | 지원 안 함("전부 보여줘"는 범위 밖; 새 screen으로 초기화됨). `Conditions`가 덮어쓰기만 허용하는 기존 규칙과 같다 |
| 메모리 저장 | D10 `update_memory`는 `conditions` deepcopy라 코드 변경 없음 |
| 상한 | 코드 상수 `TOP_N_MAX = 20`(= respond `MAX_ROWS`, 임의 상수 → §9). 초과 입력은 20으로 클램프(표 행 상한과 일치해 "외 N개" 처리 겹침 없음) |

## 5. analyze 변경점 (D7 §3·§4)

### 5-1. JSON 스키마·프롬프트

`AnalyzeOut`에 `top_n: Any = None`(느슨한 타입 — 잘못된 값이 invalid 재호출을 유발하지 않게) 추가. 프롬프트 출력 키에 한 줄 추가:

> - top_n: 사용자가 요청한 결과 개수를 정수로("3개", "세 개", "상위 5곳", "TOP 3", "하나만"=1), 없으면 null. **개수는 bare_number나 constraints의 value가 아니다.** 예: "PER 15 이하 3개" → constraints에 PER 15, top_n=3. "5개로 바꿔줘"는 intent=refine, top_n=5. 숫자만 온 "10%"는 top_n이 아니다.

(`bare_number` 설명에 "개/곳/종목 단위의 숫자는 제외" 한 구절 보강.)

### 5-2. 검증 코드(LLM 불신)

`_top_n(v) -> int | None`:
- `None`, `bool`, 비수치, NaN/inf, 정수가 아닌 수(`2.5`), `< 1` → **무시(None)**. invalid 처리(재호출·error)도, asked도 하지 않는다(부가 정보이므로 실패해도 기존 동작으로 degrade).
- 정수값 float(`3.0`)은 int로. `> TOP_N_MAX`면 `TOP_N_MAX`로 클램프.
- 충돌 가드: `top_n`과 `bare_number`가 같은 값이면 `bare_number=None`(LLM이 같은 숫자를 양쪽에 넣어 "3개"가 임계값으로 오인되는 것 방지). 값이 다르면 둘 다 유지("PER 15 이하 3개" 같은 같은 메시지의 숫자 둘은 constraints/top_n으로 오기 때문에 정상 경로에서 bare_number와 충돌하지 않음).

### 5-3. 병합·이벤트

- `extracted = bool(o.industry or new_cons or pending or o.bare_number is not None or top_n is not None)`.
- screen/refine 병합: `top_n is not None`이면 `cond["top_n"] = top_n`(그 외 키를 건드리지 않음; base에 있으면 유지). **`top_n`이 None이면 키를 만들지 않는다**(기존 conditions 동등성·테스트 유지).
- §4의 ②번 보정(chitchat+top_n만 → refine) 추가.
- 이벤트 `llm_decision(kind="conditions")` 요약 문자열 끝에 `top_n=3`을 값이 있을 때만 덧붙임(없으면 현재 문자열과 동일).
- 되묻기: `top_n`은 필수 조건이 아니므로 `missing`에 영향 없음. 질문 템플릿 불변.

### 5-4. 기존 시나리오와의 정합

- 대표 요청 1~5·D0 §6-6 되묻기 체인: `top_n` 키 없음 → 동작·conditions·이벤트 문자열 불변.
- "ROE 높은 종목 3개" → pending=[ROE], `top_n=3`, asked(industry/threshold) → "10%"(bare_number, pending 1개 승격; `top_n` 유지) → "반도체" → answered, 상위 3개.
- "3개"가 `bare_number`로 오인되는 경로: 프롬프트 규칙 + 충돌 가드. 그래도 LLM이 `bare_number=3, top_n=None`으로 내면 기존 동작(임계값 후보)이 되므로 §9에서 few-shot으로 확인.

## 6. 영향 단위별 변경점 (구현 범위 한정)

| 단위/파일 | 변경 |
|---|---|
| D0 `state.py` | `Conditions.top_n: NotRequired[int \| None]` (승인 후) |
| D7 `analyze.py` | §5: `AnalyzeOut.top_n`, 프롬프트 1항목, `_top_n`, `TOP_N_MAX`, 병합 1줄, 보정 1규칙, 이벤트 문자열 |
| D7 `plan.py` | **변경 없음**(`candidate_limit`·`search_scope` 불변) |
| D6 `constraints.py` | 순수 함수 1개 추가: `rank_passed(codes, metrics, constraints, top_n) -> list[str]`. `codes`=PASS 코드(후보 순서), 반환=정렬(top_n 있을 때만) 후 `[:top_n]`. `top_n` 없으면 입력 그대로 반환. judge/respond 공용(순위 로직 한 곳) |
| D8 `judge.py` | 공시 대상 `passed`를 `rank_passed(passed, rows, constraints, cond.get("top_n"))`로 대체. `verdicts/metrics` 산출은 불변 |
| D9 `respond.py` | answered 분기: `all_pass=_passed(state)`, `shown=rank_passed(all_pass, metrics, constraints, top_n)`; 표·공시 줄·`report_draft`·`last_result_summary.top_codes`는 `shown` 사용, `pass_count=p`. 안내 문구(§3) 추가, 요약 템플릿 문구에 "(상위 {N}개 표시)" 추가, 요약 facts에 `top_n`/`n_shown` 추가(숫자 검증 허용집합에 포함됨) |
| D10 `memory.py` | **변경 없음**(conditions 통째 저장) |
| D8 `execute.py`, D9 `replan.py`/`graph.py` | 변경 없음 |

## 7. 회귀·정합

- `top_n` 키 없음 → `rank_passed`는 입력을 그대로 반환, respond/judge 분기는 현재와 동일 경로. 기존 377 passed 유지가 목표(테스트 수정 없이).
- `top_n` 지정 시 정렬 기준이 달라짐: 시총 순이 아니라 제약 지표 순(§3). 사용자가 "낮은/상위"를 말한 요청의 기대와 일치. `top_n` 없는 요청은 시총 순 유지.
- 공시 의도·lookup 규칙(D7 요청 3, lookup 공시 미지원)은 불변.

## 8. 사용자 판단 사항

1. **(필수) D0 변경 승인**: §2의 `Conditions.top_n` 및 §6-1 문장. 미승인 시 구현 불가.
2. **(선택 제안) 기본 출력 개수 제한**: 개수 미지정 시에도 상위 K개(예: 10)만 출력하는 기본값. 현재 안은 회귀 방지를 위해 **두지 않음**. 두려면 `top_n` 기본값 주입 한 곳 변경이지만 기존 테스트·E2E 영향 있음.
3. **(선택 제안) 개수 부족 시 재계획**: "N개 요청인데 PASS<N"이면 `limit_up` 등으로 확장. D0 규칙 5 변경이 필요하여 이번 범위에서 제외.
4. 정렬 기준이 "첫 제약"인 점("PER 낮은 3개"인데 ROE가 첫 제약이면 ROE순)은 단순화. 메시지에서 순위 지표를 따로 뽑으려면 `rank_by` 필드가 추가로 필요(제외).

## 9. 테스트 관점 (LLM mock, 네트워크·키 불필요)

- analyze 추출: "3개"/"세 개"/"상위 5곳"(LLM mock이 정수 반환) → `conditions["top_n"]`; "PER 15 이하 3개"→PER 15 + top_n=3(bare_number 없음); `top_n=0/-1/2.5/true/"abc"/NaN` → 키 없음·status 정상; `top_n=50` → 20; `top_n==bare_number` 가드; top_n 없는 출력 → conditions에 키 없음(기존 테스트 동등성).
- 병합/후속: 새 screen 초기화(이전 `top_n` 승계 없음), asked 후속 병합 유지, refine "5개로"(3→5 덮어쓰기, 나머지 조건 유지, intent=refine), chitchat+top_n만(prev_intent=screen/refine → refine 보정; prev_intent=None → asked + top_n 저장), lookup/chitchat 불변.
- judge: PASS 7·`top_n=3`·`want_disclosures` → `search_disclosures` 3회만(정렬 상위 3개 코드), `top_n` 없으면 7회; 정렬 방향(`>=` 내림/`<=` 오름), None 뒤로, 동률 후보순.
- respond: 상위 N 표(정렬 순), 초과 안내 문구, N보다 적을 때 안내, `top_n==p` 무안내, 공시 줄은 출력 종목만, `report_draft` 키 = 출력 종목, `last_result_summary.top_codes/pass_count`, `top_n` 없음 → 기존 골든 출력과 바이트 동일, limit/error에서 `top_n` 무영향.
- 메모리: conditions에 `top_n`이 있는 상태로 update_memory→build_state 왕복.
- E2E 보강(그래프, Tool·LLM mock): ① "반도체 중 PER 낮은 종목 3개 + 공시" 유사 요청 → PASS 7에서 3행·"4개 더"·공시 호출 3회·`final(answered)` 1회. ② 직후 "5개로 바꿔줘" → 5행. 대표 요청 1~5 불변.
- 기존 377 passed 유지(수정 없이 통과해야 함).

## 구현 시 확인 항목

- 실 LLM(gpt-4o-mini)이 "3개"를 `top_n`으로, "10%"를 `bare_number`로 일관되게 분류하는지(한글 수사 "세 개", "TOP3", "하나만"). few-shot 보강 여부.
- 임의 상수: `TOP_N_MAX=20`(표 행 상한 `MAX_ROWS`와 같은 값으로 두는 점), 안내 문구 표현.
- 정렬 기준(첫 제약) 체감 확인: 다중 제약 요청에서 사용자 기대와 어긋나는 사례 수집.
- 요약 문장 LLM의 숫자 검증: `top_n`/`n_shown`이 facts에 들어가 허용되는지 확인.
- `NotRequired` 키를 가진 TypedDict가 LangGraph `AgentState` 컴파일에서 문제없는지(langgraph 실행 환경).
- 알려진 한계: 개수 해제 불가, refine 시 전체 재실행, 개수 부족 시 자동 확장 없음.
