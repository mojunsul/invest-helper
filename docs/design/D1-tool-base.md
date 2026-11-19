# D1 Tool 기반 (`backend/tools/base.py`, `registry.py`)

기준: D0-contract §1,2,5, DESIGN §1,3. ToolResult/ToolMeta/ToolError/ErrorCode는 D0 정의를 그대로 쓰며 여기서 재정의하지 않는다. 의사코드이며 구현 아님.

## 1. Tool 인터페이스 (base.py)

```python
class Tool:                          # frozen dataclass. 등록 시 1회 생성
    name: str                        # "find_stock_candidates" 등 (registry 키, PlanStep.tool과 동일)
    description: str                 # Agent가 보는 "언제 쓰나" 한 문장(DESIGN §3 표)
    input_model: type[BaseModel]     # 입력 스키마의 단일 원천. 기본값은 모델 필드 default로 표현
    fn: Callable[[BaseModel], Awaitable[ToolResult]]   # 원본 본문(테스트용으로 공개)
    kind: Literal["external","local"]   # 외부 API 툴 / 로컬 서버 API 툴(F3001, D1c). 기본값 없음
    requires_keys: tuple[str, ...]   # settings 속성명. 예: ("DATA_GO_KR_KEY",)
    timeout_s: float | None          # None이면 config 상수 TOOL_TIMEOUT_S

    async def run(raw: dict, *, timeout_s=None) -> ToolResult   # 래퍼. 절대 예외를 던지지 않음
    def normalize(raw: dict) -> dict                            # input_model.model_validate(raw).model_dump(mode="json") (기본값 채움, extra 필드는 무시되어 캐시 키에 불포함, json.dumps 가능 보장; 입력 필드는 str/int/float/bool/None/list 계열로 제한). 검증 실패 시 ValidationError

class ToolFailure(Exception):        # 본문이 의도적으로 실패를 알릴 때. code: ErrorCode, message: str(한국어 요약, 원문 금지)

def ok(data, *, requested=None, actual=None, estimated=False) -> ToolResult
def fail(code: ErrorCode, message: str) -> ToolResult           # meta=ToolMeta() 기본값. message에 키 포함 시 고정 문구로 대체(§3 NF3002)
def cache_key(tool: str, normalized_input: dict) -> str         # D0 §1 형식 그대로
def is_cacheable(r: ToolResult) -> bool                          # r.ok or r.error.code in {"NO_FINANCIALS","NO_RESULT"}
```

- 입력 스키마 표현: **Pydantic v2 모델**. Agent 노출용 JSON Schema는 `input_model.model_json_schema()`로 파생(별도 손작성 스키마 없음).
- 본문(`fn`)은 **async**, 검증된 입력 모델을 받아 `ToolResult`를 반환(`ok(...)`/`fail(...)`)하거나 `ToolFailure`/예외를 던진다. 빈 결과는 본문이 `fail("NO_RESULT", ...)`로 반환(`data=[]` 금지, D0).
- 외부 HTTP 직전 `throttle(url)`로 호스트별 최소 간격(D1b).

## 2. @register 동작

```python
@register(name="get_financials", description="...", input_model=FinInput, kind="external", requires_keys=("DATA_GO_KR_KEY","DART_API_KEY"))
async def get_financials(inp: FinInput) -> ToolResult: ...
```

- 시그니처: `register(*, name, description, input_model, kind: Literal["external","local"], requires_keys=(), timeout_s=None)`. `kind`는 필수 키워드(기본값 없음, 빠뜨리면 `TypeError`), 허용 값 밖이면 등록 시 `ValueError`(D1c). `requires_keys`에 `settings`에 없는 속성명이 있으면 **등록 시 `ValueError`**.
- import 시점에 `Tool`을 만들어 base.py 내부 `_TOOLS: dict[str, Tool]`에 넣는다. **중복 이름은 import 시 `ValueError`**(설정 오류이므로 조기 실패; 키 누락과 달리 런타임 사용자 요청과 무관).
- 데코레이터는 `Tool`을 반환(함수 자체는 `tool.fn`). 테스트는 `tool.fn` 직접 호출 또는 `tool.run` 호출 모두 가능.
- 순환 import 방지: 저장소 dict는 base.py, registry.py는 읽기 파사드 + `load_all()`(D3~D5 모듈 import로 등록 유발).

## 3. 래퍼 `Tool.run(raw)` 처리 순서

1. **키 검사**: `requires_keys` 중 `getattr(settings, k)`가 빈 문자열이면 즉시 `fail("MISSING_API_KEY", MSG[MISSING_API_KEY])`(아래 표의 단일 문구). 본문·네트워크 호출 전, 입력 검증보다 앞(fatal 우선 노출).
2. **입력 검증**: `normalize(raw)` 실패(`ValidationError`) → `INVALID_INPUT`.
3. **본문 실행** `asyncio.wait_for(fn(inp), t)`. `t = timeout_s if timeout_s is not None else (tool.timeout_s if tool.timeout_s is not None else TOOL_TIMEOUT_S)` (우선순위: `run` 인자 > `Tool.timeout_s` > 상수; `is None`으로 판별해 0이 무시되지 않게). 호출 전체 상한이며 본문 내부 복수 HTTP 호출을 합산한다.
4. 반환값이 `ToolResult`가 아니거나 D0 불변식(ok↔error, ok=True면 data 필수) 위반이면 로그 후 `API_ERROR`. 정상 반환이어도 `error.message`에 `mask()` 검사(아래 NF3002)를 적용.
5. 예외 → ErrorCode 변환(아래 표). `asyncio.CancelledError`/`KeyboardInterrupt`는 잡지 않고 전파(취소 보존).

| 발생 | ErrorCode | message(고정 한국어 요약) |
|---|---|---|
| 키 빈 문자열(1단계) | MISSING_API_KEY | "API 키가 설정되지 않았습니다. .env를 확인하세요." |
| pydantic `ValidationError`(2단계 입력 검증) | INVALID_INPUT | "입력값이 올바르지 않습니다." |
| pydantic `ValidationError`(본문 내부: 응답 파싱·ToolResult 생성) | **API_ERROR**(INVALID_INPUT 아님) | 아래 API_ERROR 문구 |
| `asyncio.TimeoutError` / `httpx.TimeoutException` | TIMEOUT | "외부 API 응답이 지연되었습니다." |
| HTTP 429 (`httpx.HTTPStatusError`, status==429) | RATE_LIMIT | "외부 API 호출 한도를 초과했습니다." |
| 그 외 `httpx.HTTPStatusError`/`httpx.RequestError`, JSON 파싱 오류, 알 수 없는 `Exception`, 반환 형식 위반 | API_ERROR | "외부 API 호출에 실패했습니다." |
| `ToolFailure(code, message)` | code 그대로(NO_RESULT, NO_FINANCIALS, RATE_LIMIT 등). **`code`가 ErrorCode 외 값이면 API_ERROR로 강제** | 본문이 준 message(키 포함 시 아래 규칙으로 대체) |

- 공공API가 HTTP 200 본문에 한도초과/키오류 코드를 담는 경우의 해석은 개별 Tool(D3~D5)이 `ToolFailure`로 던진다(래퍼는 HTTP 계층만 안다).
- 실패 시 `meta=ToolMeta()` 기본값.

### NF3002 키 비노출 (원문은 로그에만, 로그는 필터가 마스킹)
- **`mask(text)->str`** (base.py): 대상은 settings의 모든 키(`DATA_GO_KR_KEY`, `DART_API_KEY`, `GMS_API_KEY`) 각각 v에 대해 형태 집합 `{v, quote(v,safe=''), unquote(v), quote(unquote(v),safe='')}`(Encoding 키/Decoding 키와 Tool의 변환 양쪽을 포괄). **빈 문자열은 집합에서 제외**(전체 오염 방지), 긴 형태부터 정렬해 치환, **대소문자 무시**(`%2f`/`%2F`)로 `***` 치환. 키는 호출 시점의 settings에서 읽는다.
- **`ToolError.message` 키 포함 검사**: `mask(message) != message`이면 `API_ERROR` 고정 문구("외부 API 호출에 실패했습니다.")로 대체하고 code는 유지. 이 검사는 `fail()` 헬퍼 안에서 수행하고, step 4(본문이 반환한 모든 `ToolResult`의 `error.message`)와 `ToolFailure` 경로에도 동일하게 적용한다(본문이 `ToolResult(...)`를 직접 만들어도 step 4에서 걸림).
- **로깅 필터 `install_log_mask()`** (멱등, 중복 설치 안 함): 레코드가 거치는 최종 지점인 **핸들러**에 `logging.Filter`를 건다. 근거: 로거 Filter는 자식 로거에서 전파된 레코드에 적용되지 않지만, 핸들러 Filter는 어느 로거에서 왔든 그 핸들러에 도달하는 모든 레코드에 적용된다. 대상은 루트 핸들러와 `logging.Logger.manager.loggerDict`의 모든 로거가 가진 핸들러(`propagate=False`인 uvicorn.error/uvicorn.access 자체 핸들러 포함). 필터는 `msg`와 `args`의 각 항목을 개별 마스킹하며 `args`의 타입·길이를 보존한다(uvicorn 접근 로그 포맷터가 `args`를 5-튜플로 언팩하므로), `stack_info`·`record.__dict__`의 str 값(extra)도 마스킹하며, `exc_info`가 있으면 traceback을 포맷해 마스킹한 값을 `exc_text`에 **덮어쓴다**(Formatter는 기존 `exc_text`를 재사용하므로 포맷 전에 설정). `httpx`·`httpcore` 로거 레벨은 WARNING 이상으로 고정. 호출은 앱 시작 시 로깅 설정 이후(D13), 핸들러 추가 후 재호출.
- **wrapper 로그의 독립 보호**: wrapper는 필터 설치 여부와 무관하게 `logger.warning("tool=%s code=%s detail=%s", name, code, mask(traceback.format_exc()))`처럼 `exc_info` 없이 마스킹한 텍스트만 남긴다(D13이 `install_log_mask()`를 빠뜨려도 wrapper 로그 안전).

**누출 경로 점검표** (경로 → 닫는 규칙)

| 경로 | 규칙 |
|---|---|
| wrapper 로그(message/traceback) | wrapper가 `mask()` 후 기록(독립) + 핸들러 필터 |
| 루트 로거 / 자식 로거(propagate=True) | 루트 핸들러 필터 |
| propagate=False 로거(uvicorn.error/access) | `loggerDict` 순회로 자체 핸들러에 필터 |
| httpx·httpcore(URL 포함 INFO) | 레벨 WARNING 고정 + 핸들러 필터 |
| 개별 Tool 로그(D3~D5) | 핸들러 필터(message·args·exc_text·extra); `print` 금지는 D3~D5 규칙(stdout은 필터 밖) |
| 핸들러가 필터 설치 이후 추가됨 | `install_log_mask()` 재호출(위임 D13, 가정 7) |
| `ToolError.message` | `fail()`/step 4/`ToolFailure`의 `mask` 검사 → 고정 문구 대체 |
| `errors[]`·`tool_result.summary`·API 응답 | `ToolError.message`를 그대로 쓰므로 위 행으로 닫힘(D8은 `data`·원문을 summary에 넣지 않음) |
| `ToolResult.data` | 본문이 원천값만 담고 키·요청 URL 금지(D3~D5 위임; 키가 data에 있으면 설계 위반) |
| 테스트 출력(pytest 단언 repr) | `Settings` 키 `repr=False`(D0 요청 3) |

## 4. registry.py (Agent 노출)

```python
def load_all() -> None                       # backend/tools/*.py(base·registry·__init__ 제외)를 pkgutil.iter_modules로 순회 import → 등록. MSG(코드→고정문구 dict)는 base.py 정의
def describe() -> list[dict]                 # [{"name","kind","description","input_schema"(JSON Schema dict)}] — 이것만 Agent/LLM 프롬프트에 전달 (`kind` 추가는 D1c)
def get(name) -> Tool | None                 # 내부용(실행 노드 D8, 테스트)
async def call(name, raw) -> ToolResult      # 미등록 이름 → fail("INVALID_INPUT","알 수 없는 도구입니다."), 그 외 tool.run(raw)
def cache_key_for(name, raw) -> str | None   # normalize 후 cache_key. 미등록 이름 또는 입력 검증 실패면 None(캐시 우회)
```

- `describe()` 결과에는 `fn`, `requires_keys`, `timeout_s`, API 경로 등 내부 구현을 넣지 않는다. `input_schema`의 `title`/`$defs`는 pydantic 출력 그대로 허용.
- Agent(plan 노드 D7)는 `describe()`만 보고 `PlanStep{tool, input}`을 만들고, 실행은 D8이 `registry.call`로 한다.

## 5. tool_cache 접점

- **키 형식은 D0 확정, 키 생성 함수는 base.py(`cache_key`), 호출·조회·저장은 execute 노드(D8)가 소유.** Tool/래퍼는 `state`를 모른다(순수 호출).
- D8 흐름: `key = registry.cache_key_for(name, raw)` → 적중이면 저장된 dict로 재구성(`ToolResult(**cached)`), `tool_call.cache_hit=True`, `duration_ms=0` / 미적중이면 `await registry.call` 후 `is_cacheable(r)`일 때만 `tool_cache[key]=r.model_dump()`. 이벤트 발행·`duration_ms` 측정도 D8.
- 키는 기본값을 채운 입력(`normalize`)으로 만들어 `{"industry":"반도체"}`와 `{"industry":"반도체","market":None}`이 같은 키가 되게 한다.

## 6. 테스트 가능성

- `tool.fn(inp)`: 래퍼 없이 로직만. `tool.run(raw, timeout_s=0.01)`: 타임아웃 주입. 키 누락 테스트는 `settings`가 frozen이므로 `monkeypatch.setattr(base, "settings", Settings(...))` 형태로 대체 가능하게 base.py는 `settings`를 모듈 전역으로 참조.
- 외부 HTTP는 개별 Tool 책임(D3~D5)으로 `httpx` 클라이언트를 모듈 함수로 분리해 mock 가능하게 한다(예: `respx`).
- 래퍼 단위 테스트 항목: 예외→코드 매핑 표 전 행, 불변식 위반 반환, CancelledError 전파, `mask()`의 원문·quote·unquote·대소문자 다른 percent-encode·빈 키 케이스, 필터 설치 후 traceback·httpx 로그·propagate=False 로거 핸들러에 키 미포함(`install_log_mask()` 2회 호출 멱등 포함), `fail()`·직접 만든 ToolResult·ToolFailure 메시지에 키 포함 시 대체, 잘못된 `ToolFailure.code` 강제, 중복 등록·미존재 `requires_keys` 오류, `describe()`에 내부 필드 없음, `is_cacheable`/`cache_key` 기본값 정규화.

## 가정

1. Tool 본문은 async(FastAPI/LangGraph + httpx 비동기 전제). 동기 라이브러리가 필요하면 본문 안에서 `asyncio.to_thread` 사용(D3~D5).
2. HTTP 클라이언트는 `httpx`. 다른 라이브러리면 매핑 표의 예외 타입만 교체(D3~D5와 합의).
3. `TOOL_TIMEOUT_S`는 `backend/config.py` 모듈 상수(10.0)로 존재하며 `settings`가 아님(D0 §5 "코드 상수"). 키 이름 `DATA_GO_KR_KEY`/`DART_API_KEY`는 `settings` 속성명과 동일.
4. get_financials처럼 한 호출에 DART·금융위 다중 요청이 있는 Tool도 10초 합산 상한으로 충분하다고 가정. 부족하면 `@register(timeout_s=...)` 개별 상향(D4, D0 TOOL_TIMEOUT_S 의미와 충돌 없음).
5. 개별 Tool이 `requires_keys`를 올바르게 선언한다는 점은 D3~D5 위임. 선언된 키 외 키 누락은 래퍼가 감지하지 못함.
6. 위임: 이름→코드 변환·산업 캐시 접근(D2), 후보/재무/공시 로직과 공공API 본문 오류 해석(D3/D4/D5), 캐시 조회·이벤트 발행·duration 측정(D8), tool_result summary 문구(D8), `install_log_mask()` 호출 위치(앱 시작 코드, D13 main.py), API_ERROR/RATE_LIMIT/TIMEOUT/INVALID_INPUT→`FETCH_FAILED` ReasonCode 변환(D8/D9).
7. `install_log_mask()` 호출 이후에 추가되는 핸들러는 재호출 전까지 덮이지 않는다. 로깅 외 출력(print 등)은 금지 규칙으로만 다룬다(D3~D5).

## D0 계약에 대한 요청

1. `TOOL_TIMEOUT_S`의 위치를 "`backend/config.py` 모듈 상수(settings 외부)"로 명시해 주세요(현재 "코드 상수"만 있고 위치 미정; D1은 가정 3으로 진행).
2. `cache_key`/`is_cacheable` 헬퍼를 `backend/tools/base.py`에 둔다는 점(D0 §1 캐시 규칙의 구현 위치)을 D0 §1에 한 줄 추가해 주세요. 계약 내용 변경은 아님.
3. `Settings`의 키 필드(`DATA_GO_KR_KEY`, `DART_API_KEY`, `GMS_API_KEY`)는 repr/str에 값이 나오지 않도록(`repr=False` 또는 마스킹) 명시해 주세요(테스트·디버그 출력으로 키 누출 방지, NF3001/NF3002).

## 구현 시 확인 항목(검증 권고)

1. `registry.load_all()` 호출 주체는 앱 시작 코드(D13)이며, `_TOOLS`가 비어 있으면 lazy 호출(`call`/`describe` 진입 시)을 고려한다.
2. 로깅 미설정 시 `logging.lastResort`(stderr)는 핸들러 필터 밖이므로 D13이 로깅 설정(핸들러 존재)을 보장한다.
3. `loggerDict` 순회 시 `PlaceHolder`를 제외한다(`isinstance(x, logging.Logger)`).
4. `mask()` 형태 집합에 `quote_plus`는 없으므로 httpx의 실제 인코딩(`%2F`, `%2B`, `%3D`)으로 테스트해 누출 여부를 확인한다.
5. 필터는 `exc_text`만 덮고 `exc_info`는 남기므로 D13이 표준 `Formatter`를 쓰는지 확인한다(커스텀/JSON Formatter, Sentry류는 우회 가능).
6. 로그 `extra`에 키가 들어갈 수 있는 비문자열 객체(dict, 예외 등)를 넣지 않는다(필터는 str 값만 마스킹). D3~D5에 안내.
7. `install_log_mask()`는 동시 설치 시 락으로 중복 등록을 방지한다.
8. `MSG` 정의 위치 서술(§4 registry 주석)을 §3으로 옮기고, `MISSING_API_KEY` 키는 `MSG["MISSING_API_KEY"]`처럼 따옴표로 표기한다.
9. `input_model`에 `extra="forbid"`를 쓰지 않는다(`normalize`가 extra 필드에서 `ValidationError`를 냄). D3~D5에 안내.
