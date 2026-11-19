# D0 공통 계약

기준: DESIGN.md §2,3,4,5,6,7,10. 이후 단위(D1~D15)는 이 문서의 이름·타입만 참조한다. 의사코드는 Pydantic v2 / TypedDict 수준이며 구현 아님.

## 1. ToolResult (`backend/tools/base.py`)

```python
class ToolError(BaseModel):
    code: ErrorCode            # 아래 §2 Literal
    message: str               # 사용자 노출용 한국어 요약. 원문 오류/키/스택 금지(NF3002)

class ToolMeta(BaseModel):     # 항상 객체로 존재(None 아님). 실패 시에도 기본값으로 채움
    requested_basis: str | None = None   # 요청 기준 (예: "2026-11-18" 또는 "bizYear=2025")
    actual_basis: str | None = None      # 실제 사용 기준 (예: "FY2025"). 모르면 None
    estimated: bool = False              # 둘 다 non-None이고 정규화된 연도 기준으로 다르면 True, 그 외 False(정규화 규칙은 D4 위임). None 불허

class ToolResult(BaseModel):
    ok: bool
    data: Any | None = None              # ok=True면 None 불가(값 필수), ok=False면 반드시 None
    error: ToolError | None = None       # ok=False일 때만 값. ok=True면 반드시 None
    meta: ToolMeta = ToolMeta()
```

- 불변식: `ok == (error is None)`. model_validator로 강제. 빈 결과(후보 0개 등)는 `ok=False, NO_RESULT`(data=`[]` 아님).
- Tool 입구에서 모든 예외를 잡아 `ok=False`로 변환(`API_ERROR` 기본). 원문 오류는 로거로만.
- `tool_cache`에는 `.model_dump()`한 dict 저장. 키: `f"{tool}:{json.dumps(input, sort_keys=True, ensure_ascii=False)}"`(기본값 채운 입력). `cache_key`/`is_cacheable` 헬퍼는 `backend/tools/base.py`에 둔다. **`NO_FINANCIALS`/`NO_RESULT` 외의 `ok=False`는 캐시하지 않는다**(재계획 시 재호출). ok=True와 이 두 확정 실패는 캐시 적중 시 재호출 안 함.

## 2. 공통 실패 코드

```python
ErrorCode = Literal["MISSING_API_KEY","INVALID_INPUT","API_ERROR","RATE_LIMIT","TIMEOUT",
                    "NO_RESULT","NO_FINANCIALS","NO_INDEX"]
FATAL_CODES = {"MISSING_API_KEY"}      # 어느 Tool에서 나오든 즉시 종료(§4 규칙 1)
```

| 코드 | 범위 | fatal | 비고 |
|---|---|---|---|
| MISSING_API_KEY | 공통 | **Y** | 프론트에 키 누락 안내 |
| INVALID_INPUT / API_ERROR / RATE_LIMIT / TIMEOUT | 공통 | N | 후보 조회에서 나오면 §4 규칙 2(error). 종목 단위에서는 UNDECIDABLE(`FETCH_FAILED`)이고 재계획 트리거 아님 |
| NO_RESULT | candidates, disclosures | N | candidates만 재계획 대상(§4 규칙 3). disclosures는 "공시 없음" 표기 |
| NO_FINANCIALS | financials | N | 해당 종목 UNDECIDABLE |
| NO_INDEX | search_filing_text(선택 F3022) | N | 필수 범위 밖, 코드만 예약 |

`NO_REPLAN_CODES` 상수는 두지 않는다. 그 역할(재계획 금지 코드)은 "후보 조회가 `NO_RESULT` 이외로 실패 → 재계획 없이 error"(§4 규칙 2)로 흡수된다.

## 3. Event (`backend/events/bus.py`)

```python
EventType = Literal["stage_start","stage_end","tool_call","tool_result","llm_decision","replan","final"]
Stage = Literal["analyze","plan","execute","judge","respond"]

class Event(BaseModel):
    session_id: str
    seq: int          # 버스가 부여(세션별 1부터 +1). 발행자는 넣지 않는다
    ts: str           # UTC ISO8601, 버스가 부여 (예: "2026-11-18T09:00:00.123Z")
    type: EventType
    payload: dict     # type별 모델을 model_dump한 값 (아래)
```

- `publish(session_id, type, payload_model)`. 버스가 세션별 카운터(락 보호)로 seq/ts 부여 후 구독자 호출, 구독자 예외 삼킴. seq는 세션 전체에서 단조 증가(재시작 시 1로 리셋 → 저장 키 충돌은 run_logs `id` PK로 회피).
- **run 경계**: payload에 run_id를 두지 않는다. `/api/logs/{sid}`(D13)는 `stage_start(analyze)`~`final` 구간을 run으로 파생한다.
- **중복 발행**: 의도는 `stage_end(analyze).intent/reason`과 `llm_decision(kind=intent)` 양쪽 발행. 건너뛴 stage(chitchat/asked 경로의 plan/execute/judge)는 start/end 모두 비발행. **실제 실행된 노드는 재계획 때 다시 실행될 때마다 start/end를 재발행**한다.
- run_logs 매핑: `payload` → `payload_json`(ensure_ascii=False).

| type | payload (모든 필드 필수, `?`는 선택·None 허용) |
|---|---|
| stage_start | `stage: Stage` |
| stage_end | `stage: Stage`, `intent?: str`, `reason?: str` (analyze에만 채움) |
| tool_call | `tool: str`, `input: dict`, `cache_hit: bool` |
| tool_result | `tool: str`, `ok: bool`, `error_code?: ErrorCode`, `duration_ms: int`, `summary: str` (건수·핵심값; 캐시 적중이면 duration_ms=0) |
| llm_decision | `kind: Literal["intent","conditions","summary"]`, `decision: str`, `reason: str` |
| replan | `count: int`(1..REPLAN_MAX), `trigger: Literal["no_pass","no_candidates"]`, `change: Literal["market_expand","similar_industry","limit_up"]` |
| final | `status: Literal["answered","asked","limit","error"]` |

- 모든 요청은 정상/오류 무관 **`final` 1회**로 종료. chitchat은 `status="answered"`.

## 4. AgentState (`backend/agent/state.py`, LangGraph `TypedDict`, total=False)

```python
Intent = Literal["screen","lookup","report_save","refine","chitchat"]
Metric = Literal["ROE","ROA","OPM","PER","PBR","PSR","DEBT_RATIO"]   # OPM=영업이익률
Market = Literal["KOSPI","KOSDAQ"]
Status = Literal["answered","asked","limit","error"]
Missing = Literal["industry","threshold","target_stock"]
ReasonCode = Literal[            # Verdict.reasons 허용 값. 코드만, 문장 금지
  "THRESHOLD_NOT_MET",           # FAIL 전용
  "NO_FINANCIALS", "FETCH_FAILED",   # FETCH_FAILED: API_ERROR/RATE_LIMIT/TIMEOUT/INVALID_INPUT (상세는 errors[])
  "NO_MARKET_CAP", "NEGATIVE_EARNINGS", "NEGATIVE_EQUITY", "NONPOSITIVE_REVENUE", "NONPOSITIVE_ASSETS",
  "INDUSTRY_AVG_UNAVAILABLE"]    # ref="industry_avg"인데 industry_avg.value=None(표본 n=0)

class Constraint(TypedDict):
    metric: Metric
    op: Literal[">=","<="]
    value: float | None
    ref: Literal["industry_avg"] | None
    # 불변식: value와 ref 중 정확히 하나만 값을 가진다. 임계값이 없으면 constraint를 만들지 않는다(§6-6).
    # "PER<=산업평균" → value=None, ref="industry_avg"

class Conditions(TypedDict):     # 사용자가 말한 값만. 확장해도 바뀌지 않는다
    industry: str | None
    market: Market | None        # 사용자 지정 시장 보존(None=미지정=전체)
    base_date: str | None        # "YYYY-MM-DD", None=최근 영업일
    constraints: list[Constraint]
    want_disclosures: bool       # 기본 False. 사용자가 공시를 요청했으면 True(병합 규칙 §6-1)
    pending_metrics: list[Metric]   # 임계값을 기다리는 지표(값 없이 지표만 보존). 기본 []. 승격 규칙 §6-3

class SearchScope(TypedDict):    # 실제 탐색 범위(effective)
    market: Market | None
    extra_industries: list[str]  # 유사 업종(D2 산출). 기본 []
    candidate_limit: int | None  # None=D3 기본값

class PlanStep(TypedDict):  tool: str; input: dict   # input은 해당 Tool 입력 스키마 그대로

class Verdict(TypedDict):
    status: Literal["PASS","FAIL","UNDECIDABLE"]
    reasons: list[ReasonCode]    # PASS=[]; FAIL=["THRESHOLD_NOT_MET"]+UNDECIDABLE 제약의 사유 합집합(F3011 노출 보장); UNDECIDABLE=1개 이상(합집합)
    failed: list[Metric]         # FAIL일 때 불충족 제약의 metric 목록, 그 외 []
    # 집계: 하나라도 불충족이면 FAIL(UNDECIDABLE 제약이 섞여도 FAIL 우선). FAIL 없이 판단불가가 있으면 UNDECIDABLE. 전부 충족이면 PASS.
    # 제약이 0개면 판정하지 않는다(공허한 PASS 금지; analyze가 사전에 asked 처리하므로 judge에 도달하지 않음, §6-6).

class MetricRow(TypedDict):      # 수치와 메타 분리
    values: dict[str, float | None]   # 키: revenue/op_profit/net_income/assets/equity/mkt_cap + Metric 7종. 계산 불가=None
    fiscal_period: str | None
    actual_basis: str | None
    estimated: bool
    reasons: dict[Metric, ReasonCode]   # 값이 None인 지표만 사유 기록. 기본 {}. D6가 채움(lookup 지표별 사유 안내용)

class ErrorRecord(TypedDict):  tool: str; stock_code: str | None; code: ErrorCode; message: str
class ReplanRecord(TypedDict):   # 타입은 replan 이벤트 payload와 동일
    count: int; trigger: Literal["no_pass","no_candidates"]; change: Literal["market_expand","similar_industry","limit_up"]

class AgentState(TypedDict, total=False):
    session_id: Required[str]
    user_msg: Required[str]
    intent: Intent | None                   # 항상 요청마다 초기화(analyze가 설정)
    prev_intent: Intent | None              # D10이 메모리 last_intent를 주입(§6-5). 기본 None
    last_status: Status | None              # D10이 메모리의 직전 요청 최종 status를 주입. 기본 None. "직전 턴이 asked" 판별의 유일한 기준(§6-2)
    intent_reason: str | None
    conditions: Conditions                  # 기본 {industry:None,market:None,base_date:None,constraints:[],want_disclosures:False,pending_metrics:[]}
    target_stock: str | None                # lookup/report_save 대상(6자리 코드 또는 사용자 입력명 원문). D7 analyze는 원문만 추출, 이름→코드 변환 함수는 D2가 제공, 호출은 D8
    missing: list[Missing]                  # 되묻기 필요 조건 목록. 기본 []
    search_scope: SearchScope               # 기본 {market:conditions.market, extra_industries:[], candidate_limit:None}. 초기화 주체: plan 노드(D7)
    expansion_step: int                     # 기본 0. 0..3 = 마지막으로 적용한 확장 단계 번호(건너뛰면 건너뛴 번호 포함해 점프)
    replan_count: int                       # 기본 0. 0..REPLAN_MAX = 실제 재계획 횟수
    plan: list[PlanStep]
    tool_cache: dict[str, dict]
    candidates: list[dict]                  # find_stock_candidates data 행 그대로
    metrics: dict[str, MetricRow]
    disclosures: dict[str, list[dict]]      # code→[{title,date,url}]; 없으면 키 없음
    verdicts: dict[str, Verdict]
    industry_avg: dict[str, dict]           # {"PER": {"value": float|None, "n": int}} ; n=0이면 value=None
    replan_log: list[ReplanRecord]
    last_result_summary: dict | None        # {top_codes:list[str], pass_count:int}. 이전 턴 값은 D10이 주입, 이번 턴 값은 respond가 갱신
    errors: list[ErrorRecord]
    status: Status | None
    clarify_question: str | None            # status가 "asked"(analyze 작성) 또는 "limit"(respond 작성)일 때만 값
    answer: str | None                      # 본문 + 면책 문구(코드가 부착)
    report_draft: dict | None               # 종목별 draft는 D12가 스키마 확정
```

규칙(확정):
- 빈 값: 리스트/딕셔너리는 빈 값, 스칼라는 None(예외: `expansion_step`·`replan_count`는 0, `want_disclosures`는 False, `MetricRow.estimated`는 False). 읽는 쪽은 `state.get(k, 기본)`. 노드는 변경 키만 반환.
- **AgentState는 요청(/api/chat 1회)마다 새로 만들고** D10 세션 메모리로 일부 필드를 채운다. 메모리는 코드가 만든 요약·draft만 보관하므로 DESIGN §7 "Tool 원본 비보관"과 충돌하지 않는다(tool_cache/candidates/metrics 원본은 다음 요청에 넘어가지 않음).

| 구분 | 필드 |
|---|---|
| 턴 간 유지(D10 경유, 저장·갱신 시점은 §6-5) | `conditions`(`pending_metrics`, `want_disclosures` 포함), `prev_intent`(메모리 `last_intent`), `last_status`(메모리 직전 최종 status), `last_result_summary`, `report_draft` |
| 요청마다 초기화 | 나머지 전부(`user_msg, intent, intent_reason, target_stock, missing, search_scope, expansion_step, replan_count, plan, tool_cache, candidates, metrics, disclosures, verdicts, industry_avg, replan_log, errors, status, clarify_question, answer`) |

- **analyze 계약**: 필수 조건 누락 시 analyze가 `missing`을 채우고 `clarify_question`을 작성하고 `status="asked"`를 설정한다(Tool 호출 전). 라우터는 `status=="asked"`로 respond로 분기. chitchat은 respond가 `status="answered"`로 설정. 판단 세부는 §6-6.
- **재계획 술어(단일)**: `can_replan := replan_count < REPLAN_MAX and next_step is not None`. `next_step` = `expansion_step`보다 큰 단계 중 **효과 있는** 가장 작은 번호(효과 판정은 §6-12). 무효 단계는 건너뛰고 `expansion_step`은 실제 적용한 단계 번호(1↔`market_expand`, 2↔`similar_industry`, 3↔`limit_up`), `replan_count`는 +1 한 번. REPLAN_MAX는 config에서 1~3으로 클램프.
- `replan`은 노드가 아니라 judge(또는 execute)의 분기 결과이며, 그래서 `Stage` Literal에 없다. 아래 규칙 1~6의 평가와 `status` 설정 주체는 D9에 위임.
- **D9 협약**: 재계획 시 D9가 `replan_count`·`expansion_step`·`replan_log`를 갱신하고 plan을 재진입시킨다. D7(plan)은 `replan_log`로 `search_scope`를 매번 재계산하고, D8(execute/judge)은 `status`와 재계획 카운터를 건드리지 않는다.
- **종료/재계획 규칙 1~6은 intent가 screen/refine일 때만 적용**(순서대로). n_dec=PASS+FAIL 수, n_pass=PASS 수. 둘 다 재계획으로 누적된 현재 `candidates` 전체 기준(확장은 후보 집합을 넓히는 방향이므로 재평가 시 합집합).
  1. 어느 Tool이든 `MISSING_API_KEY` → `status="error"`.
  2. find_stock_candidates가 `NO_RESULT` 아닌 사유로 `ok=False`(API_ERROR/RATE_LIMIT/TIMEOUT/INVALID_INPUT) → 재계획 없이 `status="error"`.
  3. candidates가 `NO_RESULT`: `can_replan`이면 재계획(trigger=`no_candidates`), 아니면 `status="error"`(확장 여지 없음 안내).
  4. 후보≥1 & n_dec==0(전부 UNDECIDABLE) → 재계획 없이 `status="error"` + 사유 코드 요약.
  5. n_pass≥1 → `status="answered"`.
  6. n_pass==0 & n_dec≥1: `can_replan`이면 재계획(trigger=`no_pass`), 아니면 `status="limit"`(respond가 `clarify_question`로 임계값 완화 되묻기 작성).
  - `ok=False` 중 비캐시 대상은 재계획 시 재호출되고 NO_FINANCIALS/NO_RESULT는 캐시 적중.
- lookup/report_save 종료 status §6-8·9, 공시 호출 시점 §6-11.
- **면책 분리**: 코드 상수 `DISCLAIMER`를 respond가 `answer`에 부착하고, `/api/chat.disclaimer`에도 같은 상수를 별도 제공(상수 위치는 D9).

## 5. config.py / .env (`backend/config.py`)

`.env.example`과 TOOL_TIMEOUT_S, DB_PATH 2개 신규 항목을 제외하고 일치. 이 2개는 승인 전까지 코드 기본값(상수)만 사용하고 .env는 읽지 않는다. `python-dotenv`로 모듈 로딩 시 1회 읽어 `Settings`(frozen) 단일 인스턴스 `settings`로 노출.

| 키 | 타입 | 기본값 | 필수 | 비고 |
|---|---|---|---|---|
| DATA_GO_KR_KEY | str | "" | Tool 호출 시 | 금융위 주가·재무 API |
| DART_API_KEY | str | "" | Tool 호출 시 | corpCode/company |
| GMS_API_KEY | str | "" | LLM 호출 시 | OpenAI 호환 |
| GMS_BASE_URL | str | `https://gms.ssafy.io/gmsapi/api.openai.com/v1` | N | |
| GMS_MODEL | str | `gpt-4o-mini` | N | |
| REPLAN_MAX | int | 3 | N | 파싱 실패 시 3, 그 외 1~3으로 클램프 |
| TOOL_TIMEOUT_S | float | 10.0 | (신규, 코드 상수) | TIMEOUT 판정 기준. `backend/config.py` 모듈 상수이며 `settings` 속성이 아님 |
| DB_PATH | str | `invest_helper.db` | (신규, 코드 상수) | SQLite 경로 |

- **키 누락은 import 시점에 예외를 내지 않는다.** 각 Tool이 호출 시 해당 키가 빈 문자열이면 `ok=False, MISSING_API_KEY` 반환(NF3001). LLM 키 누락은 analyze/respond가 `final(status=error)`로 종료.
- 키 값은 로그/이벤트/오류 응답에 출력하지 않는다. `Settings`의 키 필드(`DATA_GO_KR_KEY`, `DART_API_KEY`, `GMS_API_KEY`)는 `repr=False`(또는 마스킹)로 repr/str에 값이 나오지 않게 한다.

## 6. 위임 규칙 목록 (노드·메모리 동작 규칙)

D0 계약이 아니라 **해당 단위 설계 시 확정해야 하는 입력 요구사항**이다(합의·검증된 내용을 보존). 필드 이름·타입·기본값은 §4를 따른다. 위임처: D7 analyze, D9 respond, D10 메모리, D8 execute.

1. **턴 유형별 conditions 처리** (판별 D7, 저장·주입 D10)

| 턴 유형 | industry / market / base_date | constraints | pending_metrics | want_disclosures |
|---|---|---|---|---|
| 새 screen (후속 병합 아님) | 전부 초기화, 이번 메시지 값만 사용(업종 없으면 이전 업종 재사용 없이 asked) | 초기화 후 이번 메시지 것만 | 초기화 | 초기화 후 이번 메시지 기준 |
| 후속 병합 (§6-2) | 이전 값 유지, 새로 추출된 필드만 덮어씀 | 이전 유지, metric 단위 덮어쓰기, 값이 채워진 pending 지표만 승격 | 승격된 지표만 제거 | 유지, 새로 언급되면 덮어씀 |
| lookup / report_save | 병합·초기화 적용 안 함, 이전 값 그대로 저장 | 그대로 | 그대로 | 그대로 |
| refine (후속 병합 아님) | 이전 값 유지, 새로 추출된 필드만 덮어씀 | 이전 유지, metric 단위 덮어쓰기(삭제는 범위 밖) | 유지(단 (b) 참조) | 유지, 새로 언급되면 덮어씀 |

2. **후속 병합 판별** (D7, 입력 `last_status`/`prev_intent`는 D10): 기본 규칙은 `last_status=="asked"`이면 이전 조건에 이어붙임. `target_stock`은 conditions 밖 요청 단위 필드로 항상 초기화·이번 메시지에서만 추출. `last_status=="asked"`이고 `prev_intent`가 lookup/report_save이며 메시지가 종목명뿐이면 intent를 `prev_intent`로 이어감. `last_status=None`이면 후속 병합 없음.
   - **알려진 미해결 틈 (a)**: 후속 병합은 `last_status=="asked"` **이고 `prev_intent∈{screen,refine}`일 때만** 적용해야 한다(lookup/report_save가 asked로 끝난 뒤의 새 screen 오염 방지; 종목명-only 이어가기는 예외). D7/D10에서 확정.
3. **pending_metrics 승격·부분 충족** (D7): 임계값 누락("ROE 높은 종목", "PER만 더 낮게")이면 constraint를 만들지 않고 `pending_metrics`에 지표 저장(기존 같은 metric constraint가 있으면 값이 채워질 때까지 유지). 값이 채워진 지표만 constraint로 승격하고 나머지는 남기며 `missing` 갱신, 부족하면 다시 asked. 업종만/숫자만/업종+숫자/일부만 어느 순서든 같은 규칙. 승격 시 `op`는 지표별 기본: ROE/ROA/OPM `>=`, PER/PBR/PSR/DEBT_RATIO `<=`(DESIGN §2), 사용자가 방향을 명시하면 그 값. 숫자만 왔는데 `pending_metrics`가 2개 이상이면 asked(`missing=["threshold"]`).
   - **알려진 미해결 틈 (b)**: refine 행도 pending_metrics에 값이 오면 후속 병합과 같이 승격해야 한다(숫자만 오고 pending 2개 이상이면 asked, `missing=["threshold"]`).
4. **limit 직후 숫자만** (D7): `last_status=="limit"`에서 숫자만 오면 `constraints`가 1개일 때만 그 metric의 `value`를 덮어쓰고(`ref`는 None으로), 2개 이상이면 asked(`missing=["threshold"]`)로 지표 지정을 요구.
5. **메모리 갱신** (D10): 요청 종료 시 intent가 screen/refine이면 `conditions`(asked로 끝났어도 병합된 값 포함), `last_intent=intent`, `last_status=status` 저장. lookup/report_save는 conditions(`pending_metrics` 포함)를 이전 그대로 유지하고 `last_intent`·`last_status`만 갱신. **chitchat은 메모리 불변**(last_intent·conditions·last_status 모두 → chitchat 직후 refine/후속 병합 정상). `last_result_summary`/`report_draft`는 새 결과가 생길 때만 갱신(새 screen 시작만으로 지우지 않음, 갱신 시점 D10). 시나리오: screen 완료→lookup→refine, screen asked→lookup→refine에서 conditions(`pending_metrics` 포함) 보존.
6. **analyze 되묻기 판단 세부** (D7): screen/refine에서 병합 후 `constraints`와 `pending_metrics`가 모두 비었거나 `pending_metrics`가 남았으면 `missing`에 `"threshold"`, Tool 호출 전 asked(예: 재시작 후 "반도체"). 필수 조건(industry, 각 제약 임계값, lookup/report_save의 target_stock) 누락 시 `missing` 채움. 시나리오: "ROE 높은 종목"(missing=[industry,threshold], pending=[ROE], asked)→"10%"(승격, missing=[industry], asked)→"반도체"(answered); "ROE 10% 넘는 종목"(missing=[industry])→"반도체"에서 ROE 조건 보존.
7. **prev_intent=None에서 refine** (D7): 이전 조건이 없으므로 조건 부족으로 asked.
8. **lookup 종료** (D9 status 설정, D8 실행): 지표 행 생성 → answered(None 지표는 `MetricRow.reasons`로 안내). `target_stock` 없음 → asked. 종목명→코드 변환 실패(없음/복수 매칭) → error. `MISSING_API_KEY`/get_financials `ok=False`(NO_FINANCIALS 포함) → error(UNDECIDABLE 표 없음). 재계획 없음. 변환 함수는 D2 제공, 호출 D8, 문구 D9.
9. **report_save 종료** (D9, draft 스키마 D12): `target_stock` 없음 → asked. 종목명→코드 변환 실패 → error(문구만 lookup과 다름). 세션 `report_draft` 없음/종목 불일치 → error("먼저 해당 종목을 조회하세요"). 일치 → answered(실제 저장은 `POST /api/reports`, chat은 안내만). 재계획 없음. `report_draft` 단위(종목 하나 vs 모음)는 D12.
10. **PER 분포 역산 미지원** (D10): `last_result_summary`에 지표 값이 없어 DESIGN §7 역산 미지원. 추정 임계값 없이 되묻기만. 지원하려면 summary 확장 필요(수정 제안 #9).
11. **공시 호출 시점** (D8/D9): `want_disclosures`가 True이면 search_disclosures를 PASS 종목에만 호출한다는 것까지 확정. 호출 노드·시점(judge 이후 execute 재진입 또는 respond 전), `NO_RESULT` 외 실패 표기는 D8/D9에서 확정(수정 제안 #10).
12. **재계획 단계 효과 판정** (D9): ① `search_scope.market is not None`이면 유효 ② D2가 비어있지 않은 유사 업종을 주면 유효 ③ `candidate_limit`가 D3 최대 상한 미만이면 유효(trigger=`no_candidates`일 때는 건너뛸 수 있음). 규칙 1~6 평가·`status` 설정 주체는 D9. `search_scope` 초기화 주체는 plan 노드(D7).

## DESIGN.md 수정 제안 (반영 완료)

1. **§3 ToolResult**: meta는 항상 객체, `requested_basis/actual_basis`는 `str|None`, `estimated`는 bool. `ok`/`error` 불변식, ok=True면 data 필수, 빈 결과는 NO_RESULT.
2. **[승인됨] §4 재계획 vs 전 후보 UNDECIDABLE**: DESIGN §4는 "충족 0 & 치명 아님이면 재계획"이라 전부 UNDECIDABLE도 재계획 대상으로 읽힌다. D0는 재계획을 `NO_RESULT` 또는 판정 가능≥1&충족 0일 때만 허용하고, 판정 가능 0개·후보 조회 실패는 재계획 없이 `status=error`로 확정.
3. **§3·§4 ErrorCode**: `NO_RESULT/NO_FINANCIALS/NO_INDEX`를 `ErrorCode` Literal에 편입. fatal은 MISSING_API_KEY만. DESIGN §4 "비재계획: MISSING_API_KEY, INVALID_INPUT"는 D0가 규칙 1·2(후보 조회가 NO_RESULT 외로 실패하면 재계획 없이 error)로 구체화했다(`NO_REPLAN_CODES` 상수는 두지 않음). 종목 단위 API_ERROR/RATE_LIMIT/TIMEOUT은 UNDECIDABLE(`FETCH_FAILED`).
4. **[승인됨 — Verdict reasons 병기, 공시 기본값] §4 AgentState**: DESIGN §10 요청 1의 "+공시 링크"는 사용자가 공시를 요청한 경우에만 해당한다고 해석해 `want_disclosures` 기본 False로 둔다(항상 포함이 의도라면 기본값 True로 변경 필요). 확정 필드 추가 — `search_scope`, `expansion_step`, `replan_count`(의미 분리), `status`, `clarify_question`, `missing`, `target_stock`, `disclosures`, `Conditions.want_disclosures`. Constraint 불변식(value/ref 택일), verdict `PASS/FAIL/UNDECIDABLE`(§5 한글 혼용 통일), `Verdict.reasons`/`failed`(집계: FAIL 우선이되 reasons에 UNDECIDABLE 사유도 병기해 F3011 노출 보장 — DESIGN §5 "충족/불충족과 섞지 않음"과 차이가 있어 승인 필요), `prev_intent`, `last_status`, `Conditions.pending_metrics`, `industry_avg={value,n}`, `MetricRow`.
5. **[승인됨] §4 재계획 확장 no-op 건너뛰기**: 시장 미지정이면 ①은 no-op, 유사 업종 없음이면 ②는 no-op 등 효과 없는 단계는 건너뛰고 다음 유효 단계로 진행, 유효 단계가 없으면 재계획하지 않고 limit(후보 0개면 error). DESIGN의 "시도마다 한 단계, 3회째 이후 중단"을 "효과 있는 단계만 세며 최대 REPLAN_MAX(1~3)회"로 갱신. 동작 변경: 시장 미지정이면 실제 재계획은 최대 2회(① 생략), REPLAN_MAX는 1~3으로 클램프.
6. **§3 find_stock_candidates 입력**: ②③ 표현을 위해 `extra_industries: list[str]?`, `limit: int?` 추가 필요. `market` None=전체.
7. **[승인됨: 확대 시 응답에 명시] §4/§7 사용자 지정 시장 확대**: 사용자가 KOSPI를 명시했는데 재계획 ①에서 전체로 확대하는 것을 허용으로 해석(conditions.market 보존, search_scope.market만 변경). 사용자 제약 침해 여부 승인 필요.
8. **§6 Event**: seq/ts는 버스가 세션 단위 부여, `publish(session_id, type, payload)`. run_logs 정렬은 `id` 기준 보완. run 경계는 stage_start(analyze)~final 파생. 의도는 stage_end·llm_decision 양쪽 발행, 건너뛴 stage 비발행, 재계획 시 재실행 노드 start/end 재발행. `llm_decision.kind`/`replan.trigger`/`replan.change` 값 집합 확정.
9. **[승인됨 — PER 분포 역산 미지원] §7 세션 메모리**: `{conditions, last_intent, last_result_summary}`에 `report_draft`(§9 "서버가 세션 draft를 읽음")를 포함하도록 명시. AgentState는 요청마다 새로 만들고 메모리로 채우며, tool_cache는 요청 간 유지하지 않음(재계획 내에서만 재사용). refine의 constraints는 metric 단위 덮어쓰기. `lookup/report_save` 대상 종목 필드 `target_stock` 추가. `last_result_summary`에 지표 값이 없어 "직전 결과 PER 분포 역산"은 D0 범위에서 미지원(DESIGN §7 기능 축소이므로 승인 필요. 지원 시 summary 확장 필요, D10 결정).
10. **§4 공시 호출 시점**: search_disclosures를 통과 종목에만 호출하는데, 이는 judge 이후라 "plan→execute→judge" 단방향 흐름과 어긋난다. 호출 노드/시점은 D8/D9가 정하며, 흐름이 바뀌면(예: judge→execute 재진입 또는 respond 전 단계) §4 다이어그램·노드 표 수정 필요.
11. **[승인됨] §4 재계획 위치**: 후보 0개(`NO_RESULT`) 재계획은 judge가 아니라 execute 직후 규칙 3에서도 발생해 DESIGN §4 다이어그램(judge→replan)과 다르다. `replan`은 노드가 아니라 분기 결과.
12. **[승인됨] §7 되묻기 후속 병합·메모리 갱신**: 메모리에 `last_status` 추가, "직전 asked(+prev_intent∈screen/refine)이면 후속 병합", "새 screen은 conditions 전체 초기화(이전 업종 재사용 없음)", "chitchat은 메모리 불변", (상세는 §6) lookup/report_save 조건 불변, 빈 제약 asked, limit 후 숫자 처리, lookup/report_save 종료 status는 DESIGN §7·§10에 없는 동작이다. 반영 승인 필요.
13. **§1/.env**: `.env.example`에 TOOL_TIMEOUT_S, DB_PATH 추가 제안(승인 전엔 코드 기본값). DESIGN §1 config 설명에 LLM 키가 `GMS_*`임을 반영.
