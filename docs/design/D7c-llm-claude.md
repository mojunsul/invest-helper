# D7c LLM 공급 경로 전환 (GMS OpenAI → GMS Anthropic, claude-haiku-5-5)

기준: D7 §3(LLM 클라이언트), D7b(오류 진단 로그), D9(respond 요약), D17 §4(임베딩), D0 §config 표, 코드 `backend/agent/llm.py`·`config.py`·`nodes/analyze.py`·`nodes/respond.py`·`tools/filing_text.py`·`scripts/check_apis.py`, 메모리 `gms-provider-status`. 의사코드이며 구현 아님.

## 1. 목적

- 프로젝트 LLM을 GMS Anthropic 경로의 `claude-haiku-5-5`로 바꾼다(사용자 결정). 임베딩 기본값은 `local-hash-v1`(D17 대안)으로 바꾼다.
- **바뀌지 않는 것**: `llm.complete(messages, *, json_mode, timeout_s) -> str`와 `llm.embed(...)`의 시그니처, `LlmError` 코드 4종(MISSING_API_KEY/TIMEOUT/RATE_LIMIT/API_ERROR)과 매핑 규칙, D7b 진단 로그 형식, 키 이름 `GMS_API_KEY`. 호출부 `analyze.py`·`respond.py`는 수정하지 않는다.
- 새 의존성 없음(httpx 직접 호출 유지, OpenAI/Anthropic SDK·langchain 미도입).

## 2. 확인된 사실 (오케스트레이터 실호출, 2026-11-20)

- GMS OpenAI 경로(`.../gmsapi/api.openai.com/v1`): 모든 모델 429 `organization_spend_limit_exceeded`(SSAFY 상위 조직 한도, 코드 문제 아님).
- GMS Anthropic 경로 정상: `POST https://gms.ssafy.io/gmsapi/api.anthropic.com/v1/messages`, 헤더 `x-api-key: <GMS_API_KEY>`, `anthropic-version: 2023-06-01`, `content-type: application/json`. 바디 `{model, max_tokens, messages, system?}`, 응답 텍스트는 `content[*].text`. 사용 가능 모델 `claude-haiku-5-5`(선택), `claude-haiku-4-5-20251001`.
- GMS에는 사용 가능한 임베딩 모델이 없다.
- 현재 구조화 출력: `complete(json_mode=True)`가 `response_format={"type":"json_object"}`를 넣고, analyze는 `SYSTEM_PROMPT`에서 "오직 아래 JSON만 출력"을 이미 지시하며 `_parse()`가 응답의 첫 `{`~마지막 `}`를 잘라 `AnalyzeOut.model_validate`한다(실패 → `None` → 기존 폴백). respond는 `json_mode=False`.

## 3. 변경 범위

| 파일 | 함수/항목 | 변경 |
|---|---|---|
| `backend/agent/llm.py` | `complete()` | URL `GMS_BASE_URL + "/messages"`, 헤더 교체, 바디를 `_body()`로 생성, 응답 추출을 `_text()`로. 예외 분기·로그는 그대로 |
| `backend/agent/llm.py` | 신규 `_body(messages, json_mode) -> dict`, `_text(resp_json) -> str`, 상수 `LLM_MAX_TOKENS = 1024`, `ANTHROPIC_VERSION = "2023-06-01"`, `JSON_HINT` | §4 |
| `backend/agent/llm.py` | 모듈 docstring, `embed()` | docstring만 "GMS Anthropic Messages"로. `embed()` 코드는 그대로 두고 docstring에 "OpenAI 호환 `/embeddings` 전용. 기본 설정(`local-hash-v1`)에서는 호출되지 않음" 명시 |
| `backend/config.py` | `Settings` 기본값 | `GMS_BASE_URL="https://gms.ssafy.io/gmsapi/api.anthropic.com/v1"`, `GMS_MODEL="claude-haiku-5-5"` (**D0 §config 표 기본값과 충돌 → §9 제안 1 승인 후 반영**. 미승인이어도 `.env` 값으로 동작하므로 진행을 막지 않음) |
| `backend/tools/filing_text.py` | `EMBED_MODEL` | `"local-hash-v1"`(=`LOCAL_EMBED`). 결과로 `search_filing_text.requires_keys == ()`, 질의 임베딩 네트워크 호출 0. `MIN_SCORE_LOCAL=0.1`은 그대로(실측은 D17 4단계) |
| `scripts/check_apis.py` | `check_llm()` | `/messages` + Anthropic 헤더, tool 정의를 `{"name","description","input_schema"}`로, 응답에서 `type=="tool_use"` 블록 출력. 라벨 "GMS Anthropic" |
| `.env.example`, `README.md`, `docs/design/D17-filing-rag.md` | 표기 | §9에서 반영 완료 |

## 4. messages·구조화 출력 변환 규칙

```python
LLM_MAX_TOKENS = 1024  # 임의 초기값. analyze JSON·respond 요약(≤400자)·filing 답변에 충분
JSON_HINT = "JSON 객체 하나만 출력하세요. 코드펜스나 설명을 붙이지 마세요."

def _body(messages, json_mode):
    system = [m["content"] for m in messages if m["role"] == "system"]
    if json_mode:
        system.append(JSON_HINT)
    body = {"model": settings.GMS_MODEL, "max_tokens": LLM_MAX_TOKENS,
            "messages": [{"role": m["role"], "content": m["content"]} for m in messages if m["role"] != "system"]}
    if system:
        body["system"] = "\n\n".join(system)
    return body

def _text(d):
    t = "".join(b["text"] for b in d["content"] if b.get("type") == "text")
    if not t:
        raise ValueError("empty")   # → 기존 except 분기에서 API_ERROR
    return t
```

- **system 분리**: `role=="system"` 메시지는 순서대로 `"\n\n"`로 이어 최상위 `system` 문자열로. 나머지(user/assistant)는 순서 유지 통과. 현재 호출부는 모두 `[system, user]` 2개라 교대 규칙 문제 없음.
- **구조화 출력 = 프롬프트 JSON 지시 + 기존 `_parse()`**. `response_format`은 보내지 않는다. `json_mode=True`는 `JSON_HINT`를 system 끝에 덧붙이는 의미로 바뀐다(시그니처 불변). analyze `SYSTEM_PROMPT`·`_parse()`·폴백 경로는 무수정 재사용 — `_parse()`가 이미 `{`~`}` 절단으로 코드펜스·앞뒤 문장을 견딘다.
- 채택하지 않음: `tool_use` 강제(AnalyzeOut 스키마를 JSON Schema로 중복 정의해야 하고 응답 추출 경로가 갈림), assistant `{` 프리필(최신 모델에서 미지원 가능성, `_parse`로 불필요).
- `stop_reason=="max_tokens"`로 잘린 JSON은 `_parse` 실패 → analyze 기존 폴백. 별도 처리 없음.
- 응답 추출: `content` 중 `type=="text"` 블록 텍스트를 이어 붙인다. 없거나 형식이 다르면 `KeyError/TypeError/ValueError` → 기존 분기에서 `API_ERROR`.

## 5. 오류 매핑 (불변)

| 상황 | Anthropic/GMS 응답 | LlmError |
|---|---|---|
| 키 없음 | (요청 안 함) | MISSING_API_KEY |
| `httpx.TimeoutException` | — | TIMEOUT |
| HTTP 429 | `rate_limit_error` 또는 GMS 래핑 | RATE_LIMIT |
| HTTP 400/401/403/404/413/500/529 등 그 외 | `invalid_request_error`·`authentication_error`·`overloaded_error` 등 | API_ERROR (529도 새 코드 없이 API_ERROR) |
| 200인데 `content` 없음/텍스트 블록 0개/JSON 아님 | — | API_ERROR |
| 그 외 `httpx.HTTPError` | — | API_ERROR |

- **D7b 진단 로그 유지**: `_err_diag()` 무수정. Anthropic 본문 `{"type":"error","error":{"type":..,"message":..}}`는 평면 `error`로 읽혀 `err_type`/`err_msg`가 찍히고 `err_code=None`(Anthropic은 code 필드 없음). GMS 래핑 `{"message":"[.. 에러] ..","error":{"type":"error","error":{..}}}`은 기존 `error.error` 언래핑으로 안쪽 값이 찍힌다. `mask()`→절단 순서, `from None`, 키 비노출 규칙 그대로.

## 6. config·.env 이전 안내

- 키 이름 `GMS_API_KEY`·`GMS_BASE_URL`·`GMS_MODEL` 유지. 같은 GMS 키로 Anthropic 경로가 동작함을 확인했다.
- `config._load()`는 `.env` 값이 있으면 우선한다. 기존 `.env`에 OpenAI URL/`gpt-4o-mini`가 남아 있으면 `{openai URL}/messages`로 요청이 가서 실패(API_ERROR, D7b 로그에 status·본문 표시) → analyze가 기본 응답으로 떨어진다. **코드로 자동 보정하지 않는다.** 사용자가 `.env`의 두 줄을 지우거나(코드 기본값 사용, §9 제안 1 승인 시) Anthropic URL·`claude-haiku-5-5`로 고친다. 절차는 README §2에 반영했다.

## 7. 테스트 관점 (네트워크 없음, httpx MockTransport)

**갱신 대상 (요청·응답 형태를 단언하는 기존 테스트)**
- `tests/test_d7.py::test_complete_ok_and_request`: URL `…/messages`, 헤더 `x-api-key==KEY`·`anthropic-version=="2023-06-01"`·`authorization` 없음, 바디 `model`(Settings 기본값)·`max_tokens==LLM_MAX_TOKENS`·`response_format` 없음, mock 응답을 `{"content":[{"type":"text","text":"hi"}]}`로.
- `tests/test_d17.py::test_missing_gms_key`: 기본 `local-hash-v1`에서는 키가 필요 없으므로 "GMS 키 없이도 `MISSING_API_KEY`가 아니다" + `base._TOOLS["search_filing_text"].requires_keys == ()`로 바꾼다.

**영향 없음(유지)**: `llm.complete`를 monkeypatch하는 테스트(`test_d7` analyze 계열, `test_d9`, `test_d16_e2e`, `test_d17_route`), `test_complete_http_errors`·`timeout_and_bad_body`(`{"nope":1}` → API_ERROR 그대로)·`empty_key_no_network`, `test_diag_*` 4종(본문 형태 단위 검사라 공급자 무관), `test_d17::test_llm_embed_errors`(embed 코드 불변).

**신규**
- `_body`: system 1개·2개 분리/연결, system 없음 → `system` 키 없음, user/assistant 순서 보존, `json_mode=True`면 `JSON_HINT`가 system 끝에, `False`면 없음.
- `_text`/`complete`: 텍스트 블록 2개 이어 붙임, 비텍스트 블록 무시, `content: []` → API_ERROR.
- 오류: Anthropic 평면 429 본문 → RATE_LIMIT + 로그 `err_type=rate_limit_error`·`err_code=None`, 529 → API_ERROR, 키 비노출.
- analyze 회귀: 코드펜스로 감싼 JSON 응답(```` ```json {...} ``` ````)을 mock `complete`가 돌려줘도 `_parse` 성공.

## 8. 구현 시 확인 항목

- **실호출(사용자 승인 하 최소 호출)**: `claude-haiku-5-5`로 analyze 대표 요청 1~2개(예: "반도체 업종에서 ROE 10% 이상", "삼성전자 PER 알려줘")의 JSON이 `_parse`로 통과하는지, respond 요약이 `SUMMARY_MAX`·숫자 검증을 통과하는지. 실패하면 `JSON_HINT`/프롬프트 문구만 조정.
- `LLM_MAX_TOKENS=1024`·`LLM_TIMEOUT_S=30` 적정성(응답 지연·잘림 여부).
- GMS가 `system` 최상위 필드를 그대로 전달하는지(오케스트레이터 확인은 messages만 사용 시일 수 있음). 안 되면 system을 첫 user 메시지 앞에 붙이는 방식으로 대체.
- GMS Anthropic 경로의 실제 오류 본문 형태(래핑 여부) — D7b 로그로 관찰만.
- D7a 등 기존 문서의 "gpt-4o-mini 분류 일관성" 확인 항목은 haiku-5-5 기준으로 읽는다.

## 9. 상위 문서 수정 내역

**반영 완료(사용자 승인분)**
- `.env.example`: 주석·`GMS_BASE_URL`·`GMS_MODEL`을 GMS Anthropic/`claude-haiku-5-5`로.
- `README.md`: §2 키 표 용도, 기존 `.env` 이전 절차(두 줄 삭제 또는 교체), §7 출처 표, §8 `search_filing_text` 임베딩 문구(로컬 해시, 네트워크 없음).
- `docs/design/D17-filing-rag.md`: §4 기본값을 `local-hash-v1`로(GMS 임베딩은 보존 경로), §10 오류 표 MISSING_API_KEY 행 조건, §11 3·4단계 문구, 구현 시 확인 항목의 GMS 임베딩 가용성 = 확인 완료(불가).
- `docs/DESIGN.md`: OpenAI/gpt-4o-mini/langchain-openai 표기가 없어(§1 config는 `GMS_*`만 언급) 수정 없음.

**제안 → 사용자 승인, D7c 구현 시 반영 완료(D0 §config)**
1. **D0 §config 표**: `GMS_API_KEY` 비고 "OpenAI 호환" → "GMS Anthropic Messages", `GMS_BASE_URL` 기본값 → `https://gms.ssafy.io/gmsapi/api.anthropic.com/v1`, `GMS_MODEL` 기본값 → `claude-haiku-5-5`. 승인 시 `config.py` 기본값도 같이 바꾼다(§3).

**참고**: D7 §3의 엔드포인트(`/chat/completions`, Bearer)와 `json_mode`=`response_format` 서술, D7 §10의 response_format 확인 항목은 이 문서로 대체된다.
