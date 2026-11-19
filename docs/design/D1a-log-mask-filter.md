# D1a 로그 마스킹 필터 결함 수정 설계

상위: `docs/DESIGN.md`, `docs/design/D1-tool-base.md` §NF3002(`mask`, `install_log_mask`). 구현 범위는 `backend/tools/base.py`의 `_MaskFilter` 수정 + `tests/test_d1.py`(또는 신규 테스트 파일) 추가로 한정한다. `install_log_mask()`·`mask()` 시그니처와 `main.py`·`logger_agent.py`는 변경하지 않는다.

## 1. 원인

현 필터(`base.py` `_MaskFilter.filter`):

```python
record.msg, record.args = mask(record.getMessage()), None
```

- `getMessage()`로 `msg % args`를 미리 계산해 마스킹하고 `args=None`으로 만든다. 키 누출 차단 목적은 맞지만 `record.args` 구조를 파괴한다.
- uvicorn `AccessFormatter.formatMessage`(`uvicorn/logging.py` 약 97~117행)는 `message`(`%(message)s`)를 쓰지 않고 `recordcopy.args`를 `(client_addr, method, full_path, http_version, status_code)`로 **언패킹**해 `request_line`을 만든다. `args`가 `None`이면 `TypeError: cannot unpack non-iterable NoneType object`가 나고 logging이 `--- Logging error ---`를 출력하며 해당 접근 로그 줄이 사라진다.
- 필터는 핸들러 레벨에서 포맷터보다 먼저 실행되므로, 모든 핸들러(uvicorn.access 포함)에 걸린 필터가 매 요청마다 이 충돌을 일으킨다. 키 노출은 없다(로깅 결함).
- 일반 포맷터(`%(message)s`)는 `record.getMessage()`(= `msg % args`, args 비어있으면 `msg` 그대로)만 쓰므로 `args=None`이어도 동작했다. 그래서 uvicorn 전용 포맷터에서만 드러났다.

## 2. 수정 방침 (비교 후 확정)

| 안 | 내용 | 평가 |
|---|---|---|
| A | `getMessage()` 결과를 마스킹해 `msg`에 넣고 `args=()` | uvicorn 5-튜플 언패킹이 여전히 깨짐(`ValueError: not enough values`). 불채택 |
| B | `args`는 건드리지 않고 `msg`만 마스킹 | 키가 `%s` 인자로 들어오면 누출. 불채택 |
| **C (확정)** | `msg`와 `args`의 **각 항목을 개별 마스킹**하되 args의 컨테이너 타입·길이·순서를 보존 | 포맷터가 요구하는 구조 유지 + 모든 경로 마스킹 |

### 확정안 C의 동작

`filter(record)`:

1. `record.msg`가 `str`이면 `mask(msg)`로 치환(포맷 문자열 자체에 키가 있는 경우).
2. `record.args` 처리(타입 보존):
   - `tuple` → 같은 길이의 `tuple`, 각 항목에 `_m(x)` 적용. uvicorn 접근 로그 5-튜플은 5-튜플로 남고 `full_path`(query에 `serviceKey=<키>`가 섞인 경우 포함)·`client_addr` 등 str 항목이 마스킹된다.
   - `dict`(`logger.info("%(k)s", {"k": v})` 형태, LogRecord가 단일 dict 인자를 args로 언팩) → 같은 키의 `dict`, 값에 `_m` 적용.
   - 그 외(`None`·빈 값) → 그대로.
3. 항목 헬퍼 `_m(x)`:
   - `str` → `mask(x)`.
   - `int/float/bool/None` → 그대로(키가 될 수 없음, `%d` 포맷 호환 유지).
   - 그 외 객체(예외, Path, 임의 객체) → `str(x)` 또는 `repr(x)`에 `mask`를 적용했을 때 변하면(= 키 포함) `mask(str(x))` 문자열로 **대체**, 변하지 않으면 원본 객체 유지. (`%s`/`%r` 어느 쪽으로 찍혀도 누출 없음. 키를 담은 비문자열 객체가 `%d` 등으로 쓰이는 경우는 비현실적이라 무시.)
4. 최종 안전망: `record.getMessage()`를 시도해 `mask(결과) != 결과`이면(마스킹된 조각들이 합쳐져 키를 다시 만든 극히 드문 경우 등) 그때만 구 방식 `record.msg, record.args = mask(결과), ()`로 폴백한다. `getMessage()`가 예외를 던지면(잘못된 포맷) 폴백 없이 통과시킨다(logging이 기존대로 처리). 정상 경로에서는 args가 보존된다.
5. exc_info/exc_text/stack_info/`__dict__`의 str 값(extra) 처리는 **기존 코드 유지**:
   - `exc_info`가 있으면 `logging.Formatter().formatException(...)`를 마스킹해 `exc_text`에 덮어쓰고, `exc_text`만 있으면 마스킹.
   - `record.__dict__`의 str 값(msg·exc_text 제외, `stack_info` 포함)을 마스킹.
   - 단 이 루프는 `args`를 건드리지 않는다(args는 tuple/dict라 str 조건에 안 걸림 — 현행 동작 그대로).
6. 멱등: 같은 레코드가 여러 핸들러의 필터를 통과해도 이미 마스킹된 값에 재적용되므로 결과가 같다.

### 누출 경로 점검

| 경로 | 처리 |
|---|---|
| `msg` 문자열 | 1단계 |
| `%s`/`%r` 튜플 인자 (str, 예외 객체 포함) | 2~3단계 |
| dict args (`%(name)s`) | 2단계 |
| uvicorn 접근 로그 `full_path` (query에 키) | 5-튜플 str 항목 마스킹, 구조 보존 |
| `exc_info` traceback | `exc_text` 덮어쓰기(기존). 포맷터가 `exc_text`를 재사용하므로 uvicorn 포맷터에서도 안전 |
| `stack_info`, extra(str) | `__dict__` 루프(기존) |
| extra(비문자열: dict/list/객체) | **범위 밖**(기존에도 미처리). 구현 시 확인 항목 |

## 3. 상위 계약 변경 여부 / 상위 계약 요청

D1 계약의 **의도**(키 형태 4종 마스킹, 모든 핸들러 적용, 어떤 로그 경로로도 키 비노출)는 변경 없음. 다만 D1 §NF3002 `install_log_mask` 설명(66행)의 문구 "`getMessage()`를 마스킹해 `msg`로 바꾸고 `args=None`"은 본 수정과 모순되므로 **문구 수정을 제안**한다(직접 수정하지 않음, 사용자 승인 후 반영):

> 변경 전: "필터는 `getMessage()`를 마스킹해 `msg`로 바꾸고 `args=None`, …"
> 변경 후: "필터는 `msg`와 `args`의 각 항목을 개별 마스킹하며 `args`의 타입·길이를 보존한다(uvicorn 접근 로그 포맷터가 `args`를 5-튜플로 언팩하므로). …(이하 동일)"

**사용자 승인 완료, D1에 반영됨.**

D0·DESIGN.md 변경 요청은 없음.

## 4. 테스트 (caplog/핸들러 기반, 외부 호출 없음)

기존 `tests/test_d1.py::test_log_mask_idempotent` 등은 그대로 통과해야 한다. 추가:

1. **접근 로그 회귀**: `uvicorn.logging.AccessFormatter('%(client_addr)s - "%(request_line)s" %(status_code)s', use_colors=False)`를 쓰는 핸들러를 임시 로거에 붙이고 `install_log_mask()` 후, `lg.info('%s - "%s %s HTTP/%s" %d', "127.0.0.1:1", "GET", "/p?serviceKey=<KEY>", "1.1", 200)`(args는 5-튜플)를 기록. `h.handleError`를 오버라이드해 호출 시 실패시키거나 `logging.raiseExceptions` + stderr 캡처로 `TypeError`/`--- Logging error ---`가 없고 포맷 결과에 `GET`, `200`, `***`가 있으며 KEY가 없음을 확인. (구현 전에 현 코드로 이 테스트가 실패함을 확인해 회귀 테스트임을 보증.)
2. **args 구조 보존**: 필터 직접 호출(`LogRecord` 생성) 후 `record.args`가 같은 길이 tuple, dict 인자는 dict, 정수 항목(status_code)은 `int` 그대로.
3. **키 4형태(원문, `quote`, `unquote`, `quote(unquote)`) × 위치(msg 포맷 문자열 / `%s` 튜플 인자 / dict args / 비문자열 객체 인자(`ValueError(KEY)`) / `exc_info` traceback)**: `parametrize`로 `getMessage()`·포맷 결과·`exc_text`에 키가 없음을 확인. caplog 사용 시 caplog 핸들러는 설치 시점 이후 추가될 수 있으므로 `install_log_mask()`를 caplog 핸들러 부착 후 호출하거나, 기존 테스트처럼 임시 핸들러를 쓴다.
4. **폴백**: 개별 마스킹으로는 안 잡히고 합성 결과에만 키가 나타나는 경우 최종 출력에 키가 없음(구현 가능한 케이스를 만들기 어려우면 헬퍼 단위로 생략하고 `미확인`으로 기록).
5. 테스트 키는 기존 test_d1의 `KEY` 픽스처 방식을 따른다(.env 미사용).

## 5. 구현 시 확인 항목

- 현 코드에서 테스트 1이 실제로 `TypeError`로 실패하는지 먼저 확인한 뒤 수정.
- 서버 기동(`uvicorn` 실행) 후 접근 로그가 정상 출력되고 `--- Logging error ---`가 없는지 실호출 1회 확인(키가 query에 들어가는 요청으로 `***` 치환까지).
- uvicorn 기본 `use_colors=True`일 때 `status_code`가 `int`로 유지되어 `int(status_code)`가 동작하는지(항목 `int` 보존 전제).
- extra의 비문자열 값(dict/list)이 누출 경로가 될 수 있음: 현행 코드도 미처리이므로 본 단위 범위 밖이며, 실제 사용처(`extra=` 사용)를 grep으로 확인해 없으면 그대로 둔다.
- `msg`에 대한 `mask`가 포맷 문자열 속 `%`와 충돌하는 경우(키 형태가 `%2F` 등 포함)는 키가 `***`로 치환되므로 영향 없음이나, 키 일부가 `%s` 같은 포맷 지정자와 겹치면 `getMessage()`가 예외를 낼 수 있음 → 폴백 단계의 예외 통과 동작 확인.
- 핸들러가 필터 설치 이후 추가되면 `install_log_mask()` 재호출 필요(D1 가정 7, 변경 없음).
