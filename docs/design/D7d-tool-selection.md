# D7d Tool 명세 전달과 LLM의 Tool 선택 (F3001·F3006 보강)

기준: 명세 F3001("Agent에는 이름·설명·입력 스키마만 넘긴다"), §5.1 목표("상황에 따라 Tool을 선택하여 호출"), §8.7 기획 검증("Tool 명세를 함께 넘겼을 때 LLM이 Tool 호출을 선택하는지 확인"), 체크리스트 1번("Agent에 넘기는 Tool 목록을 출력해 정의서와 대조"). 상위: DESIGN §3·§4·§6, D0 §3, D1 §describe, D1c, D7, D7c, D8 §5, D17 §7. 의사코드이며 구현 아님.

## 1. 목적

- analyze의 LLM 호출에 `registry.describe()`(name·kind·description·input_schema)를 넘기고, LLM이 **필요한 Tool 이름 목록 `tools`**를 구조화 출력으로 고르게 한다.
- 실행 순서·입력 구성은 지금처럼 규칙이 정한다(데이터 흐름 candidates→financials→disclosures, filing_qa→search_filing_text). LLM 선택은 **검증 후 반영**하고, 규칙과 다르면 이벤트로 남긴다.
- 새 의존성 없음. LLM 호출 횟수 불변(analyze 1회, invalid 시 재호출 1회 그대로).

## 2. 현재 상태

- `registry.describe()`는 테스트(test_d1·test_d17)에서만 호출된다. backend 어디서도 LLM에 넘기지 않는다(D7 §5-2에 "Agent/LLM에 describe()를 넘기지 않는다"고 명시).
- analyze LLM은 `intent`·조건·`want_disclosures`만 출력한다. plan이 intent→Tool을 규칙으로 고르고, D8이 후보별 `get_financials`, judge가 `want_disclosures`면 `search_disclosures`를 호출한다.
- 실측(`.venv`, Tool 4개 로드 후): `json.dumps(describe(), ensure_ascii=False)` = **2,371자**(search_disclosures 322 / search_filing_text 458 / find_stock_candidates 693 / get_financials 308자 + 설명).

## 3. 변경 범위

| 파일 | 함수/항목 | 변경 |
|---|---|---|
| `backend/agent/nodes/analyze.py` | `SYSTEM_PROMPT` | 출력 키 `tools` 추가, `want_disclosures` 줄 삭제(§4) |
| 같은 파일 | 신규 `tool_specs() -> list[dict]` | `registry.load_all()`(멱등) 후 `registry.describe()` 그대로 반환. 간결화 안 함 |
| 같은 파일 | `_ask_llm` | system 메시지 = `SYSTEM_PROMPT + "\n\n사용 가능한 Tool 명세(JSON):\n" + json.dumps(tool_specs(), ensure_ascii=False)`. user payload는 불변 |
| 같은 파일 | `AnalyzeOut` | `tools: list[str] = []` 추가. `want_disclosures`는 **필드 유지**(기존 mock·호환, 값이 오면 우선) |
| 같은 파일 | `analyze()` | ① 선택 정리 ② screen/refine에서 `want_disclosures` 반영 ③ `end()`에서 `llm_decision(kind="tools")` 발행(§5·§6) |
| `backend/agent/nodes/plan.py` | 신규 `rule_tools(intent, want_disclosures) -> list[str]` | 규칙상 이 요청이 쓰는 Tool 이름 목록(§5 표). analyze가 import해 비교에 사용. `plan()` 본문은 **불변** |
| `backend/events/bus.py` | `LlmDecision.kind` | Literal에 `"tools"` 추가(§8 D0 제안) |

비교·이벤트를 plan이 아니라 analyze에서 하는 이유: chitchat·asked 경로는 plan을 건너뛰므로 plan에 두면 그 경우 LLM 선택이 기록되지 않고, plan에 넘기려면 AgentState 필드가 새로 필요하다. 규칙 표(`rule_tools`)는 plan.py에 두어 "Tool 결정 규칙은 plan 소관"을 유지한다.

## 4. 프롬프트·구조화 출력 변경

SYSTEM_PROMPT 출력 키 목록에서 `want_disclosures` 줄을 지우고 다음을 추가한다.

```
- tools: 이 요청을 처리하는 데 호출해야 할 Tool 이름 목록. 아래 Tool 명세의 name에서만 고른다.
  Tool 없이 답할 수 있으면(인사·잡담·보고서 저장) [].
  예: "반도체 ROE 10% 이상 종목, 공시도" -> ["find_stock_candidates","get_financials","search_disclosures"]
      "삼성전자 PER 알려줘" -> ["get_financials"], "안녕" -> []
```

- Tool 명세는 system 끝에 붙인다(요청마다 같은 내용, user payload 키 집합 `{message, prev_conditions, prev_intent, last_status}` 불변).
- Tool 설명은 우리 코드의 고정 문자열이라 주입 위험이 없다. 사용자 메시지는 기존대로 "데이터"로 취급.
- `tools`가 리스트가 아니면 `AnalyzeOut` 검증 실패 → 기존 invalid 처리(재호출 1회 → 실패 시 API_ERROR). 필드가 빠지면 `[]`.

### 크기·비용·지연
- 추가 입력 2,371자 ≈ 1천 토큰 내외(한글 설명 비중, 추정). 기존 system 약 1.5천 자와 합쳐도 수천 토큰 이하. 출력 증가는 `tools` 배열 약 20~40토큰, `LLM_MAX_TOKENS=1024` 안.
- GMS haiku-5-5는 **요청당 1 Credit**이고 analyze 호출 횟수가 그대로이므로 **Credit 증가 없음**.
- 지연: 입력 1천 토큰 prefill 증가분은 haiku 기준 수십~수백 ms 수준(미측정, §7에서 실측). `LLM_TIMEOUT_S=30` 영향 없음.
- 스키마 간결화(`title` 제거 등)는 하지 않는다. F3001이 "입력 스키마를 넘긴다"이고 크기가 문제되지 않는다.

## 5. 반영 규칙

### 5-1. 선택 정리 (analyze, LLM 응답 직후)
```
valid   = [t for t in dict.fromkeys(o.tools) if registry.get(t)]   # 중복·미등록 제거
unknown = len(set(o.tools)) - len(valid)                           # 이름은 로그에 남기지 않고 개수만
```

### 5-2. `want_disclosures` (screen/refine 조건 병합 단계, 기존 `if o.want_disclosures is not None` 자리)
```
if o.want_disclosures is not None:   cond.want_disclosures = o.want_disclosures   # 호환(mock·구 응답)
elif "search_disclosures" in valid:  cond.want_disclosures = True
# 그 외: new_screen은 default False, followup/refine은 이전 값 유지
```
LLM의 Tool 선택이 실제 실행 흐름(judge 끝 공시 호출 여부)을 바꾸는 지점이다(F3012 "LLM이 실행 흐름을 바꾸는 판단").

### 5-3. 규칙 표 `rule_tools(intent, want_disclosures)` (plan.py)

| intent | 규칙 Tool 목록 | LLM 선택이 다를 때 |
|---|---|---|
| screen / refine | `find_stock_candidates, get_financials` (+ `search_disclosures` if want_disclosures) | 규칙 우선. 공시만 §5-2로 LLM 선택 반영 |
| lookup | `get_financials` | 규칙 우선 |
| filing_qa | `search_filing_text` | 규칙 우선 |
| report_save | `[]` | 규칙 우선 |
| chitchat | `[]` | 규칙 우선(LLM이 Tool을 골라도 실행 안 함) |

- 의도와 모순되는 선택(예: chitchat인데 get_financials, lookup인데 빈 목록)은 **의도가 우선**한다. 의도가 이미 LLM 판단이고, 보정 규칙(D7 §4-1)·데이터 흐름이 의도 기준이기 때문이다.
- LLM이 `[]`를 고르면 보통 intent도 chitchat/report_save이고 규칙도 `[]`이므로 기존 "Tool 없이 응답" 흐름과 같다. intent가 Tool이 필요한 의도인데 `[]`면 불일치로 기록만 하고 규칙대로 진행.
- plan의 step 작성·D8 전개·judge 공시 호출 로직은 **변경 없음**. 실제 실행 Tool은 언제나 규칙 목록의 부분집합이다(되묻기·변환 실패로 덜 실행될 수 있음).

## 6. 이벤트

analyze의 성공 경로마다 `end()`에서 1회(conditions 이벤트 앞) 발행. 실패 경로(LlmError·invalid)는 미발행.

| 필드 | 값 |
|---|---|
| kind | `"tools"` |
| decision | 규칙 목록 `",".join(rule)` 또는 `"none"` (실제로 계획될 Tool) |
| reason | `f"llm={','.join(valid) or 'none'}; unknown={unknown}; match={'yes' if set(valid)==set(rule) else 'no'}"` — `REASON_MAX`로 자름 |

- 순서: `stage_start(analyze)` → `llm_decision(intent)` → `llm_decision(tools)` → `llm_decision(conditions)`(있을 때) → `stage_end(analyze)`.
- `match=no`가 "LLM 선택과 규칙이 다름"의 기록이다. 매 요청 발행하는 이유: §8.7 "LLM이 Tool 호출을 선택하는지"의 증빙을 `/api/logs`에서 바로 보이게 하기 위함.
- 미등록 이름은 원문을 남기지 않는다(LLM 출력 문자열이 로그로 새는 것 방지).

### 체크리스트 1번 증빙("Agent에 넘기는 Tool 목록")
1. `_ask_llm`에서 `logger.info("analyze tools offered=%s", [t["name"] for t in specs])` — 실제 LLM 입력을 만든 그 리스트로 출력.
2. 정의서 대조용 출력: `.venv/bin/python -c "import json;from backend.agent.nodes.analyze import tool_specs;print(json.dumps(tool_specs(),ensure_ascii=False,indent=1))"` 결과를 DESIGN §3 표와 대조(제출물에 첨부).
3. 테스트가 fake `complete`에 전달된 system 메시지에 `json.dumps(registry.describe(), ensure_ascii=False)`가 그대로 들어 있음을 단언(§7).

## 7. 테스트 관점 (`tests/test_d7.py` 갱신 + 신규 케이스, LLM mock)

- **기존 mock 호환**: `tools`는 기본 `[]`, `want_disclosures`는 유지되므로 기존 JSON 응답 mock은 그대로 파싱된다. test_d16_e2e의 `want_disclosures=True/False` mock도 그대로 동작.
- **갱신 필요**: test_d7 `test_events_order_payload`(L276) 순서 단언에 `("llm_decision","tools")` 삽입과 뒤따르는 `ev[2]`·`ev[3]` 인덱스 +1, `test_chitchat_has_no_conditions_event`(L288) 단언을 `["intent","tools"]`로. test_d17_route L179는 respond 이벤트만 수집하므로 영향 없음(구현 시 실행으로 확인). 나머지는 `kind` 필터 사용이라 영향 없음.
- **신규**:
  1. system 메시지에 describe JSON 전체 포함, user payload 키 불변.
  2. `tools`에 미등록 이름·중복 → valid에서 제거, `unknown` 개수, 원문이 이벤트에 없음.
  3. screen + `tools`에 search_disclosures, `want_disclosures` 미제공 → `conditions.want_disclosures=True`. refine에서 미선택 → 이전 값 유지.
  4. chitchat + `tools=["get_financials"]` → 이벤트 `decision="none"`, `match=no`, plan 미진입(기존 라우팅).
  5. `tools`가 문자열 등 비리스트 → invalid 재호출 경로.
  6. `rule_tools` 표 전 행 + 반환 이름이 모두 `registry.get`에 존재.
- 전체 스위트 회귀 실행(`pytest -q`).

## 8. 구현 시 확인 항목

- **실호출 1~2회**(GMS haiku-5-5, 2 Credit 이내): ① "반도체 ROE 10% 이상 종목 찾고 공시도 보여줘" → `tools`에 find_stock_candidates·get_financials·search_disclosures, `want_disclosures=True`, 이벤트 `match=yes` 기대. ② "안녕" → `tools=[]`. 응답 원문·이벤트·소요 시간을 구현 보고에 기록. LLM이 `get_financials`를 빠뜨리는 등 `match=no`가 나와도 규칙이 실행하므로 FAIL 사유가 아니라 프롬프트 예시 보강 대상.
- **공시 미요청 screen**("반도체 ROE 10% 이상")에서 search_disclosures 미선택 확인(과다 선택 시 불필요한 OpenDART 호출·IP 차단 위험).
- 실제 입력 토큰 증가량·지연 증가 실측값 기록.
- refine에서 "공시는 빼줘"를 LLM이 이제 표현할 수단이 없다(want_disclosures 프롬프트 줄 삭제). 실사용상 필요하면 `want_disclosures:false` 한 줄을 프롬프트에 되살린다(필드는 남아 있음).

## 9. 상위 문서 수정 제안 (사용자가 방향 승인함 — 아래 최소 수정은 구현 단계에서 반영 예정)

1. **D0 §3 이벤트 표**: `llm_decision.kind: Literal["intent","conditions","summary","tools"]`. `bus.py` 동일. AgentState·Intent·EventType은 **변경 없음**.
2. **DESIGN §4 노드 표**: analyze 행에 "Tool 명세(`describe()`)를 받아 필요한 Tool 이름 선택(`tools`)" 추가. plan 행에 "LLM 선택은 검증(미등록 제거)·비교 후 규칙 우선, 공시 여부는 LLM 선택 반영" 추가.
3. **DESIGN §6**: `llm_decision` kind 예시에 `tools` 추가(값 집합은 D0 참조 유지).
4. **D7 §5-2**: "Agent/LLM에 describe()를 넘기지 않는다 — plan은 LLM 미사용" 문장을 "describe()는 analyze가 LLM에 넘긴다(D7d). plan은 LLM 미사용"으로 수정. **D7 §4·프롬프트**의 `want_disclosures` 출력 키 설명을 D7d §5-2로 대체.
5. **D1 §describe 주석·L97**: "Agent(plan 노드 D7)는 describe()만 보고" → "analyze(D7d)가 describe()를 LLM 프롬프트에 넣고, plan은 규칙으로 PlanStep을 만든다".

## 변경 이력
- 초안 작성.
