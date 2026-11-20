# D16 통합 (대표 요청 5개 E2E 추적 · 실행 방법 · 승인 대기 목록 · 실호출 전 체크리스트)

기준: `docs/DESIGN.md` §1·§9·§10·§11, D0~D15. 문서만 작성(코드 없음). 상위 계약(D0·D1·DESIGN.md)은 **수정하지 않는다** — §4는 사용자 승인 대기 목록일 뿐이다.
이 단위의 구현 산출물은 **테스트 2개 파일 + README 작성**뿐이며 `backend/`·`frontend/` 코드는 바꾸지 않는다(테스트가 결함을 드러내면 그 단위의 수정으로 별도 보고).

## 1. 범위와 기존 테스트와의 차이

| 기존 | mock 경계 | D16 |
|---|---|---|
| `test_d9.py::test_e2e_*` | `llm.complete`, `registry.call`, `resolve_*` 전부 monkeypatch, `graph.run_agent` 직호출 | Tool 본체·identity·memory·API까지 실제 |
| `test_d13.py::test_integration_*` | `memory.run_turn` 자체를 mock | `run_turn`·그래프·노드·Tool 실제 |

D16 E2E는 **`TestClient(backend.main.app)` → `/api/chat` → `memory.run_turn` → `graph.run_agent` → analyze/plan/execute/judge/replan/respond → Tool 본문 → (HTTP 시임 mock)** 와 `/api/reports`·`/api/logs` 까지를 실제 코드로 관통한다. 새 로직 없음, 새 계약 없음.

## 2. 대표 요청 5개 E2E 추적 테스트 (`tests/test_d16_e2e.py`)

### 2-1. mock 경계 (이것만 mock)

| 경계 | 방식 | 비고 |
|---|---|---|
| LLM | `backend.agent.llm.complete` monkeypatch(`json_mode=True`→analyze 응답 큐 pop, `False`→요약: 고정 문장 또는 `LlmError`) | `test_d9.py::g`와 같은 방식. analyze JSON은 D7 스키마(`intent, reason, industry, market, constraints, want_disclosures, target_stock`) |
| 주가·후보 HTTP | `stock_candidates._fetch_page`, `financials._fetch_price` | 원시 응답 형태는 `tests/test_d3.py::row/page`, `test_d4.py::price` 헬퍼를 **복사 재사용**(공용 `tests/conftest.py` 신설은 하지 않음 — 단순화) |
| 재무 HTTP | `financials._fetch_fin` | `test_d4.py::frow/resp` 형태 |
| 공시 HTTP | `disclosures._fetch_list` | `test_d5.py::row/resp` 형태 |
| 날짜 | 세 Tool의 `_today` → 고정일(`date(2026,11,19)`) | 휴장·기준일 결과를 결정적으로 |
| 키 | 각 Tool 모듈·`base`의 `settings`를 `Settings(DATA_GO_KR_KEY="E2E_SECRET_KEY", DART_API_KEY="E2E_SECRET_KEY2", GMS_API_KEY="E2E_SECRET_KEY3")`로 monkeypatch | `test_d4.py::env`와 동일 패턴. **키 누락 시나리오는 빈 값 Settings** |

**실제로 쓰는 것(mock 금지)**: `memory`, `graph`, 모든 노드, `registry`·`@register` 래퍼(타임아웃·캐시·이벤트), `constraints`, `identity.resolve`(+tmp SQLite), `bus/logger_agent/stream`, `api/*`, `main` 앱·미들웨어.

**identity 실제 사용**: `config.DB_PATH`를 `tmp_path`로 monkeypatch한 뒤 `store.connect_rw()`로 `company` 행을 직접 INSERT(`detail_status='ok'`, `listed=1`, `induty_code`, `crno`, `corp_code`, `name`, `name_norm`, `corp_cls`)하고 `cache_meta`는 `complete` 판정에 필요한 만큼만(`counts()` 기준 total>0, pending+failed 5% 이하). `resolve.TERMS_PATH`를 tmp `terms.json`(§2-2)으로 바꾸고 `resolve._terms.cache_clear()`(양쪽 fixture 종료 시도 clear). → `resolve_industry`·`select_by_industry`·`get_crno`·`get_corp_code`·`similar_industries`·`resolve_stock`가 전부 실제.

**네트워크 차단 가드(autouse)**: `httpx.AsyncClient.send`·`httpx.Client.send`를 `AssertionError("real HTTP")`를 던지도록 monkeypatch. mock 누락이 실호출로 새는 것을 막는다(이 머신의 로컬 `.env`에는 **실제 키가 들어 있다** — `load_dotenv()`가 모듈 import 시 읽으므로 가드와 `settings` 패치는 필수, §5-1 참조).

**앱 기동**: 기존 D13 `env`/`client` 패턴 그대로(`config.DB_PATH` tmp, `bus._subscribers`/`_seq`·`stream._sinks/_installed`·`logger_agent._installed` 초기화, `memory.clear_all()`, **`main.FRONTEND`는 실제 `frontend/` 유지**해 정적 서빙도 같이 확인). `with TestClient(main.app) as c`로 lifespan 실행(`registry.load_all`·`init_table`·`logger_agent.install`·`stream.install`). session_id는 요청마다 새 UUID(§2-5의 연속 세션 테스트만 고정).

### 2-2. 픽스처 유니버스 (모든 숫자는 계산 가능한 임의값, 단위 원)

terms: `반도체→["261"]`, `은행→["6412"]`, `금융지원→["6419"]`(②유사 업종 시험용, `similar_industries("은행")`이 6419 계열을 돌려주는지는 구현 시 확인). 그 외 term 없음.

반도체(prefix 261), 후보 조회 응답 = 시가총액 큰 순:

| 코드 | 이름 | 시장 | 시총 | 순이익 | 자본 | ROE | PER | 비고 |
|---|---|---|---|---|---|---|---|---|
| 000001 | 알파반도체 | KOSPI | 1000 | 100 | 500 | 20.0 | 10.0 | |
| 000002 | 베타반도체 | KOSPI | 3000 | 200 | 1000 | 20.0 | 15.0 | |
| 000003 | 감마반도체 | KOSDAQ | 500 | 25 | 500 | 5.0 | 20.0 | |
| 000004 | 델타반도체 | KOSPI | 800 | 100 | 500 | 20.0 | 8.0 | |
| 000005 | 엡실론반도체 | KOSPI | 700 | – | – | – | – | 요청 3에서만 재무 응답 빈 값(`NO_FINANCIALS`), 요청 1에서는 universe에서 제외 |

공통 재무 행: `enpSaleAmt`=1000, `enpBzopPft`=150, `enpTastAmt`=1000, `fnclDebtRto`=100(요청 1·3).
공시: 000001만 1건(`분기보고서`, 날짜, 뷰어 URL `https://dart.fss.or.kr/...` 형태), 000004는 빈 응답(`NO_RESULT`), 000002는 HTTP 오류(`API_ERROR` → 해당 종목만 "조회 실패"; 000002는 FAIL이라 호출되지 않음 — 호출 안 됨 자체가 단언 대상).

은행(prefix 6412) 3종: ROE 각 10.0 / 12.0 / 8.0(순이익·자본으로 구성), 부채비율(`fnclDebtRto`) 800/900/1100, 전부 KOSPI. 유사 업종 6419 1종(KOSDAQ, ROE 5, 부채비율 300) — ②에서 후보에 추가됨.

### 2-3. 요청별 기대 결과 대조표

각 행의 "기대"는 DESIGN §10 문구 + D0/D8/D9의 승인된 해석이다. **DESIGN §10-2는 "3회 후 되묻기"라고 쓰나, D0 승인 항목 5(효과 있는 단계만 센다)에 따라 시장 미지정이면 실제 재계획은 2회** — 대조는 승인된 해석으로 하고 그 차이를 테스트 주석으로 남긴다(DESIGN 변경 요청은 §4 P-1x에 포함하지 않음: D0 승인 완료분이므로 DESIGN §4 문구 정합은 선택 사항, 사용자 판단).

| # | 입력(analyze mock) | 기대 (응답 필드 / 이벤트 / DB) |
|---|---|---|
| 1 | "반도체 중 ROE 10% 넘고 PER 산업 평균 이하" → `screen`, 반도체, constraints=[ROE>=10, PER<=ref industry_avg], `want_disclosures=False`. 시임: 000001~000004 | `status=answered`; `verdicts` 4건: 000001·000004 PASS, 000002(PER 15>12.5)·000003(ROE 5) FAIL; `industry_avg.PER`={value 12.5, n 4}(중앙값); `replans=[]`; `tool_calls`: `find_stock_candidates`×1, `get_financials`×4, `search_disclosures`×0; `steps` stage 순 `analyze,plan,execute,judge,respond` 모두 round 0; `answer`에 알파·델타 표 행·"산업 평균"·면책(`respond.DISCLAIMER`) 포함, `answer.endswith(DISCLAIMER)`; `saveable`=[000001, 000004]; `missing_api_key=false`; `run_logs`에 `final` 정확히 1건(`answered`) |
| 1b | 위와 같되 `want_disclosures=True`("공시도 보여줘") | PASS 2종목에만 `search_disclosures`×2(FAIL 종목 0회), 000001 공시 줄에 링크, 000004 "공시 없음", `status` 불변(answered), 요약 LLM 프롬프트에 공시 제목 미포함(llm mock이 받은 messages 검사) |
| 2 | "ROE 25% 넘고 부채비율 30% 이하 은행" → `screen`, 은행, 시장 미지정. 시임: 은행 3종 + 6419 1종 | `status=limit`; `replans`의 `change`=[`similar_industry`,`limit_up`](①은 시장 미지정이라 건너뜀); `steps` round 0·1·2 이고 round 1·2에 `plan,execute,judge` 재발행; `get_financials` **HTTP 시임 호출은 종목당 1회**(기존 3종은 round 1·2에서 `tool_call.cache_hit=true`), 6419 종목은 round 1에서 신규 1회; `industry_avg`는 누적 후보 기준 재계산; `answer`에 "재계획 이력"·관측 최고 ROE 12.00%·"조건을 완화할까요?"·면책; `saveable=[]`; `final(limit)` 1건. **2b**: 시장을 KOSPI로 지정한 변형 → `change`=[`market_expand`,`similar_industry`,`limit_up`] 3회 + 응답에 시장 확대 명시 문구 |
| 3 | 요청 1과 같되 universe에 000005 추가, 그 재무 응답은 빈 값 | `status=answered`; `verdicts["000005"]`={UNDECIDABLE, reasons에 `NO_FINANCIALS`}; 나머지 4종 판정은 요청 1과 동일; `industry_avg.n=4`(000005 제외); `tool_result`에서 000005 `ok=false, error_code=NO_FINANCIALS`; 다른 종목 `get_financials`는 계속 실행(`tool_calls` 개수=5); `answer` 제외 목록에 "재무정보 없음"; 재계획 없음 |
| 4 | "안녕" → `chitchat` | `status=answered`; Tool 호출 0(`tool_calls=[]`); `steps` stage ⊆ {analyze, respond}(plan 이후 없음); `answer`=안내 템플릿+면책; 메모리 불변(같은 세션 직후 후속 요청의 `conditions`가 기본값); `final(answered)` 1건 |
| 5 | "ROE 높은 종목" → `screen`, industry=None, constraints=[{metric:ROE}](임계값 없음) | `status=asked`; Tool 호출 0; `answer`가 되묻기 문구로 시작+면책; **`_fetch_page/_fetch_fin/_fetch_price/_fetch_list` 호출 0회**(시임 카운터); `final(asked)` 1건; **5b(연속)**: 같은 세션에 "반도체에서 10% 이상"(analyze mock: industry=반도체, 숫자만) → 후속 병합으로 ROE>=10 승격 → 이번엔 Tool 실행되어 `answered` |

### 2-4. 모든 시나리오 공통 불변식 (헬퍼 `assert_common(resp, sid)` 1개로 구현)

1. 응답 키 집합이 D13 §5-1(`answer,status,verdicts,industry_avg,steps,tool_calls,replans,disclaimer,missing_api_key,saveable`)과 일치, `disclaimer==respond.DISCLAIMER`.
2. `GET /api/logs/{sid}`: 이벤트 `run` 값이 요청 횟수와 같고, 각 run의 첫 `stage_start`=analyze·마지막=`final`·`final` 정확히 1건, id 순 정렬에서 `seq` 단조 증가(같은 run 내).
3. 응답 `steps`/`tool_calls`/`replans`가 같은 run의 `run_logs`로부터 재구성한 값과 동일(수집 이벤트 = 저장 이벤트).
4. **키 비노출**: `E2E_SECRET_KEY*` 문자열이 모든 응답 본문·`run_logs.payload_json` 전체·`caplog` 텍스트에 0건.
5. 오류 응답이 없음(전부 200). 이 시나리오에서 `INTERNAL_ERROR`/`session busy`가 나오면 실패.
6. 시임 호출 카운터와 `tool_call` 이벤트 수의 관계: `cache_hit=false`인 `tool_call` 수 = 해당 시임 호출 수(Tool별), `cache_hit=true`는 시임 호출 0.
7. 네트워크 차단 가드가 한 번도 발동하지 않음.

### 2-5. reports·logs 연결 (E2E 확장 테스트)

한 세션에서: 요청 1 실행 → `saveable[0]`로 `POST /api/reports`(201, `duplicate_of=[]`) → `GET /api/reports/{id}`의 `metrics`가 응답 `verdicts`/draft 값(ROE 20.0, PER 10.0 등)과 일치, `disclaimer` 저장 → 같은 종목 재`POST` → 201 + `duplicate_of=[첫 id]` → `PATCH`(name/memo만 변경, 지표 불변, `updated_at` 갱신) → `DELETE` 204 → `GET` 404 `REPORT_NOT_FOUND`. 이어 lookup("알파반도체 알려줘", analyze mock: `lookup`, target_stock) → 7지표 표기, 조건 불변. 마지막으로 `GET /api/logs`가 해당 세션 1건(`event_count` = 상세 이벤트 수)이고 `DELETE /api/logs/{sid}` 204 후 상세 404 `SESSION_NOT_FOUND`, 메모리(후속 요청 조건 병합)는 유지.

**추가 경로(작은 시나리오 2개)**: ① 키 누락(`Settings` 전 키 빈 값): 요청 1 → `status=error`, `missing_api_key=true`, judge 단계 없음, `replans=[]`, 키 안내 문구; ② LLM 키 누락(`llm.complete`가 `LlmError("MISSING_API_KEY")`) → analyze 단계에서 `status=error`, Tool 0회, 메모리 미갱신.

### 2-6. 정적 서빙 연결

같은 앱에서 `GET /`(index.html 200), `/reports.html`·`/chat.js`·`/common.js`·`/reports.js`·`/style.css` 200, CSP·`nosniff` 헤더 존재, `/reports`(확장자 없음) 404 JSON(`{code,message}`), `/api/*` 라우트가 정적보다 우선. (D14/D15가 임시 frontend로 이미 검증 → 여기서는 **실제 `frontend/` 파일 존재**만 확인.)

## 3. 실행 방법 점검

DESIGN §1이 `README.md # 실행법, 외부 API 출처·이용조건, 구현 범위`를 요구한다. 현재 README는 한 줄(`# invest-helper`)뿐이므로 **D16 구현에서 작성**한다(요구 있는 항목만, 장문 금지).

**README 필수 항목(최소)**:
1. 설치: `python -m venv .venv` 후 `pip install -r requirements.txt` (Python 버전은 개발 환경 실제 값을 구현 시 확인해 기재).
2. `.env` 준비: `.env.example` 복사 → 키 채움. 키 목록·용도: `DATA_GO_KR_KEY`(금융위 주가·재무), `DART_API_KEY`(DART 기업정보·공시), `GMS_API_KEY`·`GMS_BASE_URL`·`GMS_MODEL`(LLM), `REPLAN_MAX`(1~3). `.env`는 커밋 금지(.gitignore 확인됨).
3. **산업 캐시 구축(앱 실행 전 1회)**: `python -m backend.identity.build` (D2 §수정요청 4). DART 키 필요, 중단 후 재실행 재개 가능, `--force/--refresh` 옵션은 build.py 실제 인자를 확인해 기재. 미구축이면 앱은 뜨지만 산업 질의가 `INDUSTRY_NOT_READY`류 안내로 끝남(로그 한 줄 안내가 이미 있음).
4. **산업명 사전 채우기**: `backend/identity/data/industry_terms.json`(현재 빈 배열) 형식·채우는 법(§5-3).
5. 실행: **저장소 루트에서** `python -m uvicorn backend.main:app --host 127.0.0.1 --port 8000` → 브라우저 `http://127.0.0.1:8000/`(채팅), `/reports.html`(리포트). 인증이 없으므로 `0.0.0.0` 바인드 금지를 한 줄 명시. 루트에서 실행해야 하는 이유: `config.DB_PATH="invest_helper.db"`가 **상대 경로(cwd 기준)** 이기 때문(다른 cwd에서 실행하면 빈 DB가 새로 생김 → 구축한 캐시를 못 찾음). 아래 테스트가 이 전제를 고정한다.
6. 테스트: `python -m pytest` (JS 단위는 node가 있을 때만 실행되고 없으면 skip — 명시).
7. 외부 API 출처·이용조건: data.go.kr 금융위원회 `GetStockSecuritiesInfoService_V2`(주가)·`getSummFinaStat_V2`(재무), OpenDART(`corpCode.xml`·`company.json`·`list.json`), SSAFY GMS(OpenAI 호환). **이용조건·호출 한도 문구는 각 서비스 페이지 확인 후 기재 — 확인 못 한 항목은 "미확인"으로 적는다**(지어내지 않음).
8. 구현 범위: DESIGN §2·§12의 제외 항목 요약(점수화 추천·산업 전망·세션 영속화·Forward PER·F3022 미구현 등), 세션 메모리는 서버 재시작 시 소실.
9. 면책: 투자 권유 아님.

**자동 점검(테스트)** — `tests/test_d16_ready.py`:
- `README.md`에 위 5개 키워드(`pip install`, `.env`, `backend.identity.build`, `uvicorn backend.main:app`, `127.0.0.1`)가 존재, 문서 내 `0.0.0.0` 바인드 권장 문구 부재.
- `.env.example`의 키 집합 = `config.Settings` 필드명 집합(+REPLAN_MAX) 일치, `.env.example` 값에 시크릿처럼 보이는 비어 있지 않은 키 값 없음(`*_KEY=` 뒤 빈 값).
- `.gitignore`에 `.env`·`*.db` 포함, `git ls-files`에 `.env` 미포함(git 없으면 skip).
- 기동 스모크 3종(§5-2).

## 4. 사용자 승인 대기 목록 (DESIGN.md / D0 / D1 수정 제안 일괄 — **직접 반영 금지**)

범례: 출처 = 각 단위 설계의 요청 번호. 불승인이어도 구현은 이미 완료·동작하므로 **문서 동기화만** 달라진다. 이미 승인·반영된 항목(D0 §"DESIGN.md 수정 제안" 1~13의 `[승인됨]`/반영 완료분, DESIGN §4의 공시 judge-끝 문구, D9 협약)은 제외했다. 표본 대조: D0 항목 1·3·6·8·10·13의 문구가 현재 DESIGN.md(§1 config 줄, §3 표·입력, §4 line 82, §6 line 138)에 존재함을 확인함 → 구현 시 나머지도 `grep`으로 재확인.

### A. DESIGN.md §1 구조·의존성

| ID | 제안 | 출처 | 위험 |
|---|---|---|---|
| P-1 | 트리 `db/sqlite.py` 주석을 "reports (run_logs DDL·함수는 `events/logger_agent.py`)"로 수정 | D12-1, D13-1 | 문구만 |
| P-2 | `events/`에 `stream.py`(세션별 팬아웃 collect/open_stream/sse_format) 추가 | D11-1, D13-2 | 문구만 |
| P-3 | `identity/` 패키지(`models/store/dart/resolve/build.py`, `data/industry_terms.json`) 추가 | D2-4, D13-3 | 문구만 |
| P-4 | `agent/llm.py`(analyze·respond 공유 LLM 호출, `response_format` JSON 모드) 및 `agent/nodes/replan.py`(D9 래퍼 `decide_outcome`) 트리·§4 표 반영 | D7-4, D9 §3 | 문구만 (현 트리에 없음) |
| P-5 | 의존성 `defusedxml`, `uvicorn`(+ README 'build CLI를 앱 실행 전 1회' 단계) | D2-4, D13-3 | 없음(requirements에 이미 있음) |
| P-6 | `config.py` 주석: `DB_PATH`·`TOOL_TIMEOUT_S`는 코드 상수(.env 미사용). `.env.example`에 둘을 추가할지는 별도 판단 | D0 §13, D13-4, D1-1 | 추가 시 `config.py`가 .env를 읽도록 코드 변경 필요 → 기본안은 "주석만" |

### B. DESIGN.md §3·§4·§7 본문

| ID | 제안 | 출처 |
|---|---|---|
| P-7 | §3 line 60 "재무는 확정 분기 기준" → "확정 **사업연도** 기준, `actual_basis`와 `requested_basis`의 연도가 다르면 `estimated=true`"(실호출 결과: 연간만 제공) | D4-1 |
| P-8 | §4 노드 표 plan 행: "규칙 기반 최소 step(후보별 전개는 execute), `describe()` 미사용" | D7-4, D7-6 |
| P-9 | §4 노드 표 respond 행: "LLM은 요약 문장 1회(실패 시 코드 템플릿), 면책 코드 부착; chitchat/asked/error는 코드 템플릿" | D9-5 |
| P-10 | §7: "세션 수 상한(LRU)·TTL 없음, `memory.run_turn`이 요청 단위 state 생성·갱신 소유" | D10-2 |

### C. DESIGN.md §9 API 명세

| ID | 제안 | 출처 |
|---|---|---|
| P-11 | `POST /api/reports` 성공 **201**; 오류 `code` 목록: `REPORT_NOT_FOUND`·`DRAFT_NOT_FOUND`·`INVALID_REQUEST`(422)·`INTERNAL_ERROR`(500)·`SESSION_BUSY`(409)·`SESSION_NOT_FOUND`(404)·`NOT_FOUND`·`METHOD_NOT_ALLOWED`·`HTTP_ERROR` | D12-2, D13-5 |
| P-12 | `POST /api/chat`: `session_id`=UUID(서버가 소문자 정규화), `message` 1~2000자, 같은 세션 동시 요청은 409 `SESSION_BUSY`; 응답 항목 형태(`verdicts/steps/tool_calls/replans`)는 D13 §5-1로 확정, **추가 필드 `missing_api_key: bool`, `saveable: [{stock_code, stock_name}]`**(DESIGN §9 "키 누락 안내"·"저장" 화면 요구의 수단) | D13-6 |
| P-13 | `GET /api/logs/{session_id}`: 이벤트에 `run`(정수) 추가, 정렬은 "seq 순"→**저장 순(id 순)**(재시작 시 seq 리셋 보완), 없는 세션 404, `DELETE`는 멱등 204 | D13-7 |
| P-14 | (선택, **기본 미채택**) SSE `GET /api/chat/stream/{session_id}` — 채택 시에만 §9에 추가. 현재 `stream.open_stream` 코드만 존재, 라우트 없음 | D11-2, D13-8 |

### D. D0 / D1 문서 보강 (계약 변경 아님·문구 확정 위주)

| ID | 제안 | 출처 |
|---|---|---|
| P-15 | D0 §6-5: "analyze 실패(`intent` 미설정)로 끝난 요청은 메모리를 갱신하지 않는다"; D0 §4: "초기 state에 `conditions`는 항상 존재" | D10-1·2 |
| P-16 | D0 §6-6: "Tool 전에 `resolve_industry`가 `unknown/ambiguous`이면 `industry=None, missing=[industry]`로 asked" (구현은 이미 이 동작) | D2-2, D3-1, D7-2 |
| P-17 | D0 §6-8/9: `resolve_stock`의 `IdentityError`(캐시 미준비)도 `error`로 종료한다는 문장 | D2-3 |
| P-18 | D0 §4 `extra_industries` 원소 형식("KSIC 접두 숫자 문자열")과 채우는 주체(D9 판정 후 D7 plan이 `similar_industries`로 채움) 명시 | D2-1 |
| P-19 | D0 §1 `estimated`: "문자열에서 첫 4자리 연도를 추출해 비교(D4), 일 단위 기준일 보정(D3)은 `estimated=False`+`actual_basis`만 채움" 한 줄 | D3-2, D4-2 |
| P-20 | D0 §12-③ "D3 최대 상한"=`stock_candidates.MAX_LIMIT`, D9가 import | D3-4 |
| P-21 | D0 §6-11 확정 기록(공시: PASS만·judge 끝·NO_RESULT=키 없음·그 외 실패는 종목별 오류·status 불변·MISSING_API_KEY는 규칙 1) 및 `errors`·`target_stock` 주석((tool,stock_code)별 최신, 후보 실패는 `tool="find_stock_candidates"`, resolve 실패 레코드 규약, `target_stock`에 해석된 6자리 코드 덮어씀) | D8-2·3 |
| P-22 | D1 §2 예시 `requires_keys`: 도구별 실제 사용 키만 선언(D4=`DATA_GO_KR_KEY`, D5=`DART_API_KEY`); D1에 `TOOL_TIMEOUT_S` 위치(`config.py` 모듈 상수), `cache_key/is_cacheable` 위치(`tools/base.py`), 키 필드 `repr=False` 명시; D1 §4 "plan이 `describe()`를 보고 PlanStep을 만든다" → "plan은 규칙 기반, `describe()` 미사용" | D1-1~3, D4-3, D5-3, D7-6 |
| P-23 | D0 `ReasonCode` 추가 후보(**사용자 판단, 기본은 현행 유지**): `MISSING_SOURCE_VALUE`(현재 `NO_FINANCIALS` 재사용), `INVALID_CONSTRAINT`(현재 `NO_FINANCIALS`로 대체); DEBT_RATIO equity≤0 → `NEGATIVE_EQUITY`는 DESIGN §5에 없는 D6 추가 규칙이므로 승인 필요 | D6-1·4·5 |
| P-24 | n=1~2 표본의 `PER<=산업평균`을 `INDUSTRY_AVG_UNAVAILABLE`로 취급할지(**사용자 판단, 기본은 현행: 판정하되 "표본 부족" 표기**) | D6-2 |
| P-25 | lookup 턴의 공시 의도 지원 여부(`AgentState.lookup_disclosures` 신설 필요) — **기본 미지원(현행)** | D7-3 |
| P-26 | DESIGN §4/§9 표시 범위: 시총 기준일·연결/별도 구분 노출 여부(**기본 미노출**), `ReportSummary`에 `memo` 포함 여부(**기본 미포함**) | D4-4, D15 참고 |
| P-27 | DESIGN §10-2 문구: "3회 후 되묻기" → "효과 있는 단계만 세어 최대 `REPLAN_MAX`회(시장 미지정이면 2회)" 정합(D0 승인 5 이미 반영된 동작의 DESIGN 문구 일치; 선택) | D0 승인 5, 본 문서 §2-3 |

승인 요청 방식 제안: 사용자가 ID 단위로 "승인/보류/불승인"을 정하면 별도 문서 동기화 단위(코드 변경 없음)로 일괄 반영. P-14·P-23~P-26은 **기본값(현행 유지)** 이므로 아무 말 없으면 변경하지 않는다.

## 5. 실호출 전 체크리스트와 자동 검증

### 5-1. 사람이 하는 체크리스트 (실호출은 사용자 실행; 이 단위는 하지 않음)

**선결(실호출 전)**
- [ ] `.env` 점검: 이 머신 `.env`에는 **이미 값이 들어 있다**(gitignore 대상, 비추적). "키 없음" 가정과 다르므로 테스트가 `settings`를 반드시 패치하고 네트워크 가드를 쓰는지(§2-1) 확인. 키 값은 문서·로그·이슈에 옮기지 않는다. 키 누락 경로 확인은 `Settings` 패치로만.
- [ ] **`industry_terms.json` 채우기**(현재 `[]`): 지금 상태에서는 숫자 접두를 직접 입력하지 않는 한 모든 산업명이 `unknown` → 모든 `screen`이 되묻기로 끝난다(**실사용 차단 항목**). 채우는 법: ① `company.json` 실응답 1건과 구축된 캐시의 `induty_code` 자릿수·차수 확인(D2 A1~A8), ② KSIC 표(출처·이용 조건 확인)에서 대표 용어→접두 목록 작성, 최소 `반도체`·`은행`(DESIGN §10 대표 요청용), ③ `resolve._terms()` 검증(길이≥2·중복 불가) 통과 확인, ④ 각 term의 `n_listed`가 0이 아닌지 구축 후 확인. **접두값 자체는 실데이터 확인 전 가정**(예: 반도체 261, 은행 6412 — 확인 전 확정 금지).
- [ ] `python -m backend.identity.build` 실행 → `cache_status()["complete"]` true, 실패·pending 비율(5% 가정 적정성), 소요 시간·DART 일일 한도.
- [ ] requirements 완결성(§5-2 자동 테스트 통과) + `pytest` 전체 통과(현재 314개 + D16 신규).

**실호출 확인 항목(각 단위 §구현 시 확인 항목의 합집합)**
- D2: A1~A8(corpCode zip/XML 구조, `company.json` 필드·`induty_code` 차수, `jurir_no` 부재 종목, 호출 한도, 우선주 corpCode), `COVERAGE_MISS_DENOM`·`COMMIT_EVERY`·zip/XML 상한, KSIC 표 출처·이용 조건, LIKE 인덱스 사용(`EXPLAIN QUERY PLAN`).
- D3: G1~G7(`basDt` 전체 일자 페이지 조회 가능 여부, `numOfRows` 상한, 응답 변형(단건 dict·0건 `""`), 한도·키 오류 `resultCode`, 휴장일, 숫자 문자열, 우선주·ETF 혼입), `mrktCtg` 값 집합(`KOSDAQ GLOBAL`/`KONEX`), 상수 `DEFAULT_LIMIT/MAX_LIMIT/PAGE/LOOKBACK_DAYS/timeout`. G1 불가 시 D3 재설계(후보별 조회 폭증).
- D4: H1~H7(재무 `bizYear` 소급, `likeSrtnCd` 지원, 0건/단건 형태, `resultCode` 값, 금융업(은행) 필드 구성 — DESIGN §10-2 시나리오가 은행), `fnclDebtRto`가 연결·별도 행 모두에 있는지, 호출 한도, `register(timeout_s)` 상향 필요 여부.
- D5: G1~G4(뷰어 URL 열림, `end_de`·`page_count` 최대·정렬, `bgn_de` 범위 제한, 키·한도 status, 정정공시 접두, 0건 013 vs 빈 list), 조회 기간 180일·`TITLE_MAX` 적정성.
- D7: GMS Anthropic `system` 필드 전달·JSON_HINT 준수 여부(D7c), `claude-haiku-5-5` 지연·한도, 의도 분류 정확도(refine vs screen, 숫자만/종목명만 응답), `LLM_TIMEOUT_S` 실측.
- D8: `FIN_CONCURRENCY`(금융위 한도), 한 라운드 전체 소요, PASS 100건 시 공시 호출 수 상한 필요 여부.
- D9: `MAX_ROWS/MAX_EXCLUDED/TOP_N/SUMMARY_MAX`, 요약 숫자 검증 오탐 정도, `limit_up` 효과 없는 소진 1회, `RECURSION_LIMIT=40`(재계획 최대 회전 시 미초과 — 모의로는 확인됨).
- D10~D13: 세션 동시 요청·`database is locked` 빈도(busy timeout 공통화), 스레드 발행 순서, `run_agent`의 요청당 `build_graph()` 컴파일 비용, 취소 시 `final` 합성 발행, `/api/chat` 총 소요(재계획 포함)가 브라우저/프록시 타임아웃 이내인지.
- D12·D15: `base_date` 형식 혼재(screen vs lookup)로 인한 중복 판정 어긋남, `industry_avg`·`reasons` 직렬화(NaN/inf), 공시 URL 스킴.
- D14: 브라우저 수동 시나리오 9항(D14 §11), `crypto.randomUUID` secure context, 전각폭 표 정렬. D15: 수동 시나리오 8항(D15 §12).
- D16(본 단위): README 실행 절차를 **깨끗한 새 clone**에서 처음부터 따라 해 보기(설치→`.env`→build→기동→대표 요청 5개 수동 입력).

### 5-2. 코드로 자동 검증 (`tests/test_d16_ready.py`, 네트워크·키 불필요, 외부 상태 의존 금지)

1. **requirements 핀 일치**: `requirements.txt`의 `name==ver` 전 줄을 파싱해 `importlib.metadata.version(name)`과 비교(모두 `==` 핀인지, 현재 환경 설치본과 일치하는지). 실패 메시지에 불일치 목록 출력.
2. **requirements 완결성**: `backend/`·`scripts/`·`tests/`(+`conftest`)의 모든 `.py`를 `ast`로 파싱해 최상위 import 모듈명을 수집 → `sys.stdlib_module_names`·로컬 패키지(`backend`, `tests`, `scripts`)를 제외한 나머지가 requirements의 배포명(정규화 매핑: `dotenv→python-dotenv`, `yaml` 등 필요 시 소규모 사전)에 모두 존재. 반대로 requirements에 있으나 어디서도 import되지 않는 항목은 **경고만**(fastapi가 쓰는 `starlette`, TestClient용 `httpx` 등 간접 의존이 있어 실패로 삼지 않음). `respx`·`pytest-asyncio` 등 현재 미사용 패키지를 테스트가 import하지 않는지는 이 검사가 잡는다.
3. **import 스모크**: `backend/` 하위 모든 모듈을 `pkgutil.walk_packages`로 `importlib.import_module`(예외 0) + 별도 서브프로세스 `python -c "import backend.main"`(cwd=저장소 루트, 환경변수 `DATA_GO_KR_KEY=""` 등 3개 키를 **빈 값으로 명시**해 `.env` 실제 키가 자식 프로세스로 흘러들지 않게 — `load_dotenv`는 이미 존재하는 환경변수를 덮어쓰지 않음). import 시점에 키 필수·네트워크·파일 생성 부작용이 없음을 고정.
4. **앱 기동 스모크(in-process)**: tmp `DB_PATH`·빈 `Settings`·빈 identity 캐시로 `with TestClient(app)` → lifespan 성공, `GET /api/reports`=200 `[]`, `GET /api/logs`=200 `[]`, `POST /api/chat`(analyze LLM mock 없이 `llm.complete`가 `MISSING_API_KEY`) → 200 `status=error`·`missing_api_key=true`. 키·캐시 없이 기동되는 D0·D13 계약을 앱 레벨에서 재확인.
5. **uvicorn 명령 스모크(서브프로세스)**: README의 기동 명령 그대로(`sys.executable -m uvicorn backend.main:app --host 127.0.0.1 --port <빈 포트>`), cwd=저장소 루트가 아니라 **tmp 디렉터리 + `PYTHONPATH=저장소 루트`**(저장소의 `invest_helper.db` 오염 방지·`DB_PATH` 상대경로 특성 확인), env=빈 키 3개. `/api/reports`가 200이 될 때까지 최대 15초 폴링, `/`가 200이고 CSP 헤더 존재, 종료는 `terminate()`(Windows 포함, `wait(5)` 후 `kill`). 포트는 `socket.bind(("127.0.0.1",0))`로 확보. 15초 내 미기동이면 stderr 마지막 줄을 포함해 실패. 느린 환경 대비 `pytest.mark.skipif(os.getenv("SKIP_BOOT_SMOKE"))` 한 줄만 허용.
6. **DB_PATH 전제 고정**: `config.DB_PATH`가 상대 경로(`not os.path.isabs`)임을 단언하는 테스트 1개 — README의 "루트에서 실행" 문구와 짝. (절대경로로 바꾸는 개선은 상위 변경이므로 하지 않음; 바뀌면 이 테스트와 README를 함께 갱신.)
7. **terms 파일 형식**: `industry_terms.json`이 JSON 배열이고 `resolve._terms()`가 예외 없이 로드(빈 배열 허용). **대표 용어 존재 단언(`반도체`·`은행`이 `resolve_industry` ok)은 `xfail(strict=False)`** 로 두어 사용자가 §5-1에서 파일을 채우면 자동으로 통과(XPASS)하게 한다 — 채워지지 않은 현재 상태를 실패로 만들지 않되 보이게 한다.
8. **문서 정합 스모크**: `docs/design/D0..D16-*.md` 파일 존재, D16 §4 ID(P-1~P-27)가 D13 §11·D12 §10·D11 §7 등 출처 문서의 요청 번호와 모순되지 않음은 **수동 검토**(자동화하지 않음).

## 6. 테스트 관점 (요약)

- `tests/test_d16_e2e.py`: §2 전부. 시나리오 1·1b·2·2b·3·4·5·5b + reports/logs 연결 + 키 누락 2종. 모두 `TestClient`·tmp DB·`asyncio` 불필요(동기 TestClient). 시나리오당 새 UUID, `memory.clear_all()`는 fixture에서.
- `tests/test_d16_ready.py`: §5-2 1~7.
- 기존 314개 테스트 무수정 통과(회귀). D16 구현은 테스트·README 추가뿐이므로 `backend/`·`frontend/` diff는 0이어야 한다(`git diff --stat -- backend frontend` 비어 있음을 구현 보고에 첨부).
- 실행 증거 원칙: 구현 보고는 `pytest -q` 전체 결과(통과 수)와 위 diff 확인을 제시하고, 실행하지 않은 것(실호출·브라우저 수동 시나리오·node 미설치 시 JS 테스트)은 "미확인"으로 표기.

## 7. 구현 시 확인 항목 (FAIL 아님)

- E2E 시임 응답의 정확한 원시 형태(헬퍼 복사 시 `test_d3/d4/d5` 현재 구현과 일치), `similar_industries("은행")`가 픽스처의 6419 종목을 반환하도록 terms·`induty_code` 구성(접두 겹침 규칙은 D2 §유사 업종: 합산 상위 5·L≤2) — 2의 `similar_industry` 단계가 "효과 있음"으로 판정되려면 필요. 어긋나면 `limit_up`만 도는 변형으로 기대표를 구현자가 조정(근거를 주석에 남김).
- 표 문구 단언은 부분 문자열만(전각폭·반올림 표기 `10.00%` 등 D9 §11 임의 상수에 의존하지 않게). 숫자 경계 값은 판정 경계에서 떨어진 값을 쓴다(위 픽스처가 그렇게 구성됨).
- `E2E_SECRET_KEY`가 `tool_call.input`·이벤트에 들어가는 경로가 있으면 D8 버그(D13 §12) — 발견 시 해당 단위 결함으로 보고.
- `TestClient` 동기 호출이 이벤트 루프를 요청마다 새로 만들므로 `build_graph()` 요청당 컴파일·`asyncio.Lock` 동작이 달라 보일 수 있음. 동시성(409)은 D13 테스트가 이미 다룸 — D16은 반복하지 않는다.
- 기동 스모크(5-2의 5)의 Windows 프로세스 종료·포트 재사용 이슈는 실행해 보고 조정. 불안정하면 `SKIP_BOOT_SMOKE` 허용 범위 안에서만 완화.
- 2의 요약 LLM: 시나리오 1은 고정 문장(숫자 없음)을 반환해 채택 경로, 시나리오 2·3은 `LlmError`로 템플릿 경로를 타게 해 양쪽을 모두 통과시킨다.
- 임의 상수 없음(폴링 15초·포트 확보 방식은 테스트 내부 값).

## 8. 상위 계약 요청

신규 요청 없음. 이 문서에서 **발견한 사실**(요청이 아니라 사용자 인지용):
1. 로컬 `.env`에 실제 값이 들어 있음(비추적). 테스트·문서·보고에 값을 옮기지 않음. 키 로테이션 필요 여부는 사용자 판단(대화·로그에 노출된 적 있는지에 달림).
2. `industry_terms.json`이 비어 실사용 시 모든 산업 질의가 되묻기로 끝남(§5-1).
3. DESIGN §10-2의 "3회"와 승인된 D0 해석(시장 미지정 시 2회)의 문구 차이(P-27).
4. `config.DB_PATH`가 상대 경로라 실행 위치에 따라 DB가 갈림(§3, 5-2의 6).
