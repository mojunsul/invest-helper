# D2b 구축 CLI 호출 간격·연결 실패 연속 시 중단

기준: D2 §6(단계 B·오류 매핑 표·`BuildReport`), D0 §2(ErrorCode). 의사코드이며 구현 아님. **D0·D1·DESIGN.md는 수정하지 않는다.** 새 `ErrorCode`·`IdentityError.code` 값 없음. 변경은 D2 내부(build CLI 전용 경로)에 한정.

## 1. 배경(실사고)

`python -m backend.identity.build` 단계 B가 company.json을 지연 없이 순차 호출(~600건/분). 약 1,570건 성공 후 OpenDART가 사용자 IP를 차단 → 이후 모든 요청이 **연결 수준**에서 실패(httpx `ReadError`/`RemoteProtocolError`, connection reset). `020` 응답이 아니므로 RATE_LIMIT 중단이 걸리지 않았다. 현재 `_get`은 이를 `API_ERROR`로 바꾸고 build는 행을 `failed`로 둔 채 계속 진행 → 남은 수천 행을 전부 두드리며 `failed`로 기록. (휴대폰 망에서는 사이트 접속 가능 = IP 단위 차단으로 판단.)

목표: ① 호출 간격을 두어 차단을 예방, ② 차단이 걸리면 빨리 멈추고 재실행 안내.

## 2. 호출 간격 (build.py)

```python
COMPANY_INTERVAL_S = 0.2   # 임의 상수(구현 시 확인 항목). 최대 ~300건/분
async def build(source, *, refresh=False, force=False, commit_every=COMMIT_EVERY,
                min_interval_s: float = COMPANY_INTERVAL_S) -> BuildReport:
    ...
    for sc, cc in rows:
        if min_interval_s:
            await asyncio.sleep(min_interval_s)   # 매 company.json 호출 전 고정 대기
        ...
```
- 요청 소요 시간은 빼지 않는다(실제 간격 ≥ 0.2s + 응답 시간). 단순성 우선.
- 단계 A(corpCode.xml 1회)는 대상 아님.
- CLI(`main`)는 기본값을 쓴다(옵션 추가 없음). 테스트는 `min_interval_s=0`.

## 3. 연결 오류 구분 (dart.py)

D0 `ErrorCode`·D2 `IdentityError.code` Literal은 그대로 두고, **dart.py 내부 하위 클래스**로만 구분한다.

```python
class DartNetworkError(IdentityError):
    """연결 수준 실패(응답을 못 받음). code는 API_ERROR 그대로 — 외부에는 기존과 동일하게 보인다."""
    def __init__(self):
        super().__init__("API_ERROR")

# HttpDartSource._get except 절, 기존 TimeoutException 다음·Exception 앞에 1개 추가
except httpx.TimeoutException as e:
    raise _fail("TIMEOUT", e) from None              # 기존 그대로
except httpx.TransportError as e:                    # ConnectError/ReadError/RemoteProtocolError 등
    _fail("API_ERROR", e)                            # 로그 형식 기존과 동일(타입명만, mask)
    raise DartNetworkError() from None
except Exception as e:
    raise _fail("API_ERROR", e) from None            # 기존 그대로
```
- `TimeoutException`은 `TransportError`의 하위이므로 **반드시 앞 절에서 먼저** 잡는다(TIMEOUT 코드 유지).
- HTTP ≥400·JSON 파싱·`status` 이상은 기존 `IdentityError("API_ERROR")`(하위 클래스 아님) 그대로.
- `from None` 유지 → 키 비노출 규칙(D2 §6) 그대로. 기존 `test_key_not_leaked_on_exception`(ConnectError → code `API_ERROR`, `__cause__` None)은 수정 없이 통과해야 한다.
- **하류 영향 없음**: `HttpDartSource`/`DartSource`는 build CLI만 사용(grep 확인: `backend/identity/build.py`, `tests/test_d2.py`뿐). D3~D5 Tool은 자체 HTTP를 쓰고 `IdentityError`는 `resolve.py`/`store.py`에서만 받으므로 `DartNetworkError`를 볼 일이 없다. 보더라도 code가 `API_ERROR`라 기존 1:1 변환 그대로.

## 4. 연속 실패 시 중단 (build.py)

```python
NETWORK_ABORT_AFTER = 5    # 임의 상수(구현 시 확인 항목)
streak = 0
for sc, cc in rows:
    ...
    try:
        body = await source.fetch_company(cc); ...           # 기존 ok/none 기록
        streak = 0
    except IdentityError as e:
        if e.code in ("RATE_LIMIT", "MISSING_API_KEY"):
            stopped = e.code; break                          # 기존 그대로
        con.execute("UPDATE company SET detail_status='failed', ...")   # 기존 그대로
        streak = streak + 1 if isinstance(e, DartNetworkError) or e.code == "TIMEOUT" else 0
        if streak >= NETWORK_ABORT_AFTER:
            stopped = "NETWORK"; break
    n += 1; ...
```
- 연속 카운트 대상 = 응답을 못 받은 실패(`DartNetworkError` + `TIMEOUT`). 차단이 connect timeout으로 나타날 수도 있으므로 TIMEOUT 포함. 서버가 무엇이든 응답한 경우(ok/none/비연결 API_ERROR)는 0으로 리셋.
- 연속 구간에서 실패한 행은 **`failed` 그대로 둔다**(되돌리지 않음). 단계 B가 `pending`+`failed`를 다시 순회하므로 재실행 시 자동 재시도 → 별도 처리 불필요. 아직 시도하지 않은 행은 `pending` 유지. `finally` 커밋은 기존 그대로.
- `BuildReport.stopped_by` Literal에 `"NETWORK"` 추가(models.py). D2 CLI 내부 값이며 D0 계약 아님. `complete`는 기존 규칙대로 계산(중단 시 보통 False → exit 1).

### 4-1. 중단 로그 (사용자 안내)
기존 최종 `logger.info("build ok=...")` 직후:
```python
if stopped == "NETWORK":
    logger.error("DART 서버 연결이 연속 %d회 실패해 중단했습니다(IP 차단 또는 네트워크 문제 가능). "
                 "잠시 후 또는 다른 네트워크에서 옵션 없이 다시 실행하면 남은 종목부터 이어서 진행합니다.",
                 NETWORK_ABORT_AFTER)
```
- 단계 A(corpCode.xml)에서 `DartNetworkError`가 나면 기존대로 `main`의 `except IdentityError` → `build aborted code=API_ERROR`로 종료(1회 호출이라 연타 없음). `main`에서 `isinstance(e, DartNetworkError)`면 위와 같은 취지의 한 줄을 추가로 남긴다(선택, 1줄).

## 5. 테스트 (tests/test_d2.py, 실호출·실대기 없음)

- 공통: `run()` 헬퍼가 `min_interval_s=0`을 기본으로 넘기도록 변경(기존 테스트가 0.2s×행 수만큼 느려지지 않게). 기존 단언 변경 없음.
1. **간격 적용**: `monkeypatch.setattr(B.asyncio, "sleep", 기록용 async 함수)` 후 `B.build(Fake(), min_interval_s=0.2)` → 기록된 인자가 company.json 호출 수(7)만큼 모두 0.2. `min_interval_s=0`이면 기록 0건.
2. **연속 연결 실패 중단**: `fetch_company`가 k번째 호출부터 항상 `DartNetworkError()`를 내는 가짜 → `stopped_by == "NETWORK"`, 호출 수 == k-1+`NETWORK_ABORT_AFTER`, `failed == NETWORK_ABORT_AFTER`, 남은 행은 `pending`(`pending_left` 확인), `complete is False`. 이어 정상 `Fake()`로 재실행 → `failed==0`, `complete`.
3. **TIMEOUT도 연속 카운트**: 전부 `IdentityError("TIMEOUT")` → `stopped_by == "NETWORK"`(호출 수 == N).
4. **성공이 끼면 리셋**: 연결 실패 N-1회 → 성공 1회 → 연결 실패 N-1회 → … 패턴(테스트용 행이 부족하면 `monkeypatch.setattr(B, "NETWORK_ABORT_AFTER", 2)`) → `stopped_by is None`, 전 행 시도됨.
5. **비연결 API_ERROR는 계속**: 전부 `IdentityError("API_ERROR")`(하위 클래스 아님) → `stopped_by is None`, 전 행 `failed`(기존 동작 유지).
6. **dart.py 매핑**: `_mock` 핸들러가 `httpx.ReadError`/`httpx.RemoteProtocolError`를 raise → `DartNetworkError`이고 `code=="API_ERROR"`; `ReadTimeout` → `TIMEOUT`이며 `DartNetworkError` 아님; `b"not json"`·HTTP 500 → `IdentityError`이나 `DartNetworkError` 아님.
7. 중단 로그: 2번에서 `caplog`에 "옵션 없이 다시 실행" 포함.

## 6. README §3 (문구만)

명령 블록 아래에 한 줄 추가:
> 구축은 DART 호출 사이에 약 0.2초씩 쉬어 수천 종목에 15분 안팎 걸린다. 연결 실패가 연속되면(IP 차단 등) 자동으로 멈추며, 잠시 후 또는 다른 네트워크에서 옵션 없이 다시 실행하면 실패·미처리 종목만 이어서 처리한다.

## 7. 구현 시 확인 항목

- `COMPANY_INTERVAL_S=0.2`는 임의값. DART 분당/일일 한도는 미확인(D2 A1). 0.2s로 전체 구축이 차단 없이 끝나는지 실호출로 확인, 차단되면 값 상향.
- `NETWORK_ABORT_AFTER=5`는 임의값. 일시적 끊김에 너무 민감하면 상향.
- IP 차단 해제 소요 시간 미확인(사용자 망 복구 시점 관찰). 현재 DB에 쌓인 대량 `failed`는 재실행으로 재시도됨(별도 정리 불필요) — 재실행 후 `failed` 0 근접 확인.
- 차단이 connect timeout/ConnectError 중 무엇으로 나타나는지 실관찰(둘 다 카운트하므로 동작 동일).
- `asyncio.sleep` monkeypatch가 `asyncio.run` 내부에 영향을 주지 않는지(영향 시 build 모듈에 `_sleep = asyncio.sleep` 별칭을 두고 그것을 patch).

## 8. 상위 계약 영향

- **D0 변경 없음**(ErrorCode 불변, `DartNetworkError.code == "API_ERROR"`). **DESIGN.md 변경 없음.** D1 변경 없음.
- D2 보완(문서 수정 제안만): §6 오류 매핑 표 "그 외 연결 오류" 행에 "연속 `NETWORK_ABORT_AFTER`회(TIMEOUT 포함) 시 중단(`stopped_by="NETWORK"`)", `BuildReport.stopped_by`에 `"NETWORK"`, 단계 B에 "호출 간 `COMPANY_INTERVAL_S` 대기" 한 줄.
