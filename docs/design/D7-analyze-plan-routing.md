# D7 analyze + plan + 라우팅 (`backend/agent/nodes/analyze.py`, `plan.py`, `backend/agent/llm.py`, `graph.py`의 analyze 이후 엣지)

기준: DESIGN §2·§4·§7·§10, D0 §3~§6, D1 §4~§5, D2 §3~§5, D3~D6의 '요청/위임' 항목, 구현 코드(`state.py`, `config.py`, `identity/resolve.py`, `events/bus.py`, `tools/*`). 의사코드이며 구현 아님. D0·DESIGN은 수정하지 않고 요청만 올린다.

## 1. 목적·범위

| 노드/규칙 | D7 소관 | 비소관(경계) |
|---|---|---|
| analyze | 의도 분류, 조건 추출·병합, 산업 해석(`resolve_industry`), 되묻기 판단·질문 문장, `target_stock` 원문 추출 | report_save 종목명→코드 변환 및 변환 실패 처리(D8 `resolve_stock`), status `answered/limit/error` 설정(D9) |
| plan | `search_scope` 초기화·재계산, `plan[]` 작성(규칙만, LLM 없음; 모든 step은 Tool 입력 스키마 만족, lookup의 `resolve_stock` 1회 포함) | Tool 실행·캐시·후보별 전개(D8, §5-3), 재계획 분기·`replan_count/expansion_step/replan_log` 갱신(D9) |
| 라우팅 | `analyze` 이후, `plan` 이후 엣지 | execute→judge→(respond/replan) 엣지와 규칙 1~6(D9), 그래프 조립 `graph.py` 전체(D9가 D7의 두 라우터 함수를 등록) |

LLM 호출은 **analyze의 1회뿐**이다(plan·라우팅은 규칙 코드). LLM 클라이언트 모듈 `backend/agent/llm.py`는 D7이 정의하고 D9(respond 요약 문장)가 재사용한다.

## 2. AgentState 입출력

| 노드 | 읽는 필드 | 쓰는 필드(변경 키만 반환) |
|---|---|---|
| analyze | `session_id, user_msg, conditions, prev_intent, last_status` (모두 D10이 주입, 없으면 D0 기본값) | `intent, intent_reason, conditions, target_stock, missing, status`(asked/error일 때만), `clarify_question`(asked일 때만), `errors` |
| plan | `intent, conditions, replan_log, target_stock`(lookup의 `resolve_stock`용) | `search_scope, plan` |

- analyze가 반환하는 `intent`는 **코드 보정 후 최종 의도**다(D10이 `last_intent`로 저장). `status`는 `asked`/`error`일 때만 설정하고, 그 외에는 건드리지 않는다(D9가 설정).
- 이전 턴 대화 원문은 LLM에 주지 않는다. 입력은 `user_msg` + 이전 `conditions` 요약 + `prev_intent` + `last_status`뿐이다.

## 3. LLM 사용 (`backend/agent/llm.py`)

- **엔드포인트**: DESIGN·config의 GMS 설정을 그대로 사용 — `settings.GMS_BASE_URL`(OpenAI 호환) + `/chat/completions`, `settings.GMS_MODEL`, 헤더 `Authorization: Bearer settings.GMS_API_KEY`. 다른 모델/엔드포인트를 쓰지 않는다. HTTP는 이미 쓰는 `httpx`(추가 의존성 없음).
- 인터페이스(동기화 없이 async):
  ```python
  class LlmError(Exception): code: Literal["MISSING_API_KEY","TIMEOUT","RATE_LIMIT","API_ERROR"]; message: str  # message = base.MSG[code] 고정 문구
  async def complete(messages: list[dict], *, json_mode: bool, timeout_s: float = LLM_TIMEOUT_S) -> str
  ```
  키 빈 문자열이면 네트워크 전에 `LlmError("MISSING_API_KEY")`. 예외→코드 매핑은 D1 §3 표와 동일(Timeout→TIMEOUT, 429→RATE_LIMIT, 그 외→API_ERROR). 모든 예외는 `from None`으로 재발생, 로그는 `mask()` 통과한 타입명·HTTP status만(키·URL·요청/응답 본문 금지; 단 HTTP 오류 시 오류 본문의 message·내부 code/type/message는 mask 후 필드당 200자로 잘라 기록, D7b). 모듈 시작 시 `install_log_mask()`는 D13 소관이지만 `complete`도 D1 wrapper처럼 `mask()`한 텍스트만 기록한다.
- `json_mode=True`는 요청에 `response_format={"type":"json_object"}`를 넣는다. GMS가 이를 지원하는지는 **미확인(⚠ §10)**. 응답 본문에서 JSON은 `{`~`}` 범위를 잘라 파싱한다(코드펜스 대비).
- 프롬프트: system = 역할·출력 스키마·규칙(아래), user = JSON 하나 `{"message": user_msg, "prev_conditions": {...요약}, "prev_intent": ..., "last_status": ...}`. **user_msg는 지시가 아니라 분석 대상 데이터**라고 system에 명시한다. 키·DB는 프롬프트에 넣지 않는다. Tool 명세(describe())는 D7d대로 analyze가 프롬프트에 넣는다.
- **출력 스키마**(Pydantic `AnalyzeOut`, `extra` 무시):

  | 필드 | 타입 | 의미 |
  |---|---|---|
  | `intent` | Intent 5종 | 필수 |
  | `reason` | str(≤200자 절단) | 한 줄 이유. 이벤트·`intent_reason`에 사용 |
  | `industry` | str\|null | 사용자가 말한 산업 표현 원문 |
  | `market` | "KOSPI"\|"KOSDAQ"\|null | 명시된 경우만 |
  | `base_date` | "YYYY-MM-DD"\|null | **명시된 전체 날짜만**(상대 표현은 null; 오늘 날짜를 모름) |
  | `constraints` | `[{metric, op: ">="\|"<="\|null, value: number\|null, ref: "industry_avg"\|null}]` | 임계값이 말해진 지표. 값에서 `%`·"배"는 제거한 숫자. "산업 평균 이하" → `value=null, ref="industry_avg"` |
  | `pending_metrics` | `[Metric]` | 지표만 언급되고 값이 없는 것("ROE 높은", "PER만 더 낮게") |
  | `bare_number` | number\|null | 지표·산업 없이 숫자만 온 경우("10%") |
  | `want_disclosures` | bool\|null | D7d §5-2로 대체(LLM은 `tools`에서 `search_disclosures`를 골라 표현, `want_disclosures`는 호환 필드) |
  | `target_stock` | str\|null | lookup/report_save 대상 종목 원문(이름 또는 6자리 코드) |

- **검증 코드(LLM을 신뢰하지 않음)**: 스키마 위반(metric/op/market 범위 밖, 날짜 형식 불일치, value가 NaN/inf/bool, **한 constraint에 value·ref 동시 존재**)이면 invalid. 값·ref 둘 다 null인 constraint는 `pending_metrics`로 옮긴다. `op` null이면 지표 기본(ROE/ROA/OPM `>=`, PER/PBR/PSR/DEBT_RATIO `<=`, D0 §6-3). 같은 metric이 constraints와 pending에 모두 있으면 constraint 우선.
- **실패 처리**: invalid 출력·JSON 파싱 실패는 **1회 재호출 후** 여전히 invalid면 `status="error"`. `LlmError`는 재호출 없이 `status="error"`. 둘 다 `errors += ErrorRecord(tool="llm", stock_code=None, code, message)`(invalid는 code `API_ERROR`, 고정 문구 "외부 API 호출에 실패했습니다."). 키 누락은 `MISSING_API_KEY` → D0 §5대로 `final(status=error)`(프론트 키 안내). 사용자 노출 문구는 D9가 `errors[0].message`로 만든다. 원문 응답·프롬프트는 로그·이벤트에 남기지 않는다.
- LLM 출력은 코드가 검증한 뒤 상태 값으로만 쓰이며 실행되지 않는다. `industry`/`target_stock`은 이후 D2 함수가 한 번 더 해석·검증한다(프롬프트 주입으로 얻을 수 있는 것은 잘못된 조건 값뿐).

## 4. analyze

### 4-1. 처리 순서
1. `stage_start(analyze)`. 입력 기본값 채움.
2. LLM 호출·검증(§3). 실패면 status=error 반환(6단계 이벤트만 발행) → 라우팅으로 respond.
3. **의도 보정(코드)** — `followup := last_status=="asked" and prev_intent in {screen,refine}` (D0 §6-2·(a) 확정).
   - `last_status=="asked"` and `prev_intent in {lookup,report_save}` and `target_stock`이 있고 industry/constraints/pending/bare_number가 모두 비었으면 → `intent=prev_intent`(종목명만 오는 이어가기).
   - `followup`이고 `intent=="chitchat"`인데 industry/constraints/pending/bare_number 중 하나라도 추출됐으면 → `intent=prev_intent`("10%"만 온 경우 등).
   - `last_status=="limit"`이고 `bare_number`만 있으면(다른 추출 없음) → `intent="refine"`(D0 §6-4).
4. **intent별 분기**
   - `chitchat`: 조건·메모리 불변(`conditions` 반환 안 함). 라우팅이 respond로 보냄.
   - `lookup`/`report_save`: `conditions` 불변(D0 §6-1). `target_stock` 없으면 `missing=["target_stock"]`, asked. 있으면 그대로 통과(이름→코드는 D8). 이번 메시지의 `want_disclosures`는 반영하지 않는다(§9 위임표, 요청 3).
   - `screen`/`refine`: §4-2 병합 → §4-3 되묻기 판단.
5. 이벤트 발행(§7). 결과 반환.

### 4-2. 조건 병합 (screen/refine)
- **기준(base)**: `followup` 또는 `intent=="refine"`이면 `state.conditions`, 아니면(새 screen) 빈 기본 conditions(D0 §6-1 표; 이전 업종 재사용 없음). `prev_intent=None`인 refine은 이전 조건이 비어 있으므로 일반 되묻기 판단(§4-3)에서 걸린다(D0 §6-7; 완전한 조건이 같이 오면 통과 — §9 해석).
- 새로 추출된 `market/base_date`는 덮어쓰기. `want_disclosures`는 null이 아니면 덮어쓰기, 새 screen에서 null이면 False.
- **industry**: 추출값이 있으면 **Tool 전에 `r = await to_thread(resolve_industry, text)`**(D2 API, 결정적·DB·LLM 없음).
  - `ok` → `conditions.industry = text`(원문 저장; D3가 같은 함수로 재해석하므로 일관).
  - `ambiguous` → `industry=None`, `missing`에 `industry`, 질문에 `options[].term`을 나열("‘X’는 여러 산업으로 해석됩니다: a, b, c. 어느 쪽인가요?"; 추측 안 함).
  - `unknown` → `industry=None`, `missing`에 `industry`, 질문 "‘X’ 산업을 인식하지 못했습니다. 산업 이름을 다시 알려주세요."
  - `ValueError`(용어 파일 오류) → `status=error`, `ErrorRecord(tool="resolve_industry", code="API_ERROR")`.
  - 이전에 저장된 업종이 있어도 refine에서 새 업종이 해석 실패하면 `None`으로 비워 되묻는다(사용자가 바꾸려 한 의도 존중).
  - 빈 캐시(`n_listed=0`)는 `resolve_industry`를 통과한다(D2). 캐시 미준비는 D3 실행 시 `CACHE_NOT_READY`로 error(되묻기 대상 아님).
- **constraints**: 추출된 각 constraint를 `metric` 단위로 덮어쓰고(없으면 추가) 해당 metric을 `pending_metrics`에서 제거. 추출된 pending 지표는 `pending_metrics`에 추가(중복 제거; 같은 metric의 기존 constraint는 값이 채워질 때까지 유지, D0 §6-3).
- **bare_number**(없으면 건너뜀):
  1. `last_status=="limit"`: `constraints`가 1개면 그 metric의 `value`를 덮어쓰고 `ref=None`; 2개 이상이면 `missing`에 `threshold`로 asked(D0 §6-4).
  2. 그 외(followup/refine): `pending_metrics`가 1개면 그 지표를 기본 op로 승격(constraint 추가, pending에서 제거); 2개 이상이면 `threshold`로 asked(D0 §6-3·(b)); 0개면 무시.
- 단위: 값은 지표 단위 그대로(ROE 10=10%, PER 15=15배, D6 가정 확인).

### 4-3. 되묻기 판단·`asked` 처리
- `missing` 계산(순서 고정): `conditions.industry is None` → `industry`; `pending_metrics` 비어있지 않음 또는 `constraints` 비어있음 → `threshold`; (lookup/report_save) `target_stock` 없음 → `target_stock`. 비어 있지 않으면 **Tool 호출 전** `status="asked"`, `clarify_question` 작성, 병합된 `conditions`는 그대로 반환(D10이 asked여도 저장 — D0 §6-5).
- **질문은 코드 템플릿**(LLM 생성 아님): industry(위 ambiguous/unknown 문구가 우선, 없으면 "어떤 산업을 대상으로 할까요?"), threshold(pending 지표별 "‘ROE’의 기준값을 알려주세요. 예: ROE 10% 이상" ; 이미 그 지표의 constraint가 있으면 "현재 PER 상한 15 → 얼마로 낮출까요?" 식으로 현재값 표기, 값을 추정하지 않음 — DESIGN §7; constraints·pending 모두 비면 "어떤 지표와 기준값으로 찾을까요? 예: ROE 10% 이상"), target_stock("어느 종목인가요?"). 여러 항목이면 한 문장씩 이어 붙인다. 지표 한글명 표는 D9와 공유할 모듈 상수(`METRIC_LABEL`, 구현 시 한 곳에 정의).
- 시나리오(D0 §6-6): "ROE 높은 종목" → missing=[industry,threshold], pending=[ROE], asked → "10%" → ROE 승격, missing=[industry], asked → "반도체" → missing=[] 통과. "ROE 10% 넘는 종목" → missing=[industry] → "반도체"에서 ROE 조건 보존.
- 대표 요청 5("ROE 높은 종목")와 §10-4("안녕") 추적: §8.

## 5. plan (규칙만, `async def plan(state) -> dict`)

### 5-1. search_scope (재실행에 안전하도록 매번 처음부터 계산)
```
scope = {market: conditions.market, extra_industries: [], candidate_limit: None}
for rec in replan_log:                         # D9가 재계획 시 replan_log에 추가 후 plan 재진입
    market_expand   -> scope.market = None
    similar_industry-> scope.extra_industries = similar_industries(conditions.industry)   # D2, to_thread
    limit_up        -> scope.candidate_limit = stock_candidates.MAX_LIMIT                 # D3 상수
```
- 첫 실행에서는 `replan_log=[]`이므로 초기값이 된다("초기화 주체는 plan", D0 §4). `similar_industries`는 예외/빈 값이면 `[]`(similar_industry 재계획이 효과 없는 경우는 D9가 이미 건너뜀; 방어).
- `expansion_step/replan_count/replan_log` 갱신과 `replan` 이벤트 발행은 D9(§9 협약). plan은 로그만 읽는다.

### 5-2. plan[] 작성
**원칙**: plan이 만드는 모든 step의 `input`은 해당 Tool 입력 스키마(CandidateInput/FinInput/DisclosureInput)를 그대로 만족한다(D0 §4 준수). `stock_code`가 없는 불완전 step은 만들지 않는다.

| intent | plan |
|---|---|
| screen/refine (followup 포함) | `find_stock_candidates` step **1개**. input = `{industry, market:scope.market, base_date, extra_industries:scope.extra_industries, limit:scope.candidate_limit}` — **값이 None/빈 리스트인 키는 생략**(D1 `normalize`로 기본값이 채워지고 `[]`/`None` 차이로 캐시 키가 달라지는 것을 방지; 재계획 시 같은 입력이면 같은 키) |
| lookup | `get_financials` step **1개**. input = `{stock_code, base_date?}`(base_date None이면 키 생략). 종목이 이미 식별돼 있어야 하므로 `stock_code`는 analyze의 `target_stock` 원문이 아니라 **D2 `resolve_stock` 결과 코드**여야 한다. 따라서 plan이 `resolve_stock`(결정적·DB, `to_thread`)을 호출해 코드가 확정될 때만 step을 만든다. 변환 실패(not found/ambiguous)면 plan은 `[]`를 반환하고 D8/D9가 D0 §6-8대로 처리한다(plan은 status를 설정하지 않음) |
| report_save | `[]` (종목명→코드 변환은 D8, §5-3) |
| chitchat | plan 미진입(§6) |

- 공시 호출 노드·시점은 D8/D9 확정(D0 §6-11). plan은 `search_disclosures` step을 만들지 않는다.
- Tool 이름은 `registry.get(name)`으로 등록 여부를 점검하는 테스트를 둔다(`describe()`는 analyze가 LLM에 넘긴다(D7d). plan은 LLM 미사용).
- 이벤트: `stage_start/end(plan)`만(재실행마다 재발행, D0 §3).

### 5-3. D8 위임 사항 (후보별 전개·종목 변환)
- **전개 규칙(한 줄)**: 후보별 `get_financials`는 항상 실행, `search_disclosures`는 `want_disclosures`일 때만 실행하며, 전개는 D8이 `find_stock_candidates` 결과(`state.candidates`)를 보고 만든다. D8이 만든 전개 step도 FinInput/DisclosureInput 스키마를 만족한다(`stock_code` 포함).

| 항목 | D8 책임 | D7(plan)은 |
|---|---|---|
| 후보별 `get_financials` | `candidates` 각 `code`마다 실행(PlanStep 없이 D8이 직접 생성) | step을 만들지 않음 |
| 후보별 `search_disclosures` | `conditions.want_disclosures`일 때만. 대상 종목·호출 시점·실패 표기는 기존대로 D8/D9 확정(D0 §6-11) | step을 만들지 않음 |
| report_save 종목 변환 | plan이 `[]`여도 D8이 `state.target_stock` 원문을 `resolve_stock`으로 코드 변환하고 실패 처리(D0 §6-8) | `[]` 반환 |
| lookup 변환 실패 | plan이 `[]`를 반환한 경우 D8이 `target_stock`을 다시 `resolve_stock`해 none/multiple을 D0 §6-8대로 처리 | `[]` 반환 |

## 6. 라우팅 (D7 소관 두 함수, `graph.py`가 등록)

```python
def route_after_analyze(state) -> Literal["plan","respond"]:
    if state.get("status") in ("asked","error"): return "respond"   # 되묻기/LLM·산업 해석 오류
    if state.get("intent") == "chitchat":        return "respond"
    return "plan"                                                    # screen/refine/lookup/report_save
# plan -> execute 는 무조건 엣지
```
- `lookup`/`report_save`도 plan을 거친다(그래프 단순성, DESIGN §4 다이어그램 유지). execute 이후 분기(D9)는 intent별 종료 규칙을 따른다.
- chitchat/asked/error 경로에서 plan·execute·judge는 실행되지 않으며 start/end도 발행하지 않는다(D0 §3). respond는 `final` 1회 발행(D9).

## 7. 이벤트·로그 연계 (D0 §3, `bus.publish(session_id, type, payload_model)`)

| 시점 | 이벤트 |
|---|---|
| analyze 시작 | `StageStart(stage="analyze")` |
| 의도 확정 | `LlmDecision(kind="intent", decision=<intent>, reason=<intent_reason>)` |
| screen/refine/lookup/report_save 조건 확정 | `LlmDecision(kind="conditions", decision="industry=반도체; ROE>=10; PER<=industry_avg; pending=[]; missing=[]", reason=<병합 경로: new_screen/followup/refine/limit_number/…>)` — 키 없이 조건 요약 문자열만 |
| analyze 종료 | `StageEnd(stage="analyze", intent=<최종 intent 또는 None>, reason=<intent_reason>)` (LLM 실패 시 None) |
| plan | `StageStart/StageEnd(stage="plan")` |

- tool_call/tool_result는 D8, replan은 D9, final은 D9(모든 종료 경로에서 respond가 1회). D7은 `final`을 발행하지 않는다.
- 로깅: `logging`만 사용(`print` 금지), 텍스트는 `mask()` 통과. 로그에 user_msg 원문·LLM 성공 응답 원문은 남기지 않는다(HTTP 오류 본문의 진단 필드만 mask 후 필드당 200자로 기록, D7b; 이벤트의 `reason`은 LLM이 쓴 한 줄이며 200자 절단).

## 8. 대표 요청 추적 (DESIGN §10 중 analyze/plan 구간)

| # | 요청 | analyze | plan |
|---|---|---|---|
| 1 | "반도체 중 ROE 10% 넘고 PER 산업 평균 이하" | intent=screen, industry="반도체"(resolve ok), constraints=[ROE>=10, PER<=ref industry_avg], missing=[] → plan | plan=[find_stock_candidates{industry:"반도체"}] 1개. 후보별 financials(+want_disclosures일 때 disclosures)는 D8 전개 |
| 2 | "ROE 25% 넘고 부채비율 30% 이하 은행" | 동일 구조(industry="은행") | 첫 plan 동일. 재계획 시 D9가 replan_log 추가 후 plan 재실행 → market_expand/similar/limit_up 반영(캐시 적중은 D8) |
| 3 | 한 종목 NO_FINANCIALS | (D6/D8 소관) | – |
| 4 | "안녕" | intent=chitchat, 조건 불변 → respond (plan 건너뜀) | – |
| 5 | "ROE 높은 종목" | intent=screen, pending=[ROE], missing=[industry,threshold], asked, Tool 0회 | – |

## 9. 이전 단위 위임 사항의 처리 결과

| 출처 | 위임/요청 | D7 처리 |
|---|---|---|
| D0 §6-1 | 턴 유형별 conditions 처리 | §4-2(새 screen 초기화, followup/refine 병합, lookup/report_save/chitchat 불변) |
| D0 §6-2·(a) | 후속 병합 판별, `prev_intent∈{screen,refine}` 한정, 종목명-only 이어가기 | §4-1 3단계 `followup` 정의·예외 |
| D0 §6-3·(b) | pending 승격, 방향 기본 op, refine도 승격, pending≥2+숫자만→asked | §3 검증 기본 op, §4-2 bare_number 2 |
| D0 §6-4 | limit 직후 숫자만 | §4-1 3단계(intent=refine) + §4-2 bare_number 1 |
| D0 §6-6 | 빈 제약/pending 잔존/필수조건 누락 → missing·asked | §4-3 |
| D0 §6-7 | prev_intent=None refine | §4-2: 일반 되묻기 판단으로 흡수(§10 해석) |
| D0 §4 | `target_stock` 원문만 추출(이름→코드는 D2, 호출 D8) | §4-1 4단계 |
| D0 §4/§12 | `search_scope` 초기화 주체=plan | §5-1 |
| D2 요청 1 | `extra_industries` 형식(KSIC 접두 숫자 문자열), 채우는 주체=plan 재실행 | §5-1 `similar_industries` 결과를 그대로 사용(D3가 숫자 접두로 재해석) |
| D2 요청 2 / D3 요청 1 | 모호·미해석 산업 되묻기 | **채택**: analyze가 Tool 전에 `resolve_industry` 호출, unknown/ambiguous → asked(§4-2). D3의 `INVALID_INPUT` 안전망은 유지(이중 방어) |
| D3 요청 4 | `MAX_LIMIT` 사용 | §5-1 `limit_up`에서 import |
| D5 요청 2 / D0 §6-11 | 공시 호출 노드·시점, 실패 표기 | D8/D9 위임 유지. plan은 공시 step을 만들지 않음(§5-3: `want_disclosures`일 때 D8이 후보별 전개) |
| D6 가정(제약 value 단위) | `%` 제거 숫자 저장 | §3 프롬프트 규칙 + §4-2 단위 |
| D1 §4 | plan이 `describe()` 보고 PlanStep 작성 | **변경**: plan은 규칙만이라 `describe()` 미사용(DESIGN §4 표 "규칙 우선"과 일치). Tool 이름은 테스트로 레지스트리 정합 확인. D1 문구 정정 제안은 §10 요청 6 |
| D0 §4 `PlanStep.input` | "해당 Tool 입력 스키마 그대로" | **준수**(2회차): plan은 스키마를 만족하는 step만 생성. 후보별 전개는 D8(§5-3). D0 수정 요청 없음 |
| D4/D3 일부 | `base_date`: Tool 입력 형식 `YYYY-MM-DD` | §3 스키마가 명시 날짜만 허용, 형식 불일치는 invalid |

## 10. 상위 계약에 대한 요청 (제안만)

1. ~~PlanStep 템플릿 허용~~ — **철회**(2회차, B안). D0 §4는 수정 불필요. D0 수정 요청은 아래 2·5로 줄었다.
2. **D0 §6-6 보강**: "Tool 전에 analyze가 `resolve_industry`를 호출해 `unknown/ambiguous`면 `industry=None`, `missing=[industry]`로 asked"를 추가(D2 요청 2·D3 요청 1 확정 반영). `Missing` Literal 추가는 불필요.
3. **lookup 턴의 `want_disclosures`**: D0 §6-1은 lookup 시 conditions 불변이라 "삼성전자 PER 알려주고 공시도 보여줘"의 공시 의도가 저장되지 않는다. 지원하려면 lookup 전용 요청 단위 필드(예: `AgentState.lookup_disclosures: bool`)가 필요하다. 미반영 시 lookup 공시는 이번 범위에서 미지원(plan은 lookup에 disclosures를 넣지 않음).
4. **DESIGN §4 노드 표 'plan'** 설명은 "Tool 순서 결정"인데 D7은 규칙 기반 최소 step(후보별 전개는 D8)으로 한정함을 반영하고, **analyze LLM이 `response_format` JSON 모드를 쓴다는 점**과 `backend/agent/llm.py` 공유 모듈을 DESIGN §1 구조에 추가.
5. **D9 협약 명시(D0 §4)**: 재계획 시 D9가 `replan_count/expansion_step`을 올리고 `replan_log`에 추가한 뒤 plan을 재진입시키며, plan은 `replan_log`로 `search_scope`를 재계산한다는 분담을 D0에 한 줄 기재. **D9 착수 전 사용자 승인 필요.**
6. **D1 §4 문구 정정 제안**: "plan이 `describe()`를 보고 PlanStep을 만든다"를 "plan은 규칙 기반이며 `describe()`는 사용하지 않는다(Tool 이름은 `registry.get`으로 정합 확인)"로 정정(DESIGN §4 '규칙 우선'과 일치).
7. **D8 위임 명시**: §5-3의 전개 규칙·report_save/lookup 종목 변환(plan이 `[]`여도 D8이 `resolve_stock`)을 D8 설계 입력으로 전달(D0 수정 아님).

## 11. 테스트 관점 요약 (LLM mock: `backend.agent.llm.complete` monkeypatch, 네트워크·키 불필요)

- analyze: 대표 5건(§8), 되묻기 시나리오 체인(D0 §6-6), pending 승격(숫자만·pending 1개/2개), limit 후 숫자만(constraint 1개/2개), refine "PER만 더 낮게"(질문에 현재값 표기), 후속 병합 vs 새 screen(이전 업종 비재사용), lookup/report_save 조건 불변·종목명-only 이어가기, chitchat 조건 불변, `last_status=="asked"`+`prev_intent∈{lookup,report_save}` 후 새 screen 오염 없음, resolve_industry unknown/ambiguous 되묻기(Tool 호출 0회, industry None), 불량 LLM 출력(value+ref 동시, 범위 밖 metric, 잘못된 JSON) → 재호출 1회 후 error, `LlmError` 각 코드 → status=error·errors 기록, 키 누락 → MISSING_API_KEY, 이벤트 순서·페이로드(키·원문 미포함), 로그에 `GMS_API_KEY` 미노출.
- plan: 첫 실행 scope 초기값, `replan_log` 조합별(market_expand/similar/limit_up 누적, 순서 무관) scope, 입력 키 생략 규칙(캐시 키 동일성은 `registry.cache_key_for`로), screen/refine은 항상 step 1개(want_disclosures 유무와 무관), lookup plan(`stock_code` 포함·스키마 검증, 변환 실패 시 `[]`)/report_save `[]`, 모든 step input이 Tool 입력 스키마를 통과, Tool 이름이 레지스트리에 존재.
- 라우팅: `status`·`intent` 조합 표 전체.
- `llm.complete`: respx로 HTTP 매핑(타임아웃/429/5xx/키 빈 문자열), 키 비노출.

## 구현 시 확인 항목

- GMS의 `response_format=json_object` 지원 여부, 실제 모델 `gpt-4o-mini` 응답 지연·한도(⚠ 실호출). 미지원이면 JSON 지시만으로 파싱.
- 임의 상수: `LLM_TIMEOUT_S`(D1 `TOOL_TIMEOUT_S`와 별개 상수; 초기값은 구현 시 실측), `reason` 200자 절단, 재호출 1회, 산업 `options` ≤5(D2).
- LLM의 intent 분류 정확도(특히 refine vs screen, 숫자만 응답, 종목명만 응답); 프롬프트 few-shot은 대표 요청 5건 + asked 후속 예시로 시작하고 결과를 보고 조정.
- 해석 사항(사용자 판단 가능): ① `prev_intent=None`인 refine이 완전한 조건과 함께 오면 screen처럼 실행됨(D0 §6-7은 "조건 부족으로 asked"). ② 새 업종 해석 실패 시 기존 업종을 비움. ③ asked 상태에서 사용자가 전혀 새로운 완전한 screen을 보내도 followup 병합이 적용됨(D0 확정 규칙의 귀결).
- 엣지: `bare_number`가 pending 0개·limit 아님일 때 무시됨, 한 메시지에 여러 의도, 같은 지표의 양방향 제약(ROE>=5와 ROE<=20; Constraint가 metric 단위 덮어쓰기라 불가), 영문/약어 지표명("영업이익률"→OPM) 매핑은 프롬프트 표로 처리, `base_date`가 미래·휴일이면 D3가 처리.
- `similar_industries` 호출은 요청 경로 동기 DB 읽기(`asyncio.to_thread`).

## 변경 이력

- 1회차: 최초 설계.
- 2회차(B안, 사용자 승인): 템플릿 step 전부 삭제(plan은 Tool 입력 스키마를 만족하는 step만 생성: screen/refine=candidates 1개, lookup=financials 1개), 요청 1(PlanStep 템플릿 허용) 철회로 D0 수정 요청 감소, §5-3 D8 위임 사항 추가(후보별 전개·report_save/lookup 종목 변환), §10 요청 6(D1 문구 정정)·7(D8 위임) 추가.


## D2e 갱신
- 프롬프트: `industry`는 겹치면 가장 구체적인 주제 표현, 산업 인식 방식 언급만 있으면 null. `general_knowledge`(일반지식 요청 명시 시 true; asked 직후 요청은 screen/refine) 키 추가. 상세는 `D2e-industry-llm-fallback.md`.
