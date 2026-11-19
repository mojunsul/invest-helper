# D7b LLM HTTP 오류 진단 로그

기준: D7 §3(LLM 클라이언트 `backend/agent/llm.py`), D7 §9 로깅 규칙, D0 키 비노출(NF3002), D1 `mask()`. 의사코드이며 구현 아님. **D0·D1·DESIGN.md는 수정하지 않는다.** 새 `ErrorCode`·`LlmError` 코드·이벤트 필드 없음. 변경은 `llm.py`의 `httpx.HTTPStatusError` 분기 경고 로그에 진단 필드를 덧붙이는 것뿐.

## 1. 배경

`complete()`는 실패를 `LlmError` 코드로만 매핑하고 로그는 `llm code=%s type=%s status=%s` 한 줄이다. 실사용에서 모든 요청이 HTTP 429 → 화면에는 "외부 API 호출 한도를 초과했습니다"만 보였고, 원인은 GMS 엔드포인트를 수동 호출해 본문을 봐야 알 수 있었다.

관측된 본문 형태:

| 형태 | 예 |
|---|---|
| GMS 래핑 + 중첩 `error.error` (429) | `{"message":"[OpenAI 에러] Request failed with status code 429","statusCode":429,"error":{"error":{"message":"Your organization has reached its configured enforced spend limit. ...","type":"insufficient_quota","param":null,"code":"organization_spend_limit_exceeded"}}}` |
| GMS 자체 오류, `error` 없음 (400) | `{"message":"[GMS 에러] Model claude-3-5-sonnet-latest is not available in Model","statusCode":400}` |
| 표준 OpenAI (평면 `error`) | `{"error":{"message":..,"type":..,"code":..}}` |

같은 429라도 "분당 한도"와 "조직 지출 한도 소진"은 대응이 전혀 다르다. 목표: 본문에서 출처 접두어(`[GMS 에러]`/`[OpenAI 에러]`)와 가장 안쪽 `code`/`type`/`message`를 로그 한 줄로 남긴다.

## 2. 로그 추가 (llm.py)

```python
DIAG_MAX = 200  # 필드당 최대 길이(임의 상수)


def _clip(x) -> str:
    # mask 먼저, 자르기는 나중: 키가 잘린 채 남으면 mask가 못 잡는다.
    return mask(str(x)).replace("\n", " ")[:DIAG_MAX]


def _err_diag(resp: httpx.Response) -> str:
    """오류 응답 본문에서 진단 필드 추출. 절대 raise하지 않는다."""
    try:
        try:
            d = resp.json()
            if not isinstance(d, dict):
                raise ValueError
        except Exception:
            return "raw=" + _clip(resp.text)          # 비JSON/비dict → 잘린 원문
        e = d.get("error")
        if isinstance(e, dict) and isinstance(e.get("error"), dict):
            e = e["error"]                            # GMS 래핑: error.error
        if not isinstance(e, dict):
            e = {}                                    # error 없음(GMS 자체 오류) 또는 문자열 등
        return "msg=%s err_code=%s err_type=%s err_msg=%s" % tuple(
            _clip(v) for v in (d.get("message"), e.get("code"), e.get("type"), e.get("message")))
    except Exception:
        return ""                                     # 진단 실패는 무시(본 동작 영향 없음)

...
    except httpx.HTTPStatusError as e:
        code = "RATE_LIMIT" if e.response.status_code == 429 else "API_ERROR"
        logger.warning("llm code=%s type=%s status=%s %s", code, mask(type(e).__name__),
                       e.response.status_code, _err_diag(e.response))   # 기존 줄에 진단 필드만 추가
```

- **한 줄 유지**: 기존 경고 줄 끝에 필드를 붙인다(별도 줄 없음). 예: `llm code=RATE_LIMIT type=HTTPStatusError status=429 msg=[OpenAI 에러] Request failed with status code 429 err_code=organization_spend_limit_exceeded err_type=insufficient_quota err_msg=Your organization has reached ...`
- **필드**: `msg`=최상위 `message`(GMS 출처 접두어 포함), `err_code`/`err_type`/`err_msg`=가장 안쪽 error 객체 값. 없는 값은 `None`으로 찍힌다(형태 판별에 오히려 유용). 표준 OpenAI 본문은 `msg=None`.
- **비JSON 본문**(HTML 게이트웨이 오류 등): `raw=` + 마스킹·200자 절단 원문. 개행은 공백으로 바꿔 한 줄 보장.
- **never raise**: 파싱 전체를 `try/except Exception`으로 감싸 실패 시 빈 문자열. `HTTPStatusError` 분기의 코드 매핑·`raise LlmError(code) from None`은 그대로.
- **비노출**: 모든 본문 유래 텍스트는 `_clip`(=`mask` 후 절단)을 통과. `install_log_mask()` 필터는 2차 방어일 뿐 의존하지 않는다(테스트의 caplog 핸들러엔 필터가 없으므로 `_clip`만으로 키가 가려져야 한다). 요청 헤더·URL·프롬프트는 로그에 넣지 않는다.
- **D7 §3/§9와의 관계**: D7 §3("본문 금지")·§9("LLM 응답 원문 미기록")에 대한 **예외**다. 성공 응답 completion은 기존대로 미기록이고, HTTP 오류 본문의 진단 필드만 mask 후 필드당 200자로 남긴다. 이에 맞춰 D7 §3·§9 문구를 보완했다(§6).
- **TIMEOUT·기타 분기**: 변경 없음. 타임아웃엔 본문이 없고, 200 응답의 형식 오류(`KeyError` 등)는 원문 미기록 규칙 대상이라 그대로 둔다.
- **불변**: `LlmError` 코드 매핑(429→RATE_LIMIT, 그 외 API_ERROR), 반환값, 사용자 노출 문구(`MSG`), analyze의 재호출 정책(LlmError는 재호출 없음), 이벤트·`ErrorRecord` 모두 변경 없음.

## 3. 서버 터미널 노출 경로 (변경 없음)

`backend/main.py` lifespan이 `logging.basicConfig(level=logging.INFO)` 후 `install_log_mask()`를 호출한다. 경로:

- uvicorn 기본 로깅 설정은 `uvicorn`/`uvicorn.error`/`uvicorn.access` 로거에만 핸들러를 달고 루트에는 달지 않는다 → lifespan의 `basicConfig`가 루트에 stderr `StreamHandler`를 붙인다(루트 핸들러가 없을 때만 동작).
- `backend.agent.llm` 로거는 레벨·핸들러 미설정 → 루트로 전파, 루트 레벨 INFO이므로 WARNING 통과 → uvicorn을 띄운 터미널(stderr)에 출력.
- `install_log_mask()`가 그 루트 핸들러에 `_MaskFilter`를 부착(basicConfig 이후 호출이므로 순서 맞음) → 2차 마스킹 적용. 1차는 §2 `_clip`.
- 출력 예(basicConfig 기본 포맷 `%(levelname)s:%(name)s:%(message)s`):
  `WARNING:backend.agent.llm:llm code=RATE_LIMIT type=HTTPStatusError status=429 msg=[OpenAI 에러] Request failed with status code 429 err_code=organization_spend_limit_exceeded err_type=insufficient_quota err_msg=Your organization has reached its configured enforced spend limit. ...`

**필요한 변경 없음.** `main.py`·로깅 설정은 건드리지 않는다.

## 4. 테스트 (tests/test_d7.py, 실호출 없음)

기존 `mock_http`·`lenv`·`KEY="sekret"` 재사용. 각 테스트는 `caplog.set_level(logging.WARNING)`, `LlmError` 코드가 기존 매핑대로인지와 `KEY not in caplog.text`를 함께 단언.

1. **GMS 래핑 429**: 위 표 첫 본문(안쪽 `message` 끝에 `KEY`를 끼워 넣음) → `RATE_LIMIT`; caplog에 `"[OpenAI 에러]"`, `"err_code=organization_spend_limit_exceeded"`, `"err_type=insufficient_quota"`, `"***"` 포함, `KEY` 미포함.
2. **GMS 자체 400**: 둘째 본문 → `API_ERROR`; caplog에 `"[GMS 에러]"`, `"err_code=None"` 포함.
3. **표준 평면 error 401**: `{"error":{"message":"bad","type":"invalid_request_error","code":"invalid_api_key"}}` → `API_ERROR`; `"err_code=invalid_api_key"`, `"msg=None"` 포함.
4. **비JSON·긴 본문 502**: `"<html>" + "x"*190 + KEY + "y"*500` → `API_ERROR`; caplog에 `"raw=<html>"` 포함, `KEY` 미포함(절단 경계에 걸린 키도 mask가 먼저 처리됨), 해당 레코드 메시지 길이 ≤ 고정부 + 200.
5. **비dict JSON**: 본문 `[1,2]` → 예외 없이 `LlmError(API_ERROR)`, caplog에 `"raw="` 포함.
6. **루트 핸들러 경유 출력**: `io.StringIO`에 쓰는 `StreamHandler`를 루트 로거에 붙이고(루트 레벨 INFO로 임시 설정) `base.install_log_mask()` 호출 → 케이스 1 실행 → 스트림에 `"WARNING"`·`"backend.agent.llm"`·`"err_code=organization_spend_limit_exceeded"` 포함, `KEY` 미포함. `finally`에서 핸들러 제거·레벨 복원(서버 터미널 경로 = 루트 핸들러 + `_MaskFilter`를 재현).
- 기존 `test_complete_http_errors`(본문 `body {KEY}`)는 이제 `raw=body ***`가 찍히며 기존 단언(`KEY not in caplog.text`) 그대로 통과해야 한다. 기존 테스트 단언 변경 없음.

## 5. 구현 시 확인 항목

- GMS가 다른 오류 형태(예: `error`가 문자열, `detail` 키)를 내는지 실호출에서 관찰. 이 경우 `raw=` 대신 `msg=... err_*=None`이 찍혀 정보가 빠질 수 있다 — 관측되면 그때 형태 하나를 추가(지금은 YAGNI).
- `DIAG_MAX=200`은 임의 상수. 실제 `err_msg`(지출 한도 안내문)가 잘려 핵심이 빠지면 늘린다.
- `resp.json()`/`resp.text`는 `c.post`가 본문을 이미 읽은 뒤라 `async with` 블록 밖(except 절)에서도 접근 가능할 것으로 예상 — MockTransport 테스트로 확인.
- 실서버(`uvicorn backend.main:app`)에서 GMS 키를 일부러 틀리게 두고 요청 1건 → 터미널에 `WARNING:backend.agent.llm:llm code=... err_code=...` 줄이 키 없이 보이는지 육안 확인. `--log-config`로 루트 핸들러를 따로 지정해 실행하는 경우엔 `basicConfig`가 무동작이지만 그 핸들러로 전파·마스킹되는지 함께 확인.
- 오류 본문에 사용자 프롬프트 일부가 반향되는 경우가 있는지(예: 400 invalid request) 실호출에서 확인. 있으면 `err_msg`만 생략하는 정도로 축소.

## 6. 상위 계약 영향·후속

- **D0 변경 없음. D1 변경 없음. DESIGN.md 변경 없음.** `mask()`는 기존 공개 함수를 그대로 사용.
- D7 보완(문서 수정 제안만): §3 실패 처리 또는 §9 로깅에 "HTTP 오류 시 응답 본문의 `message`·안쪽 error `code/type/message`를 `mask` 후 필드당 200자로 경고 로그에 남김(성공 응답 원문은 기존대로 미기록)" 한 줄.
- **Tool 경로(범위 밖, 후속)**: `Tool.run`의 `HTTPStatusError` 분기는 `_log_fail`이 `traceback.format_exc()`만 남기므로 상태 코드·URL(키는 mask)은 보이지만 본문은 없다 — 같은 진단 공백이 있다. 다만 DART·data.go.kr는 대부분 오류를 HTTP 200 본문(상태 코드 필드)으로 주고 이는 `_fetch_*`/파서가 `ToolFailure`로 처리하므로 실익이 작고, 본문 형식(XML 등)도 달라 `_err_diag`를 그대로 쓸 수 없다. 필요해지면 `run`의 `HTTPStatusError` 분기에서 `mask(e.response.text)[:200]`을 `_log_fail` 로그에 덧붙이는 정도로 별도 단위(D1 보완)에서 처리.
