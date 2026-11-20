# D13 chat / logs API + 앱 조립 (`backend/main.py`, `backend/schemas.py`, `backend/api/chat.py`, `backend/api/logs.py`)

기준: DESIGN §1·§6·§9·§10, D0 §3·§4·§5, D1(mask·`load_all`), D2 §6(앱은 캐시 읽기 전용), D9 §6·§9-4·§11, D10 §5, D11 §2·§4~7, D12 §6·§9·§10, `config.py`. 의사코드이며 구현 아님. DESIGN·D0는 수정하지 않고 §11에 요청만 올린다. **이벤트 계약·`bus.py`·`run_turn` 시그니처·reports 라우터는 변경하지 않는다.**

## 1. 목적·범위

| 항목 | D13 소관 | 비소관 |
|---|---|---|
| 앱 조립 | FastAPI 앱, lifespan(시작 순서 §2), 라우터 마운트, 정적 파일, 앱 전역 오류 핸들러 | Tool/노드/DB 함수 본체 |
| `POST /api/chat` | 검증·세션 직렬화·`run_turn`+`collect`·`final` 보장·응답 조립(§5) | 응답 문장·면책(D9), 메모리(D10) |
| `GET/DELETE /api/logs*` | 목록·상세(run 분할)·삭제(§6) | 저장·조회 함수(D11) |
| `/api/reports*` | **마운트만**(`reports.router`, `sqlite.init_table()` 호출) | 라우트·검증·오류 변환(D12 `_Route`) |
| 화면 | 정적 서빙·응답 필드 계약(§9) | 렌더(D14/D15) |

## 2. 앱 조립 (`main.py`)

`app = FastAPI(lifespan=lifespan)`. 시작 코드는 lifespan에 둔다(테스트는 `with TestClient(app)`). 모든 `install`은 멱등이라 재진입 무해.

```
lifespan 시작 (순서 고정, 예외는 그대로 전파 = 기동 실패):
 1. logging.basicConfig(level=INFO)                 # 핸들러 보장
 2. install_log_mask()                              # D1 — 핸들러가 생긴 뒤, 다른 모듈 로깅 전에
 3. registry.load_all()                             # D1 §127: 호출 주체는 앱 시작
 4. sqlite.init_table()                             # D12 reports (순서 무관)
 5. logger_agent.install()                          # D11: init_table(run_logs) + 구독 (저장이 먼저)
 6. stream.install()                                # D11: 팬아웃 구독
 7. 정보성 로그만(실패 아님): 키 3종 중 빈 것의 "이름", identity 캐시 상태(빈/미완료이면 build CLI 안내)
라우터: include_router(reports.router), include_router(chat.router), include_router(logs.router)
정적:   마지막에 mount("/", StaticFiles(directory=<repo>/frontend, html=True))  # /api가 우선, 디렉터리 없으면 mount 생략
```

- 5→6 순서는 D11 §4(저장이 팬아웃보다 먼저) 요구. 2가 5보다 앞서야 `logger_agent`의 경고 로그도 마스킹된다.
- 키 누락·캐시 미구축은 **기동을 막지 않는다**(D0 §5: import/기동 시 예외 없음, 요청 시점에 `MISSING_API_KEY`/`CACHE_NOT_READY`로 사용자 안내). 7의 로그에도 키 값은 쓰지 않는다.
- 앱은 identity 캐시를 구축하지 않는다(D2: CLI `python -m backend.identity.build` 수동).
- 실행: `uvicorn backend.main:app --host 127.0.0.1 --port 8000`(로컬 단일 사용자 전제, §8). `uvicorn`은 `requirements.txt`에 없으므로 구현 시 버전 고정 추가.
- 종료 훅 없음(D11: 쓰기는 동기 커밋).

## 3. 오류 응답 `{code, message}` 일관성

- **reports는 라우터 단위 `_Route`가 자체 변환**한다(D12). 앱 전역 핸들러는 **reports 외 라우트와 라우팅 단계 오류용**이며 충돌하지 않는다(라우터가 먼저 처리해 도달하지 않음; 같은 문구 체계라 만에 하나 도달해도 동일 형식).
- 앱 전역 핸들러 3개(고정 문구만, 입력값·detail·스택·경로 비반사):

| 트리거 | HTTP | code | message |
|---|---|---|---|
| `RequestValidationError` | 422 | `INVALID_REQUEST` | "요청 값이 올바르지 않습니다: {필드명, …}" (필드명=`loc` 마지막 요소만, 값 에코 금지) |
| `StarletteHTTPException` 404 / 405 / 그 외 | 404 / 405 / 해당 코드 | `NOT_FOUND` / `METHOD_NOT_ALLOWED` / `HTTP_ERROR` | 고정 한국어 문구 |
| `Exception` | 500 | `INTERNAL_ERROR` | "요청을 처리하지 못했습니다."(D12와 동일) — 원문은 `logger.exception`(마스킹 필터)으로만 |

- D13 라우트가 직접 내는 코드: `SESSION_BUSY`(409, §5), `SESSION_NOT_FOUND`(404, §6). 응답 형식은 항상 `{"code","message"}` 두 키뿐(`JSONResponse`).
- **도메인 실패는 HTTP 오류가 아니다**: 키 누락·LLM 실패·후보 0 소진·캐시 미구축 등은 `/api/chat`이 **200 + `status="error"` + 사용자 문구(answer)** 로 돌려준다(D9 `final(error)`). HTTP 4xx/5xx는 요청 형식·세션 충돌·서버 버그에만 쓴다.
- 오류 코드 전체 계약: reports 4종(`REPORT_NOT_FOUND`·`DRAFT_NOT_FOUND`·`INVALID_REQUEST`·`INTERNAL_ERROR`) + D13 `SESSION_BUSY`·`SESSION_NOT_FOUND`·`NOT_FOUND`·`METHOD_NOT_ALLOWED`·`HTTP_ERROR`.

## 4. 입력 검증 — `session_id`

- 형식: **UUID 문자열**(`^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$`), 서버가 **소문자로 정규화**해 키로 사용(대소문자 변형이 별 세션이 되지 않게). `/api/chat` 본문과 `/api/logs/{session_id}` 경로 양쪽에 같은 검증(공유 상수/`Annotated` 타입 1개).
- 의도: (a) 타 세션 ID **추측 불가**(UUIDv4 122bit) → 남의 대화 맥락·로그·draft 접근 방지, (b) 임의 문자열 폭주로 D10 `MAX_SESSIONS` LRU를 밀어내는 의도치 않은 충돌 완화. 클라이언트(D14)는 `crypto.randomUUID()`로 생성해 `localStorage`에 보관(탭 새로고침 후에도 대화 이어감, 새 대화 = 새 UUID).
- **한계(수용)**: 서버가 세션을 발급·인증하지 않으므로 UUID를 새로 만들어 대량 호출하는 클라이언트는 LRU를 밀어낼 수 있다. 인증·레이트리밋은 DESIGN 범위 밖(단일 사용자 로컬 도구, 127.0.0.1 바인딩 §8).
- D12 `POST /api/reports`의 `session_id`(1~64자)는 그대로 둔다. UUID가 아니면 어차피 세션 draft가 없어 404 `DRAFT_NOT_FOUND`이므로 정합에 문제 없음(D12 변경 요청 없음).
- `message`: `str`, strip 후 1~2000자(상한은 임의 상수 §12). 빈 문자열·공백만 → 422. 추가 필드는 `extra="forbid"`로 422.

## 5. `POST /api/chat`

### 5-1. 요청·응답 (`schemas.py`, DESIGN §9 기준 + 필드 형태 확정)

```
ChatRequest  = {session_id: UUID str, message: str(1~2000)}

ChatResponse = {
  answer: str,                  # 본문 + 면책(D9, 코드가 부착). 오류 폴백이면 고정 문구 + 면책
  status: "answered"|"asked"|"limit"|"error",
  verdicts: [ {stock_code, stock_name, status:"PASS"|"FAIL"|"UNDECIDABLE", reasons:[ReasonCode], failed:[Metric]} ],
  industry_avg: {"PER": {"value": float|null, "n": int}} | {},
  steps:      [ {seq, stage, round, started_at, ended_at|null, intent|null, reason|null} ],
  tool_calls: [ {type:"call", seq, round, tool, input, cache_hit}
              | {type:"result", seq, round, tool, ok, error_code|null, duration_ms, summary} ],
  replans:    [ {count, trigger, change} ],
  disclaimer: str,              # respond.DISCLAIMER 같은 상수(D0 §4)
  # DESIGN §9에 없는 추가 필드(§11 요청) — 모두 비어도 항상 존재
  missing_api_key: bool,        # errors에 MISSING_API_KEY가 있으면 true (D14 키 누락 안내용, D9 §13-4)
  saveable: [ {stock_code, stock_name} ]   # 현재 세션 report_draft 보유 종목 = 저장 버튼 대상
}
```

- `verdicts[]`: state `verdicts`(코드→Verdict)를 후보 순서로 평탄화. `stock_name`은 final state `candidates`의 `name`, 없으면 코드. 지표 값은 싣지 않는다(표·근거는 `answer`에 코드가 이미 렌더, D9). 값이 필요하면 D14는 저장된 리포트(D12 `Report.metrics`)를 쓴다.
- `industry_avg`: state 값 그대로(없으면 `{}`).
- `replans[]`: **final state `replan_log`가 기준**(D9 §9-4, D11 §2). 이벤트로 재구성하지 않는다.
- `saveable`: final state `report_draft`(맵)의 값들에서 `{stock_code, stock_name}`만 뽑음(DESIGN §9 "서버가 draft 보관" 유지 — 지표는 내려보내지 않는다).
- `steps[]`와 `tool_calls[]`는 **`collect(sid)`가 모은 이벤트에서만** 만든다(§5-3).

### 5-2. 처리 흐름

```
async def chat(body):
    sid = body.session_id.lower()
    lock = _locks.get(sid) or _locks.setdefault(sid, asyncio.Lock())       # WeakValueDictionary
    if lock.locked(): 409 SESSION_BUSY("이전 요청을 처리 중입니다.")           # await 없이 검사→획득: 원자적
    async with lock:
        with stream.collect(sid) as evs:
            final_state, failed = {}, False
            try:
                final_state = await memory.run_turn(sid, body.message)
            except Exception:
                failed = True; logger.exception("chat turn failed")        # 마스킹 필터 경유, 본문·user_msg 미기록
            finally:                                                       # 취소(CancelledError)에도 final 보장
                if not any(e.type == "final" for e in evs):
                    status = "error" if failed or not final_state else (final_state.get("status") or "error")
                    publish(sid, "final", Final(status=status))            # D9 §11·D11 §4
        return build_response(final_state, evs, failed)
```

- **같은 세션 동시 요청 정책 = 거부(409), 대기열 없음.** 이유: D11은 같은 세션 동시 요청 시 `collect` 혼입을 지원하지 않고, D10은 "마지막 쓰기가 이김". 대기(직렬화 큐)는 LLM 장시간 호출이 쌓여 사용자가 중복 클릭 시 같은 질문이 두 턴으로 처리될 수 있다. 검사→획득 사이에 `await`가 없어 단일 이벤트 루프에서 원자적. 락 레지스트리는 `WeakValueDictionary`(요청이 끝나 참조가 없으면 자동 정리, 별도 청소 코드 없음). **다른 세션끼리는 병렬**(락 공유 없음).
- **`final` 보장**: 정상 경로는 D9 respond가 이미 발행하므로 `collect` 안에 `final`이 있어 D13은 아무것도 발행하지 않는다(중복 방지). 예외·취소·respond 이전 중단일 때만 `final`을 발행하며, 이 발행도 `collect` 안에서 일어나므로 `evs`·`run_logs`·스트림에 동일하게 반영된다. 이미 `final`이 있으면 절대 발행하지 않는다(단일 `final` 불변식).
- 예외 시 응답: **200**, `status="error"`, `answer="요청을 처리하지 못했습니다.\n\n" + DISCLAIMER`, `verdicts=[]`, `industry_avg={}`, `replans=[]`, `saveable=[]`, `missing_api_key=false`, `steps/tool_calls`는 그때까지 수집된 이벤트(부분 이력도 사용자에게 유용). `run_turn`은 예외 시 메모리를 갱신하지 않는다(D10) — 맥락 유지.
- 핸들러는 `async def`(그래프가 비동기). 이벤트 루프를 막는 동기 호출을 라우트에 두지 않는다(저장 쓰기는 D11 확인 항목).
- 응답 크기: `tool_calls` 이벤트는 요약만(D8: 원본 아님)이라 별도 절단 없음. `input`에 키가 없음은 D11 전제(방어로 응답 직렬화 전 `mask()`는 적용하지 않음 — 키가 이벤트에 들어가는 것이 이미 버그이며 로그 마스킹이 방어선, §12 확인).

### 5-3. 이벤트 → `steps[]` / `tool_calls[]` 변환 규칙 (D14가 의존)

공통: `round` = 해당 이벤트 이전까지 본 `replan` 이벤트 수(0=최초 실행). `replans[r-1]`이 round `r`로 넘어간 이유다(D14가 "재계획 구분선" 표시에 사용). `replan`/`llm_decision`/`final` 이벤트는 배열로 노출하지 않는다(`replans`는 state, 나머지는 로그 상세 `/api/logs`에서 확인).

- `steps[]`: 노드는 순차 실행이라 `stage_start`→`stage_end`를 **같은 stage 쌍으로 매칭**한다(`stage_start`가 항목 생성, 뒤따르는 같은 stage의 `stage_end`가 `ended_at`·`intent`·`reason` 채움). 재계획 시 같은 stage가 다른 `round`로 다시 나타난다. `stage_end`가 없으면 `ended_at=null`(예외 중단). `seq`=`stage_start`의 seq, `started_at/ended_at`=이벤트 `ts`. 건너뛴 stage는 이벤트가 없으므로 항목도 없음(D0 §3).
- `tool_calls[]`: **`tool_call`/`tool_result` 이벤트를 발행 순서 그대로 평탄화**한다(`type:"call"|"result"`). **호출-결과 쌍 매칭을 하지 않는다** — D8이 `get_financials`를 `asyncio.gather`로 동시 실행해 두 이벤트가 섞이고 payload에 호출 식별자가 없어 FIFO 매칭이 오귀속될 수 있다. 화면은 이벤트 줄 단위 타임라인으로 충분(`call`: 도구·입력·캐시 적중, `result`: 성공 여부·건수/핵심값 요약·소요).
- 위 변환은 순수 함수 `to_steps(evs)`, `to_tool_calls(evs)`로 두어 이벤트 리스트만으로 단위 테스트한다(§13).

## 6. `/api/logs`

모두 동기 `def` 라우트(D12와 같은 이유: 동기 sqlite로 루프 차단 방지, FastAPI 스레드풀). `logger_agent` 함수만 호출.

| 라우트 | 응답 | 비고 |
|---|---|---|
| `GET /api/logs` | 200 `[{session_id, started_at, event_count}]` | `list_sessions()` 그대로(started_at 내림차순) |
| `GET /api/logs/{session_id}` | 200 `[LogEvent]` | `get_session()`(id 순). 빈 목록이면 404 `SESSION_NOT_FOUND`("로그가 없습니다.") |
| `DELETE /api/logs/{session_id}` | 204 | **멱등**: 삭제 행 0이어도 204(D12가 이 대비를 전제로 reports DELETE를 404로 둠). 대화 메모리는 **지우지 않는다**(D10 §5: 로그 삭제와 대화 맥락은 별개) |

- `LogEvent` = D0 `Event` 5필드(`session_id, seq, ts, type, payload`) + **`run: int`**(1부터, §6-1). DESIGN §9 `[Event]`를 깨지 않는 추가 필드(§11 요청). 정렬은 `get_session`의 id 순을 그대로 유지한다 — DESIGN §9 "seq 순"과 서버 재시작 후 seq 리셋 시 어긋나므로 **id 순이 정답**(저장 도착 순; §11 요청).
- `session_id` 경로는 §4와 같은 UUID 검증(+소문자 정규화). 형식 오류 → 422 `INVALID_REQUEST`.
- 응답 `payload`는 저장본(마스킹 후) 그대로 파싱해 돌려준다. 수정 엔드포인트 없음(F3014), 라우트 메서드는 GET/DELETE뿐(PUT/PATCH/POST는 405).

### 6-1. run 분할 (D0 §3 위임 이행)

순회하며 `run`을 부여한다. **새 run 시작 조건**: (a) 첫 이벤트, (b) 직전 이벤트가 `final`(그 뒤 이벤트), (c) `stage_start(stage=analyze)`가 현재 run에 이미 이벤트가 있는 상태에서 나타남(`final` 없이 끊긴 비정상 run 방어). 그 외는 현재 run에 속한다. 결과: 정상 run = `stage_start(analyze)`~`final`, analyze 전에 실패해 D13이 합성한 `final(error)` 단독 run도 1 run, chitchat/asked(plan 이후 미실행)도 정상 분할. 분할은 `run` 필드 부여만 하며 이벤트를 묶거나 변형하지 않는다(D14 등은 `run` 값으로 그룹핑).

## 7. 정적 파일

- 디렉터리 `frontend/`(DESIGN §1: `index.html`, `reports.html`, `*.js`) 를 `/`에 마운트(`html=True` → `/`는 `index.html`, `/reports.html`은 그대로). 라우터를 **먼저** 등록하고 마운트는 **마지막**이라 `/api/*`가 가린다. `/api/...` 미정의 경로는 §3 핸들러로 JSON 404.
- D14/D15는 **같은 오리진의 상대경로**(`/api/chat` 등)로 호출한다. 별도 포트·CORS 불필요(§8).
- `frontend/` 디렉터리가 없으면(D14 이전·테스트) 마운트를 생략하고 앱은 기동한다.
- 예외: `/docs`는 D23-A로 로컬 swagger-ui-dist(`frontend/vendor/swagger/`)를 서빙한다(CSP 불변, `tests/test_d23_docs.py`). `/redoc`은 제공하지 않는다.
- `StaticFiles`는 루트 밖 경로(`..`)를 거부한다. `frontend/`에는 비공개 파일(.env 등)을 두지 않는다 — `.env`는 repo 루트(frontend 밖)에 있음.

## 8. 보안

- **CORS: 미설정(CORSMiddleware 없음).** 프론트가 같은 오리진에서 서빙되므로 불필요하고, 타 오리진 브라우저 스크립트의 응답 읽기를 기본 차단한다. 별도 개발 서버가 필요해지면 그때 명시 오리진을 추가(와일드카드 금지).
- **바인딩**: 기본 실행은 `127.0.0.1`. 외부 노출·인증은 DESIGN 범위 밖(단일 사용자 로컬 도구). README에 "외부 공개 금지" 명시(구현 단계).
- **응답 헤더(미들웨어 1개)**: `X-Content-Type-Options: nosniff`, `Content-Security-Policy: default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; frame-ancestors 'none'`. → **D14/D15는 인라인 `<script>`·`on*=` 핸들러를 쓰지 않고 외부 `*.js`만 사용**(§9 계약). 정책 위반 시 D14/D15 설계가 우선 조정 대상이다(`'unsafe-inline'` 스크립트 허용은 하지 않는다).
- **XSS 책임**: 서버는 모든 문자열을 변형 없이 JSON으로 반환(D12 §8과 동일). **렌더 책임은 D14/D15** — `answer`, `steps[].reason`, `tool_calls[].summary/input`, `verdicts[].stock_name`, 로그 `payload` 등 모든 서버·외부 유래 문자열을 `textContent`/텍스트 노드로만 삽입, `innerHTML`·`insertAdjacentHTML` 금지, URL은 `http(s)://`만 링크화. `answer`는 개행·표 정렬이 있는 평문이므로 `white-space: pre-wrap`으로 표시(마크다운/HTML 해석 금지).
- **키 비노출**: 응답·로그·오류 어디에도 API 키·스택·내부 경로를 쓰지 않는다(NF3002). 오류 핸들러는 입력·예외 문자열을 반사하지 않는다(§3). `missing_api_key`는 불리언만 노출(어느 키인지·값은 없음).
- **입력 크기**: `message` 2000자 상한(§4), JSON 본문 크기는 서버 기본값(uvicorn). 별도 레이트리밋 없음(§4 한계).
- 인증·CSRF: 범위 밖(D12 §8과 동일).

## 9. D14/D15가 쓰는 인터페이스 요약

| 사용처 | 계약 |
|---|---|
| 세션 | 페이지 첫 로드 시 `crypto.randomUUID()` → `localStorage` 저장·재사용. 새 대화 = 새 UUID. 같은 세션에 **요청을 동시에 보내지 않는다**(전송 중 입력 잠금); 409 `SESSION_BUSY`는 "처리 중" 안내 후 무시 |
| D14 전송 | `POST /api/chat {session_id, message}` → §5-1 응답. HTTP 오류(4xx/5xx)는 `{code,message}`의 `message`를 표시. **200이어도 `status=="error"`이면 `answer`를 오류 문구로 표시** |
| D14 표시 | `answer`를 평문(`pre-wrap`)으로. `status`별 스타일(asked=되묻기, limit=완화 제안, error). `missing_api_key==true`면 "키 누락 안내" 배너("`.env`를 확인하세요") |
| D14 타임라인 | `steps[]`로 단계 행(`stage`·시작~종료·analyze의 `intent/reason`), `tool_calls[]`로 이벤트 행(call/result). `round`가 바뀌는 지점마다 `replans[round-1]`의 `{trigger, change}`로 재계획 구분선(예: `market_expand`→"시장 확대"). 표시 문구 매핑은 D14 |
| D14 저장 | `saveable[]` 각 항목에 저장 버튼 → `POST /api/reports {session_id, stock_code, name?, memo?}`(D12 §9: 201, `duplicate_of` 안내, 404 `DRAFT_NOT_FOUND` 안내) |
| D14/D15 면책 | 화면 하단/각 응답에 `disclaimer` 표시(D15는 리포트의 `disclaimer`) |
| 로그 열람(선택 UI) | `GET /api/logs`, `GET /api/logs/{sid}`(`run`으로 그룹핑), `DELETE /api/logs/{sid}`(204). DESIGN §9 화면 정의에 로그 화면이 없으므로 **UI 제공 여부는 D14 결정**(API는 제공) |
| 공통 | 같은 오리진 상대경로, 인라인 스크립트·`on*=` 금지(CSP), 문자열은 텍스트로만 렌더 |

## 10. SSE 엔드포인트 (D11 요청 2 답변)

- **기본 설계는 추가하지 않는다.** DESIGN §9에 SSE가 없고 `/api/chat` 응답의 `steps[]/tool_calls[]/replans[]`가 타임라인 요구(§9 화면)를 충족한다. `stream.open_stream/sse_format`은 D11에 남기되 D13이 사용하지 않는다(구현 완료·테스트됨, 비용 없음).
- 상위 계약 요청 #4(선택): 실시간 진행 표시가 필요해지면 `GET /api/chat/stream/{session_id}`(`text/event-stream`, `with stream.open_stream(sid) as s: async for ev in s: yield sse_format(ev)`)를 추가한다. 세션 ID를 클라이언트가 생성하므로 **POST 전에 스트림을 먼저 열 수 있어** 서버 상태 변경 없이 가능하다. 승인 시 D13을 개정한다(미승인이 기본).

## 11. 상위 계약 요청 (직접 수정 안 함, 누적 보류 제안 정리 — 승인 대기)

**A. DESIGN §1 파일 트리**
1. `db/sqlite.py` 주석: "reports (run_logs는 events/logger_agent.py가 DDL 소유)" (D12 §10-1).
2. `events/`에 `stream.py`(세션별 팬아웃: collect/open_stream/sse_format) 추가 (D11 §7-1).
3. `identity/` 패키지(`models/store/dart/resolve/build.py`, `data/industry_terms.json`) 추가, 의존성 `defusedxml`·`uvicorn`, README에 "앱 실행 전 `python -m backend.identity.build` 1회" 단계 (D2 §수정요청 4).
4. `config.py` 주석에 `DB_PATH`·`TOOL_TIMEOUT_S`는 코드 상수 (D0 수정 제안 #13 승인 시 함께).

**B. DESIGN §9 API 명세**
5. `POST /api/reports` 성공 **201**, 오류 `code` 4종(`REPORT_NOT_FOUND`·`DRAFT_NOT_FOUND`·`INVALID_REQUEST`·`INTERNAL_ERROR`) (D12 §10-2). 아울러 D13 코드 `SESSION_BUSY`(409)·`SESSION_NOT_FOUND`(404)·`NOT_FOUND`·`METHOD_NOT_ALLOWED`·`HTTP_ERROR` 추가.
6. `POST /api/chat`: `session_id`는 **UUID 형식**(서버가 소문자 정규화), `message` 1~2000자, **같은 세션 동시 요청 시 409 `SESSION_BUSY`**. 응답의 `verdicts[]`·`steps[]`·`tool_calls[]`·`replans[]` 항목 형태를 §5-1로 확정, **추가 필드 `missing_api_key: bool`, `saveable: [{stock_code, stock_name}]`**(DESIGN §9 "키 누락 안내", "저장 버튼" 화면 요구의 구현 수단).
7. `GET /api/logs/{session_id}`: 이벤트에 `run`(정수) 필드 추가, 정렬은 "seq 순"이 아니라 **저장 순(id 순)**(재시작 시 seq 리셋 보완, D0 §3), 없는 세션은 404, `DELETE`는 멱등 204.
8. (선택·미채택 기본) SSE `GET /api/chat/stream/{session_id}` (§10).

**C. D0 / DESIGN §7 보강(D10 제안 재게시, 낮은 위험)**
9. D0 §6-5: "analyze 실패(`intent` 미설정)로 끝난 요청은 메모리를 갱신하지 않는다". D0 §4: "초기 state에 `conditions`는 항상 존재". DESIGN §7: "세션 수 상한(LRU)·TTL 없음, `memory.run_turn`이 요청 단위 state 생성·갱신 소유" (D10 §9).
10. D0 §3 "run 경계 D13 파생"은 **§6-1 규칙(첫 이벤트 / final 직후 / analyze 재시작)으로 확정**됐음을 주석(D0 변경 아님, 확인용).

D0 계약(이벤트·AgentState·ToolResult) 자체의 수정 요청은 없다. 모두 불승인이어도 구현은 이 설계를 따르며, 승인 시 문서 동기화만 한다.

## 12. 구현 시 확인 항목 (FAIL 아님)

- 임의 상수: `message` 2000자, CSP 문자열, `HTTP_ERROR` 문구. 조정 가능.
- `collect` 내부 `final` 합성 발행이 `CancelledError` 중에도 동작하는지(Starlette가 클라이언트 연결 종료 시 핸들러를 취소하는지는 버전 의존) — 실호출로 확인. 취소되면 `finally`의 `publish`는 동기라 완료됨.
- `run_agent`가 호출마다 `build_graph()`를 컴파일한다(D9 코드). 요청당 컴파일 비용이 크면 구현에서 모듈 캐시(계약 불변).
- `logger_agent.record`의 동기 SQLite 쓰기가 이벤트 루프를 막는 정도, 여러 세션 병렬 시 `database is locked`(D11 §8, D12 §11, identity와 같은 파일) — busy timeout 상수 공통화 여부.
- `tool_call.input`에 키 문자열이 섞이는 경로가 없는지(응답 노출 방어, §5-2). 있으면 D8 버그.
- `WeakValueDictionary` 락이 `async with` 중 GC되지 않는지(지역 변수 `lock` 참조가 유지) 확인.
- `DELETE /api/logs/{sid}`가 진행 중인 세션에 들어오면 이후 이벤트만 남는 것을 허용(D11 §8과 동일, 별도 차단 없음).
- 정적 마운트 `html=True`에서 `/reports` (확장자 없음) 요청이 404가 되는 점 — 링크는 `reports.html`로 고정(D14/D15).
- `app.add_exception_handler(Exception, …)`가 Starlette `ServerErrorMiddleware`에서 응답을 만든 뒤에도 예외를 재전파해 서버 로그가 이중 기록되는지, 마스킹 필터를 거치는지 확인.
- `StaticFiles`가 StarletteHTTPException을 발생시켜 §3 핸들러가 JSON 404로 바꾸는지 실제 확인(정적 404도 JSON이 되는 점이 의도와 맞는지).
- 서버 재시작 직후 같은 `session_id`로 접속하면 메모리는 비고 로그는 남음 — 정상(DESIGN §7).

## 13. 테스트 관점 (`TestClient`, 임시 `DB_PATH` monkeypatch, `memory.run_turn`/`run_agent` mock)

- **기동**: `with TestClient(app)`에서 install 순서(mask→load_all→init_table→logger→stream)가 호출 순서대로임(monkeypatch 기록), 2회 기동해도 이벤트가 1회만 저장·수집, 키·캐시 없이도 기동 성공, `frontend/` 없을 때 기동 성공, `/api/reports`가 마운트되어 D12 응답 형식 유지.
- **chat 정상**: mock `run_turn`이 이벤트를 발행(`stage_start(analyze)`…`final`)하고 state 반환 → 응답 필드 전부 존재·타입, `verdicts` 평탄화·`stock_name` 폴백, `replans`가 `replan_log` 기준, `disclaimer==respond.DISCLAIMER`, `saveable` 추출, `missing_api_key`(errors에 코드 있을 때만 true), `final`이 mock에 의해 이미 발행됐을 때 D13이 추가 발행하지 않음(`run_logs`에 `final` 정확히 1건).
- **chat 예외**: `run_turn`이 예외 → 200·`status="error"`·고정 문구+면책·`final(error)` 1건(수집·`run_logs` 양쪽)·응답에 예외 문자열 미포함, 메모리 미갱신(mock 확인). `final` 없이 정상 반환하는 mock(방어 경로) → `final(state.status 또는 error)` 1건.
- **동시성**: 같은 sid 요청 2개 동시(첫 번째를 `Event`로 지연) → 두 번째 409 `SESSION_BUSY`·`{code,message}`만; 다른 sid 두 요청은 둘 다 200; 첫 요청 종료 후 같은 sid 재요청 200(락 해제); 예외 후에도 락 해제.
- **검증**: session_id 비-UUID/빈 값/대문자(정규화되어 같은 세션)/누락, message 빈·공백·2001자·여분 필드 → 422 `INVALID_REQUEST`, 응답에 입력값 미반사.
- **변환 함수**(이벤트 리스트만으로): `steps` 매칭(재계획 시 같은 stage 다른 round, `stage_end` 누락 시 `ended_at=null`, 건너뛴 stage 항목 없음, analyze의 intent/reason), `tool_calls` 발행 순서 평탄화·`round` 부여·call/result 섞임(동시 호출 시나리오)에서도 항목 손실 없음.
- **logs**: 목록 정렬·집계, 상세 `run` 분할(정상 1 run / 2턴 → run 1·2 / chitchat·asked / `final` 없이 analyze 재시작 / 합성 `final(error)` 단독 / seq 리셋 후 id 순 유지), 없는 sid 404 `SESSION_NOT_FOUND`·형식 오류 422, DELETE 204 멱등(0행도 204)·타 세션 불변·대화 메모리 불변(`clear_session` 미호출), 수정 메서드(PUT/PATCH/POST) 405 `METHOD_NOT_ALLOWED`.
- **오류·보안**: 미정의 `/api/x` → JSON 404 `{code,message}`, 핸들러가 내는 모든 응답이 두 키뿐, 내부 예외(라우트 mock) → 500 `INTERNAL_ERROR` 고정 문구·스택/경로/키 미포함, reports 오류는 D12 형식 그대로(앱 핸들러와 충돌 없음), 응답 헤더에 `nosniff`·CSP, CORS 헤더(`access-control-allow-origin`) 부재, `frontend/` 밖 경로 요청(`/../.env`, 인코딩 변형) → 404.
- **정적**: 임시 `frontend/index.html`·`reports.html`·`app.js` 생성 후 `/`·`/reports.html`·`/app.js` 200, `/api` 라우트가 정적보다 우선.
- **통합**: mock Tool/LLM으로 대표 요청 1~5를 `/api/chat`로 순서대로 호출(같은 sid) → 각 응답 `status`(answered/limit·answered/…/answered(chitchat)/asked), 요청 2의 `replans` 최대 길이·`steps.round` 증가, `/api/logs/{sid}`가 요청 수만큼 run을 가지며 각 run에 `final` 1회, `/api/reports` POST가 `saveable` 종목으로 201.
