# D11 이벤트 버스 확장 + logger (`backend/events/stream.py`, `backend/events/logger_agent.py`)

기준: DESIGN §6·§8·§9, D0 §3, D1 마스킹(`mask`, `install_log_mask`), D8·D9 §6(발행 지점), D10 §5, 구현 코드 `backend/events/bus.py`·`backend/tools/base.py`. 의사코드이며 구현 아님. DESIGN·D0는 수정하지 않고 요청만 올린다. **`bus.py`와 이벤트 계약(타입·payload·발행 지점·순서)은 변경하지 않는다.**

## 1. 목적·범위

이미 있는 것(`bus.py`, 변경 없음): `Event`·payload 모델, `publish(session_id, type, payload_model) -> Event`(세션별 seq/ts, 구독자 예외 삼킴), `subscribe(fn)`(전역·동기·해제 없음).

| 항목 | D11 소관 | 비소관 |
|---|---|---|
| `stream.py` | 버스 구독자 1개를 설치하고 **세션별 싱크**(턴 수집 리스트, 비동기 스트림)로 팬아웃 | 이벤트 발행(D8·D9·D13), HTTP/SSE 라우트(D13) |
| `logger_agent.py` | 구독 → SQLite `run_logs` 추가 저장(마스킹 후), 조회·삭제 함수 | LLM(import 금지), 로그 API 라우트·run 분할·집계 응답 조립(D13) |
| 마스킹 | 저장 직전 `mask()` 적용 | `mask` 정의·`install_log_mask` 호출(D1·D13) |
| `run_logs` DDL | D11 소유(§3) | `reports` 테이블(D12) |

버스에 unsubscribe가 없으므로(`bus.py` 불변) **전역 구독은 모듈당 1회만 걸고**, 해제가 필요한 것(턴 수집·스트림)은 `stream.py` 내부 딕셔너리로 관리한다.

## 2. `stream.py` — 팬아웃

```python
def install() -> None: ...                       # 멱등. bus.subscribe(_dispatch) 1회
@contextmanager
def collect(session_id: str) -> Iterator[list[Event]]: ...   # with 블록 동안 해당 세션 이벤트가 발행 순서대로 쌓이는 리스트
def open_stream(session_id: str) -> "EventStream": ...
class EventStream:                               # 비동기 이터레이터 + 컨텍스트 매니저
    async def __aiter__(self) -> AsyncIterator[Event]: ...   # final 이벤트를 내보낸 뒤 종료
    def close(self) -> None: ...                 # 멱등, 싱크 제거
def sse_format(ev: Event) -> str: ...            # "id: {seq}\nevent: {type}\ndata: {ev.model_dump_json()}\n\n"
```

동작:
- `_dispatch(ev)`(버스 구독자, 동기): `_sinks[ev.session_id]`의 각 싱크에 전달. 싱크 하나의 예외는 다른 싱크에 영향 없게 개별 try/except(버스가 어차피 삼키지만 첫 예외에서 나머지가 안 불리는 걸 막는다).
- `collect`: 리스트 싱크를 등록·`finally`에서 제거. D13 `/api/chat`이 `steps[]/tool_calls[]/replans[]` 조립에 사용. **`replans[]`는 D9 `replan_log`(state)가 기준이고 collect는 steps/tool_calls용**(D9 §9-4 유지).
- `open_stream`: 호출 시점의 `asyncio.get_running_loop()`를 저장, 큐는 `asyncio.Queue`(무제한). `_dispatch`는 `loop.call_soon_threadsafe(q.put_nowait, ev)`로 넣어 **발행 스레드와 무관하게 안전**. 열기 **이후** 발행분만 받는다(과거 재생 없음 — 과거는 `logger_agent.get_session`). `final` 수신 후 이터레이터 종료·자동 `close()`.
- 같은 세션에 동시 요청 2개는 지원하지 않는다(싱크가 섞임). D13이 세션당 직렬화하거나 감수(§8).
- 구독자가 없어도 `publish`는 무해(D0). 스트림/수집은 버스 계약에 영향 없음.

## 3. `logger_agent.py` — 저장

```python
def install() -> None: ...                       # 멱등. init_table() + bus.subscribe(record) 1회
def init_table() -> None: ...                    # CREATE TABLE IF NOT EXISTS + 인덱스
def record(ev: Event) -> None: ...               # 버스 구독자. 예외는 밖으로 내지 않는다
def list_sessions() -> list[dict]: ...           # [{session_id, started_at, event_count}] started_at 내림차순
def get_session(session_id: str) -> list[Event]: ...   # id 오름차순(재시작 시 seq 리셋 보완). 없으면 []
def delete_session(session_id: str) -> int: ...  # 삭제 행 수. 0이어도 예외 아님(404 판단은 D13)
```

```
run_logs(id INTEGER PRIMARY KEY AUTOINCREMENT, session_id TEXT NOT NULL, seq INTEGER NOT NULL,
         ts TEXT NOT NULL, type TEXT NOT NULL, payload_json TEXT NOT NULL)
INDEX run_logs_session ON run_logs(session_id)
```
(DESIGN §8 스키마 그대로 + 인덱스. UNIQUE(session_id, seq) 두지 않음 — 재시작 후 seq 리셋 때문, D0 §3.)

`record(ev)`:
1. `payload_json = mask(json.dumps(ev.payload, ensure_ascii=False))` — **문자열 단위 마스킹**(구조·키 이름을 몰라도 모든 값 포괄). `mask`는 `backend.tools.base`에서 import(호출 시점 settings 키 사용). ts·type·session_id는 시스템 값이라 마스킹 생략.
2. `INSERT`(추가 전용, UPDATE 경로 없음).
3. 실패(DB 잠김 등)는 `logger.warning("run_logs insert failed: %s", mask(str(e)))`만 남기고 삼킴 — 메인 흐름 무영향(D0 구독자 예외 삼킴과 동일 정책).

DB 접근: 호출마다 `sqlite3.connect(DB_PATH)`(`config.DB_PATH`; `with` + `close`)로 열고 닫는다. 모듈 `threading.Lock` 하나로 쓰기 직렬화. 연결 풀·WAL·배치 없음(구현 단순성 우선). `DB_PATH`는 호출 시 `backend.config`에서 읽어 테스트가 monkeypatch 가능.

보존: **자동 삭제·용량 상한 없음.** 삭제는 `delete_session`(API `DELETE /api/logs/{sid}`)만. 로그 수정 함수는 만들지 않는다(F3014). LLM·`openai` 등 import 금지.

저장 대상은 이벤트 payload뿐이다. `tool_call.input`은 D8이 발행한 raw 입력(키는 포함되지 않음, 방어로 `mask`), `tool_result.summary`는 건수·핵심값(D8). 사용자 메시지 원문·LLM 응답 본문은 이벤트에 없으므로 저장되지 않는다.

## 4. 순서·보장

- 구독 순서: `logger_agent.install()` → `stream.install()` 순으로 호출하면 버스 리스트 순서상 **저장이 팬아웃보다 먼저**(스트림 수신 직후 `get_session`으로 조회해도 해당 이벤트가 있음). D13 시작 코드가 이 순서로 호출(§6).
- 이벤트 순서 = `publish` 호출 순서(버스 락 안에서 seq 부여, 락 밖에서 구독자 호출). 비동기 노드가 단일 이벤트 루프에서 발행하므로 같은 세션의 저장·수집·스트림 순서는 seq와 일치. 스레드 발행이 섞이면 구독자 호출이 seq와 역전될 수 있음 → 조회는 `id` 순서가 아니라 저장 도착 순(= id)을 따르고, 역전은 감수(§8).
- **`final` 1회는 발행자 책임**(D9 respond / D13 `run_agent` 예외 폴백). D11은 중복 `final`을 막지 않고, 스트림은 첫 `final`에서 종료한다. D13은 `collect` 결과에 `final`이 없을 때만 `final(error)`를 발행한다(D9 §11 정합):

```python
with collect(sid) as evs:
    try: final_state = await memory.run_turn(sid, msg)
    except Exception: ...
    if not any(e.type == "final" for e in evs): publish(sid, "final", Final(status="error"))
```

## 5. D13이 쓰는 인터페이스 요약

| 용도 | 호출 |
|---|---|
| 앱 시작 | `install_log_mask()`(D1) → `logger_agent.install()` → `stream.install()` |
| `POST /api/chat` steps/tool_calls | `with stream.collect(sid) as evs:` 후 `evs`를 type별로 변환(D13 소관) |
| 선택적 SSE 라우트 | `with stream.open_stream(sid) as s:` 후 `async for ev in s: yield stream.sse_format(ev)` — **DESIGN §9에 SSE 엔드포인트가 없으므로 라우트 추가 여부는 D13이 결정**(§7 요청) |
| `GET /api/logs` | `logger_agent.list_sessions()` |
| `GET /api/logs/{sid}` | `logger_agent.get_session(sid)` → `[Event]`; run 분할(stage_start(analyze)~final)은 D13 |
| `DELETE /api/logs/{sid}` | `logger_agent.delete_session(sid)` → 204(대화 메모리 삭제 여부는 D10 §5 `clear_session`, D13 결정) |

## 6. 에러 처리 요약

| 상황 | 동작 |
|---|---|
| DB 쓰기 실패 | 마스킹된 경고 로그, 삼킴 |
| `install()` 중복 호출 | 멱등(모듈 플래그), 구독 1회만 |
| 스트림 소비자 중단/미소비 | `close()`/`__exit__`에서 싱크 제거. 큐 무제한이라 미소비 시 `final`까지의 이벤트만 쌓이고 close 후 GC |
| `get_session`의 payload_json 파싱 실패 | 발생 불가(자기가 쓴 JSON). 방어 없이 예외 전파(D13 500 `{code,message}`) |

## 7. 상위 계약 요청 (직접 수정 안 함, 승인 대기)

1. **DESIGN §1 파일 트리**: `events/`에 `stream.py`(세션별 팬아웃: collect/open_stream/sse_format) 추가. `bus.py`는 불변.
2. **DESIGN §9 / D13**: SSE 엔드포인트는 현재 명세에 없다. `/api/chat` 응답의 `steps[]`만으로 충분하면 `open_stream`은 쓰지 않아도 된다(구현은 작아서 두되 라우트 추가는 승인 사안). 요청이 아니라 확인: 라우트를 추가하려면 DESIGN §9 갱신 필요.
3. 그 외 D0·DESIGN 변경 요청 없음.
4. `run_logs_session` 인덱스는 구현 세부이므로 상위 수정 불필요.

## 8. 구현 시 확인 항목 (FAIL 아님)

- 동기 SQLite 쓰기가 이벤트 루프 안(`publish`→`record`)에서 실행된다. 이벤트 수십 건/요청 규모에선 무시 가능한지 실호출로 확인, 문제 시 `asyncio.to_thread` 또는 큐 기반 쓰기로 교체(계약 불변).
- 같은 세션 동시 요청: `collect`/스트림 혼입. D13이 세션별 직렬화할지 결정.
- 스레드 발행(Tool이 `to_thread` 내부에서 publish하는지)이 있으면 `call_soon_threadsafe` 경로·순서 역전 여부 확인. D8은 루프에서 발행하는 것으로 알고 있음(실제 확인).
- 서버 종료 직전 미소비 스트림/쓰기 중단: 감수. 종료 훅에서 별도 flush 없음(쓰기는 동기라 `publish` 반환 시 이미 커밋).
- `payload_json` 크기 상한을 두지 않음(임의 상수 회피). `tool_call.input`이 비정상적으로 큰 경우 있으면 구현 시 확인.
- `mask()` 문자열 단위 적용이 JSON 이스케이프(`/`→`\/` 등 `ensure_ascii=False`에선 `/`는 그대로)로 키를 놓치는 경우 없는지 키 형태 4종으로 확인.
- DELETE 후 같은 세션이 계속 쓰이면 버스 seq는 이어서 증가(리셋 안 함). 로그 목록 `started_at`이 이후 이벤트부터로 바뀌는 점 허용.
- `run_logs` 인덱스·테이블 생성이 D12 DB 초기화와 같은 파일을 쓰므로 `CREATE ... IF NOT EXISTS`로 순서 무관함을 확인.

## 9. 테스트 관점

- `stream`: `collect`가 발행 순서대로 담고 종료 후 싱크 제거(이후 발행이 리스트에 안 쌓임), 세션 격리(다른 sid 이벤트 미수신), 싱크 하나 예외 시 다른 싱크 정상, `open_stream`이 열기 이후 이벤트만 받고 `final`에서 종료·자동 close, 스레드에서 publish해도 수신, `install()` 2회 호출 시 이벤트 1회만 전달, `sse_format` 형식(`id/event/data`, 개행 종료).
- `logger_agent`(임시 DB): 이벤트 발행 → `run_logs` 행(컬럼 매핑·`ensure_ascii=False` 한글 보존), 키 문자열(원문·percent-encode 형태)이 payload에 있어도 저장본에 미포함, 같은 sid 재시작 시 seq 리셋돼도 저장 성공·`get_session` id 순, `list_sessions` 집계·정렬, `delete_session` 반환 행 수·다른 세션 불변, DB 경로 불가 시 `record`가 예외 없이 반환, 수정 함수 부재, 모듈에 LLM 관련 import 없음(소스 검사), `install()` 멱등.
- 통합: 대표 요청 1건을 mock Tool/LLM으로 돌려 `collect` 결과와 `get_session` 결과가 동일(순서·내용), 정확히 `final` 1회.
