# D21 — API 응답 스키마 문서화 (체크리스트 21번 F3017·F3018)

상위 기준: `docs/DESIGN.md` §9, D0, D1, D12(§5), D12b/D19(reports 필드), D13(§5-1·§6). **DESIGN·D0·D1 수정 없음**(§9 '수정 제안' 없음).

## 1. 목적·범위

체크리스트 21번 ①은 "/docs의 API 목록과 **스키마**를 명세서와 대조"다. 현재 요청 스키마(ChatRequest/CreateBody/PatchBody)는 /docs에 나오지만 **응답 스키마가 전부 `{}`**(route에 `response_model` 없음). 이를 해소한다.

- **런타임 응답은 1바이트도 바꾸지 않는다**(최우선). 변경 범위: 모델 정의 추가 + 데코레이터의 `responses=` 인자 + 테스트 1~2건.
- 엔드포인트 경로·메서드·요청 검증·status code·오류 응답은 건드리지 않는다. 프런트 변경 없음.

## 2. 현재 응답 목록 (실제 코드 기준) vs DESIGN §9

| 엔드포인트 | 성공 응답(코드 실측) | §9 대조 |
|---|---|---|
| `POST /api/chat` | 200 dict 15키(순서): `answer,status,verdicts,industry_avg,steps,tool_calls,replans,disclaimer,missing_api_key,saveable,intent,conditions,metrics,disclosures,evidence` (`build_response`, 실패 시에도 같은 15키) | 일치. 409 `SESSION_BUSY`는 JSONResponse 직접 반환, 422는 앱 핸들러 |
| `GET /api/reports` | 200 `[ReportSummary]` = `id,name,stock_code,stock_name,base_date,created_at,updated_at,review_date,target_per,target_roe` | §9는 `[ReportSummary]`만 명명. 필드는 D12 §5+D12b+D19가 확정(일치) |
| `GET /api/reports/{id}` | 200 `Report` = Summary + `metrics,industry_avg,disclosures(null 가능),disclaimer,memo` | 일치. 404 `REPORT_NOT_FOUND` |
| `POST /api/reports` | **201** `{id, duplicate_of:[int]}` | 일치(§9는 코드 미명시, 201은 D12 확정). 404 `DRAFT_NOT_FOUND` |
| `PATCH /api/reports/{id}` | 200 `Report` | 일치. 404 `REPORT_NOT_FOUND` |
| `DELETE /api/reports/{id}` | 204 본문 없음 | 일치. 404 `REPORT_NOT_FOUND` |
| `GET /api/logs` | 200 `[{session_id, started_at, event_count}]` | 일치 |
| `GET /api/logs/{session_id}` | 200 `[LogEvent]` = `session_id,seq,ts,type,payload,run` | §9 `[Event]` + `run`(D13 §6, 기승인) |
| `DELETE /api/logs/{session_id}` | 204(멱등) | 일치 |
| 공통 오류 | `{code,message}`: 422 `INVALID_REQUEST`, 404 `NOT_FOUND`/`SESSION_NOT_FOUND`/`REPORT_NOT_FOUND`/`DRAFT_NOT_FOUND`, 405 `METHOD_NOT_ALLOWED`, 409 `SESSION_BUSY`, 500 `INTERNAL_ERROR` | 일치(§9 "오류 응답은 {code,message}만") |

기타 엔드포인트: 정적 마운트(`/`)와 FastAPI 기본 `/docs`·`/openapi.json`뿐. API 라우트 추가 없음.

## 3. 방식 결정: `responses=` 문서화 전용 (response_model 불채택)

| 항목 | `response_model=` | `responses={code:{"model":…}}` (채택) |
|---|---|---|
| 런타임 검증·필터링 | 반환 dict를 모델로 재검증·재직렬화 → extra 키 누락, **필드 순서가 모델 선언 순서로 변경**, int→float 강제 변환(`5`→`5.0`), Literal 불일치 시 500 | **없음**. 핸들러 반환값이 그대로 JSONResponse로 나감 |
| `exclude_unset`/`None` 처리 | 별도 옵션 튜닝 필요(`response_model_exclude_*`) | 해당 없음 |
| 204·`JSONResponse` 직접 반환(409/404 경로) | 우회되어 모델과 무관 | 문서에만 반영 |
| /docs 응답 스키마 | 표시됨 | **동일하게 표시됨**(200/201 본문에 `$ref`) |
| 위험 | 중간(바이트 변경·500 회귀) | 없음 |

근거: 목적이 "명세와 대조 가능한 문서"이고 값 보증은 이미 e2e 테스트(RESP_KEYS)가 한다. 런타임 불변이 최우선이므로 문서 전용을 택한다. 단점(모델과 실제 응답이 어긋나도 런타임이 모름)은 §6 적합성 테스트로 메운다.

- 2xx 키는 실제 status와 맞춘다(chat/GET/PATCH 200, POST reports 201, DELETE 204는 `responses={204:{"description":"삭제됨"}}`처럼 모델 없이 기술. 기존 `status_code=204` 유지).
- 오류는 `responses={404:{"model":ErrorBody}, …}`로 각 라우트가 실제 낼 수 있는 코드만 기재. **422를 명시하면 FastAPI 기본 `HTTPValidationError` 대신 `ErrorBody`가 표시**된다(FastAPI는 422 계열이 이미 있으면 기본 422를 추가하지 않음) — 실제 응답이 `{code,message}`이므로 이쪽이 정확. 500은 전 라우트 공통이라 생략 가능(선택).

## 4. 모델 (위치: `backend/schemas.py`, 요청 모델과 같은 파일)

원칙: **관대한 타입**. 어긋남 가능성이 있는 곳은 `Any`/`dict`로 두고 Literal·엄격 숫자 타입은 쓰지 않는다(문서 전용이라도 §6 적합성 테스트가 오탐으로 깨지지 않게). 필드는 §2 표의 키 순서로 선언(문서 가독성; 런타임엔 영향 없음). 모델 설명은 `Field(description=…)` 없이 클래스 docstring 한 줄만.

```
ErrorBody      {code: str, message: str}
SaveResult     {id: int, duplicate_of: list[int]}
ReportSummary  {id:int, name:str, stock_code:str, stock_name:str, base_date:str|None,
                created_at:str, updated_at:str, review_date:str|None,
                target_per:float|None, target_roe:float|None}
Report(ReportSummary) + {metrics: dict[str, Any], industry_avg: dict[str, Any],
                disclosures: list[dict[str, Any]] | None, disclaimer: str|None, memo: str|None}
LogSession     {session_id:str, started_at:str, event_count:int}
LogEvent       {session_id:str, seq:int, ts:str, type:str, payload:dict[str, Any], run:int}
Verdict        {stock_code:str, stock_name:str, status:str, reasons:list[str], failed:list[str]}
Step           {seq:int, stage:str, round:int, started_at:str, ended_at:str|None, intent:str|None, reason:str|None}
ToolCall       {type:str, seq:int, round:int, tool:str|None, input:Any=None, cache_hit:bool|None,
                ok:bool|None, error_code:str|None, duration_ms:int|float|None, summary:Any=None}
                # call/result 두 형태의 합집합(전 필드 선택) — 판별 유니온은 과설계라 불채택
Replan         {count:int, trigger:str, change:str}
Saveable       {stock_code:str, stock_name:str}
ChatResponse   {answer:str, status:str, verdicts:list[Verdict], industry_avg:dict[str,Any],
                steps:list[Step], tool_calls:list[ToolCall], replans:list[Replan], disclaimer:str,
                missing_api_key:bool, saveable:list[Saveable], intent:str|None,
                conditions:dict[str,Any]|None, metrics:dict[str,Any], disclosures:dict[str,Any],
                evidence:list[dict[str,Any]]}
```

- 동적 구조 처리: `metrics`(MetricRow: values/fiscal_period/actual_basis/estimated/reasons), `industry_avg`, `disclosures`, `conditions`, `LogEvent.payload`, `evidence`는 `dict[str, Any]` 수준에서 멈춘다. 세부 키는 D0/D12/D13 문서가 기준이며 §9도 이 수준(`[Event]`, `Report`)으로만 규정한다. `status`도 `str`(허용값 `answered|asked|limit|error`는 설명 문자열로만 기재 가능, 선택).
- `ToolCall`의 `ok`/`error_code`/`duration_ms`/`summary`는 result 형태에만 존재(call 형태엔 키 없음) — 문서상 선택 필드. 모델명 충돌 방지를 위해 `Verdict`는 `ChatVerdict`로 명명해도 무방(AgentState `Verdict`와 혼동 방지; 구현 재량).
- 요청 모델(`ChatRequest`)은 schemas.py 유지. `CreateBody`/`PatchBody`는 reports.py에 그대로 둔다(이동 안 함, 변경 최소).
- 모델은 **FastAPI의 `responses` 인자에서만 참조**한다. 핸들러 코드·반환 타입 힌트 변경 없음(반환 타입 힌트를 모델로 바꾸면 FastAPI가 response_model로 해석하므로 **금지**).

## 5. 라우트 적용 (데코레이터 인자만 추가)

```
_ERR = lambda *codes: {c: {"model": ErrorBody} for c in codes}   # 공용 헬퍼(1줄), schemas.py

POST   /api/chat            responses={200:{"model":ChatResponse}, **_ERR(409,422)}
GET    /api/reports         responses={200:{"model":list[ReportSummary]}}
GET    /api/reports/{id}    responses={200:{"model":Report}, **_ERR(404,422)}
POST   /api/reports         responses={201:{"model":SaveResult}, **_ERR(404,422)}
PATCH  /api/reports/{id}    responses={200:{"model":Report}, **_ERR(404,422)}
DELETE /api/reports/{id}    responses={204:{"description":"삭제됨"}, **_ERR(404,422)}   (기존 status_code=204 유지)
GET    /api/logs            responses={200:{"model":list[LogSession]}}
GET    /api/logs/{sid}      responses={200:{"model":list[LogEvent]}, **_ERR(404,422)}
DELETE /api/logs/{sid}      responses={204:{"description":"삭제됨(멱등)"}, **_ERR(422)}
```

`GET /api/reports`·`GET /api/logs`는 인자 없음이므로 422 미기재. (`responses`에서 200/201을 명시해도 기존 기본 응답 설명("Successful Response")은 FastAPI가 병합해 유지.) 구현이 이 헬퍼에서 막히면(예: `list[...]` 모델 미지원 버전) 실호출로 확인 후 래퍼 모델 불가 시 `{"content":{"application/json":{"schema":…}}}` 직접 기술로 대체(§8).

## 6. 변경 영향

- 기존 테스트: `set(b)==RESP_KEYS`, `set(d[0])=={…,"run"}`, 422 `{code,message}` 단언 모두 **런타임 불변이므로 수정 없이 통과**해야 한다. 수정이 필요해지면 런타임이 바뀐 것이므로 구현 오류로 간주.
- 프런트(`chat.js`/`reports.js`): 응답 불변 → 영향 없음.
- `openapi.json`: 각 operation의 responses에 스키마·`components.schemas`에 위 모델 추가, 422 스키마가 `HTTPValidationError`→`ErrorBody`로 교체. 이 외 변화 없음.
- **런타임 바이트 불변 확인(1회성 구현 단계 절차)**: 구현 전(git stash 또는 변경 전 커밋)에 기존 e2e/API 테스트의 TestClient 응답 대표 9종(+409·404·422)을 `r.content`(바이트)와 status로 JSON 파일에 기록 → 구현 후 동일 입력으로 재실행해 바이트 동일 비교. 스냅샷 파일은 커밋하지 않는다(임시 파일, 일회성 증빙으로 구현 보고에 결과만 기재). `responses=` 방식은 구조상 응답 경로를 건드리지 않으므로 이 확인은 안전망.

## 7. 테스트 (신규 `tests/test_d21.py`, 1파일 2~3건)

1. **openapi 스키마 대조(체크리스트 21번 ① 자동화)**: `app.openapi()`에서 §5 표의 9개 (경로, 메서드)마다 성공 코드(200/201/204)와 오류 코드가 존재하고, 성공 응답 스키마(`$ref` 해석, 배열이면 `items.$ref`)의 **프로퍼티 키 집합이 DESIGN §9 기준 집합과 정확히 일치**함을 단언. 기준 집합은 테스트 상수로 §2/§4의 키 목록(ChatResponse 15키 = RESP_KEYS, Report 15키, ReportSummary 10키, LogEvent 6키, LogSession 3키, SaveResult 2키, ErrorBody 2키). 요청 스키마는 `ChatRequest`/`CreateBody`/`PatchBody` 필드 집합도 함께 대조(§9 요청 열). 422 응답이 `ErrorBody` 참조인지, 응답 스키마 어디에도 빈 `{}`가 없는지도 단언.
2. **적합성(모델↔실제 응답)**: 기존 e2e 패턴(TestClient, 임시 DB, `memory.run_turn` 목)으로 chat 1회·reports 저장/조회/수정·logs 조회 응답을 얻어 각 모델로 `model_validate(body)` 통과 + 최상위 키 집합이 모델 필드 집합과 같음을 단언(모델이 실제와 어긋나면 이 테스트가 잡는다 — 문서 전용 방식의 보완). 기존 e2e 테스트 파일 수정 대신 신규 파일에서 최소 fixture로 구성(재사용 가능한 헬퍼가 있으면 import).
3. (선택) 기존 전체 테스트 스위트 통과 = 런타임 불변의 회귀 증빙.

## 8. 시나리오 (검증용)

1. **/docs 대조**: 서버 실행 후 `/docs`에서 `POST /api/chat` 응답 200 스키마가 15키로 표시되고, `GET /api/reports/{id}` 응답에 `metrics`·`disclosures` 등이 보이며, 422/404가 `{code,message}`로 표시된다(`{}` 없음).
2. **런타임 불변**: 구현 전·후 동일 요청(채팅 1회, 리포트 저장→목록→상세→수정→삭제, 로그 조회→삭제, 잘못된 session_id 422, 없는 id 404)의 바이트가 동일하다. 기존 테스트 전체가 수정 없이 통과한다.
3. **동적 구조 비필터링**: 리포트 `metrics`에 임의 중첩 키(`values`, `reasons`)가 있는 저장본과 `LogEvent.payload`의 임의 키가 응답에 그대로 나온다(`response_model` 불채택 증명). draft에 모델에 없는 키를 넣어도 응답에서 사라지지 않는다.
4. **네트워크 탭 확인(②)**: `index.html`에서 질의 1회 → `POST /api/chat` 200, `reports.html` → `GET /api/reports`, 저장/수정/삭제 → 201/200/204, 로그 조회 호출이 각각 별개 경로로 보이고 응답 키가 /docs 스키마와 같다.

## 9. 구현 시 확인 항목 (FAIL 아님)

- 설치된 FastAPI/Pydantic 버전에서 `responses={200:{"model":list[X]}}`가 허용되는지, 200/201 지정 시 기본 "Successful Response" 설명과 병합되는지, 422 지정이 기본 `HTTPValidationError`를 대체하는지는 `app.openapi()` 실호출로 확인. 안 되면 `list[X]` 대신 `RootModel[list[X]]` 또는 직접 `content.schema` 기술로 대체.
- `ToolCall`·`ReportSummary` 등 `int|float` 필드 타입은 §7-2 적합성 테스트 결과에 맞춰 느슨하게 조정(문서 전용이므로 런타임 영향 없음).
- `reports.py` 커스텀 `_Route`에서 `responses` 인자가 `APIRoute` 생성자로 정상 전달되는지 확인(`router.get(..., responses=…)`는 `route_class`에 kwargs로 전달됨).
- 모델 `status` 허용값을 description에 쓸지 여부(선택). `GET /api/logs` 응답 DESIGN `[Event]`와 `run` 추가는 D13 승인분이므로 재쟁점화하지 않음.
- 204 응답에 `ErrorBody`가 섞이지 않는지, openapi에 응답 본문 스키마 없음 확인.

## 10. 수정 제안

**없음.** DESIGN §9·D0·D1은 그대로 성립한다(응답 형식은 §9와 이미 일치, 본 단위는 문서화만 추가).
