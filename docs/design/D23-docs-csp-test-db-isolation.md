# D23 /docs 렌더 복구(CSP 유지) + 테스트 실DB 격리

상위: DESIGN.md, D0, D13(보안 헤더·정적 서빙), D21(응답 문서화). **DESIGN.md/D0 수정 제안 없음**(DESIGN.md에는 CSP·`/docs` 서술이 없음을 grep으로 확인. D13 §보안 헤더의 CSP 문자열은 본 단위에서 그대로 유지). 정적 분석만 수행했고 서버·pytest는 실행하지 않았다.

## 0. 범위
- (A) `/docs`(Swagger UI)가 CSP(`script-src 'self'`)에 막혀 빈 화면. 체크리스트 21번이 `/docs` 화면 대조를 요구하므로 실제 렌더 필요.
- (B) pytest가 실DB(`invest_helper.db`)에 `run_logs` 행을 INSERT.
- 비범위: API 응답·라우트 동작, 에이전트 로직, D0 계약, requirements.txt(변경 없음).

## 1. (A) /docs

### 1.1 원인
`backend/main.py` `_HEADERS`의 CSP(`default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; frame-ancestors 'none'`)가 모든 응답(미들웨어)에 붙는다. FastAPI 기본 `/docs` HTML은 ① jsdelivr CDN의 `swagger-ui-bundle.js`·css ② 인라인 `<script>`(SwaggerUIBundle 초기화)를 쓴다. ①②가 모두 `script-src 'self'` 위반. `/redoc`도 CDN 스크립트·폰트를 써 같은 이유로 빈 화면.

### 1.2 비교
| 항목 | 경로별 CSP 완화(`/docs`·`/redoc`만) | **로컬 서빙(채택)** |
|---|---|---|
| 새 pip 패키지 | 없음 | 없음(정적 파일 복사만) |
| 오프라인 | 불가(CDN 의존) | 가능 |
| CSP | `/docs`에 `https://cdn.jsdelivr.net`+인라인 허용(`'unsafe-inline'` 또는 해시: 해시는 FastAPI 버전마다 깨짐) → 해당 경로만이라도 약화 | **전 경로 CSP 현행 그대로** |
| 코드 | 미들웨어에 경로 분기 추가(헤더 이중 정의) | 라우트 1개 + 정적 파일 3개 |
| 공급망 | 런타임에 외부 스크립트 실행 | 고정 버전 파일을 저장소에 포함 |
| 단점 | 보안 규칙 예외가 생김 | 저장소에 swagger-ui-dist 파일 약 1.5MB 추가 |

선택 근거: "보안 약화 금지"와 오프라인 동작(심사·시연 환경)을 모두 만족하고, 미들웨어·`_HEADERS`를 건드리지 않아 D13 보안 테스트가 불변이다. 비용은 파일 복사 한 번.

### 1.3 설계
1. `FastAPI(lifespan=lifespan, docs_url=None, redoc_url=None)`. 기본 `/docs`·`/redoc`·`/docs/oauth2-redirect`를 끈다. `/openapi.json`은 유지(`openapi_url` 기본값).
2. 정적 파일 `frontend/vendor/swagger/`에 고정 버전 swagger-ui-dist 3개를 둔다.
   - `swagger-ui-bundle.js`, `swagger-ui.css` (npm `swagger-ui-dist`의 동명 파일, 버전·출처·라이선스(Apache-2.0)를 같은 폴더 `README.txt`에 기록)
   - `docs-init.js`: `window.ui = SwaggerUIBundle({url: "/openapi.json", dom_id: "#swagger-ui"});` 한 줄(인라인 스크립트 대체)
3. `/docs` 라우트: `@app.get("/docs", include_in_schema=False)`가 **고정 HTML 문자열**(`<!doctype html>`, `<link rel=stylesheet href="/vendor/swagger/swagger-ui.css">`, `<div id=swagger-ui>`, `<script src="/vendor/swagger/swagger-ui-bundle.js">`, `<script src="/vendor/swagger/docs-init.js">`)을 `HTMLResponse`로 반환. 사용자 입력 반영 없음. 라우트는 `app.mount("/")`보다 먼저 등록한다(기존 `include_router` 순서와 같은 위치). `FRONTEND` 디렉터리 존재와 무관하게 동작하려면 HTML은 코드 문자열, vendor 파일만 `FRONTEND/vendor/…`에서 서빙된다(마운트 의존). 마운트가 없는 환경(`FRONTEND` 없음)에서는 `/docs`가 빈 화면이 되므로 허용(운영은 항상 존재; 테스트는 §3 참고).
4. `/redoc`: **제공하지 않는다**(명세·체크리스트에 없고 CDN 의존). 호출 시 기존 404 JSON.
5. 보안 헤더: 응답 헤더는 미들웨어가 그대로 붙인다(CSP·nosniff·`frame-ancestors 'none'`). 신규 경로는 모두 `'self'` 자원이라 완화 불필요. X-Frame-Options는 애초에 쓰지 않으며(`frame-ancestors 'none'`로 대체) 영향 없음. CORS 헤더 없음 불변.
6. 문서 갱신(구현 단계에서 함께, 설계 의무 아님): README §8·체크리스트 18·21번 비고의 "/docs 빈 화면" 문구를 제거하고 `/docs` 대조 증빙으로 교체, 캡처 1장 추가.

### 1.4 영향 범위
`backend/main.py`(생성자 인자, 라우트 1개, 상수 1개), `frontend/vendor/swagger/*`(신규), README §8, 체크리스트 비고. 다른 API·CSP·테스트 로직 불변. D13 §"frontend/ 밖 경로 404" 조건은 `/docs` 단일 예외 추가(허용 목록: 정적, `/api/*`, `/openapi.json`, `/docs`).

## 2. (B) 테스트 실DB 오염

### 2.1 근본 원인(정적 확인)
- `config.DB_PATH = "invest_helper.db"`(상대경로, 모듈 속성). `logger_agent._exec`는 호출 때마다 `config.DB_PATH`를 읽는다. `logger_agent.install()`은 모듈 전역 `_installed=True`로 만들고 `bus.subscribe(record)`를 **전역 `bus._subscribers` 리스트**에 추가한다.
- 대부분의 TestClient 테스트(d11·d13·d14·d15·d16_e2e·d16_ready)는 `_subscribers`·`_installed`를 monkeypatch하지만 **`tests/test_d21.py`의 `client` fixture는 `DB_PATH`만 monkeypatch하고 `bus._subscribers`·`la._installed`는 하지 않는다**(66~80행). 따라서 `with TestClient(main.app)`의 lifespan이 `la.record`를 진짜 전역 리스트에 영구 구독시키고 `_installed=True`로 남긴다(복원되지 않음).
- 이후 실행되는 `test_d2e`/`test_d7`/…는 DB_PATH를 격리하지 않거나(d7 `st()`의 `"s1"`, d18 `sid="s1"`) bus로 stage·llm_decision을 publish하므로, 구독돼 있는 `la.record`가 그 시점의 `config.DB_PATH`(상대경로 → 저장소 루트 실DB)에 INSERT한다. 알파벳 순서상 d21은 d7보다 앞이다. d18은 d21보다 앞이므로 d18 행의 구독 경로는 **미확인**(다른 파일이 같은 방식으로 남겼을 가능성; 해결책은 원인 비의존이라 무관 — §구현 시 확인 항목).
- 추가로 d8·d9·d17_route는 `bus._subscribers.clear()`를 실제 리스트에 호출해 전역을 변형한다.

### 2.2 비교
| 방식 | 장단 |
|---|---|
| `DB_PATH` 환경변수 우선 | 프로덕션 config 변경 필요, 세션 단위 공유라 테스트 간 행 누적, 구독 누수는 못 막음 |
| 개별 테스트에 monkeypatch 추가 | 누락이 재발(이번 사고의 원인) |
| **`tests/conftest.py` autouse(채택)** | 프로덕션 코드 무변경, 모든 테스트 일괄 보호, 구독·전역 상태도 같이 초기화 |

### 2.3 설계: `tests/conftest.py`(신규, 1개)
autouse 함수 스코프 fixture `_isolate_globals(monkeypatch, tmp_path, request)`:
1. `real_db` 마커가 **없으면** `monkeypatch.setattr(config, "DB_PATH", str(tmp_path / "isolated.db"))`. 마커 있으면 건너뛴다(§2.4).
2. 전역 이벤트 상태 초기화(monkeypatch로 복원 보장): `bus._subscribers=[]`, `bus._seq={}`, `stream._sinks={}`, `stream._installed=False`, `logger_agent._installed=False`. 개별 테스트의 같은 monkeypatch는 그대로 두어도 무해(덮어쓰기).
3. `memory.clear_all()`은 conftest에서 하지 않는다(각 테스트 기존 방식 유지). `resolve`의 `lru_cache`는 건드리지 않는다: 이를 쓰는 테스트가 이미 자체 patch/`cache_clear`를 한다(구현 시 확인).
4. 가드(같은 fixture 안, 별도 테스트 파일 불필요): 마커 없을 때 `assert os.path.isabs(config.DB_PATH) and Path(config.DB_PATH).resolve().is_relative_to(tmp_path.resolve())`, 그리고 fixture yield 이후 teardown에서 `not any(f is logger_agent.record for f in bus._subscribers)` 확인은 하지 않는다(정상 테스트가 install한다). 대신 **누수 가드 테스트** `tests/test_d23_isolation.py`:
   - `test_db_path_is_tmp`: `Path(config.DB_PATH)`가 절대경로이고 저장소 루트(`ROOT`) 하위가 아님.
   - `test_real_db_marker_keeps_default` (`@pytest.mark.real_db`): `config.DB_PATH == "invest_helper.db"`(원본값).
   - `test_no_leak_between_tests_a/b`: a에서 `la.install()`+publish 후, b에서 `bus._subscribers == []`·`la._installed is False` 단언(순서 의존이므로 같은 클래스 내 두 테스트로 작성, 실패해도 실DB는 tmp라 안전).
   - 실DB 행 수·mtime 비교는 사용자 서버와 경합하므로 하지 않는다.
5. `pytest.ini`(또는 conftest의 `pytest_configure`)에서 마커 등록: `real_db`(경고 방지). `pytest_configure`로 `config.addinivalue_line("markers", "real_db: …")` 한 줄(파일 추가 없음).

### 2.4 실DB를 의도적으로 읽는 테스트
- `test_d16_ready.py`:
  - 180행 `assert not os.path.isabs(config.DB_PATH)`는 conftest 하에서 깨진다 → 이 테스트에 `@pytest.mark.real_db` 부여(기본값 상대경로 계약 확인이 목적). 같은 파일 174행 `(tmp_path/"invest_helper.db")`는 subprocess cwd=tmp_path 방식이라 영향 없음.
  - 227행 `test_terms_prefixes_have_listed_companies_real_db`(INVEST_REAL_DB=1): `real_db` 마커 부여(실DB 읽기 전용). 이 경우 `real_terms` fixture가 실DB를 읽으므로 DB_PATH 패치 제외 필요.
  - `repo_db_state()` 비교 테스트들은 자체 monkeypatch + 실DB 파일 상태 전/후 동일 단언이라 그대로 동작.
- `test_d16_ready.py` 98·148행의 subprocess(`import backend.main`, uvicorn)는 별개 프로세스라 in-process 패치가 미치지 않는다. 148행 서버는 cwd를 확인(구현 시 확인)해 저장소 루트이면 tmp cwd로 바꾼다.
- 실DB를 읽는 resolve/identity 테스트(예: 패치 없이 `resolve.*` 호출)가 있으면 해당 테스트에 `real_db` 마커 또는 자체 tmp 캐시 구성으로 보정. 목록은 구현 시 `pytest`로 FAIL 항목을 수집해 확정(정적으로는 d2·d4·d2e가 DB_PATH를 직접 patch하므로 영향 없음으로 판단).

### 2.5 기존 테스트 보정 목록(코드 확인 결과)
- 보정 불필요(이미 자체 격리, conftest와 중복 무해): d11, d12, d13, d14, d15, d16_e2e, d2, d2e, d4.
- 마커 부여: d16_ready의 `test_...isabs...`(180행 해당 테스트), `..._real_db`(227행).
- 격리 없이 publish: d7(72행 `st`, fixture `env`), d18 → conftest만으로 보호됨(코드 수정 불필요).
- d21 `client` fixture: 전역 구독 누수 원인. conftest가 상쇄하나 일관성 위해 같은 두 줄(`_subscribers`, `_installed`) 추가는 선택(필수 아님).
- d8·d9·d17_route의 `bus._subscribers.clear()`는 conftest가 테스트마다 새 리스트를 주므로 무해.

## 3. 시나리오
1. **/docs 렌더**: 서버 기동 후 `/docs` 접속 → Swagger UI가 렌더되고 `POST /api/chat`·`/api/reports`·`/api/logs` 목록과 D21 응답 스키마가 보인다. 콘솔 CSP 위반 0건, 외부 도메인 요청 0건(네트워크 탭 전부 same-origin), 네트워크 차단 상태에서도 렌더.
2. **다른 경로 CSP 불변**: `/`, `/api/reports`, `/api/chat` 오류 응답, 404 JSON, `/docs`, `/vendor/swagger/swagger-ui-bundle.js`의 응답 헤더가 모두 `_HEADERS`와 동일(`script-src 'self'`, `frame-ancestors 'none'`, nosniff). `/redoc`은 404 JSON, `/docs/oauth2-redirect`도 404.
3. **저장소 루트에서 pytest 실행 후 실DB 불변**: 격리 전 DB 사본 해시(사용자 서버 미기동 상태에서 수동 확인) 후 `pytest` 전체 실행 → `SELECT COUNT(*) FROM run_logs`, 파일 mtime 불변. 자동 가드는 `test_db_path_is_tmp`·누수 가드 테스트.
4. **마커 예외**: `INVEST_REAL_DB=1`에서 real_db 마커 테스트만 실DB를 읽기 전용으로 사용, 나머지는 tmp.

## 4. 테스트 방법
- (A) `tests/test_d23_docs.py`(신규, `FRONTEND`를 실제 `frontend/`로 두고 TestClient): `/docs` 200·`text/html`, 본문에 `<script>`가 모두 `src="/vendor/swagger/…"`이고 인라인 스크립트·`http`로 시작하는 외부 URL 없음, `/vendor/swagger/swagger-ui-bundle.js`·`swagger-ui.css`·`docs-init.js` 200 및 content-type(`javascript`/`css`), 위 시나리오 2의 경로별 헤더 단언, `/redoc` 404, `app.openapi()`에 `/docs`가 없음(`include_in_schema=False`). 기존 d21 `test_openapi_matches_spec`은 불변이어야 한다.
- (A) 실브라우저(수동, 증빙): `/docs` 캡처 1장, 콘솔 CSP 오류 없음.
- (B) `tests/test_d23_isolation.py`(§2.3 4) + 전체 `pytest` 통과(기존 대비 FAIL 0).

## 5. 구현 시 확인 항목
- swagger-ui-dist 버전 선택·다운로드(네트워크 필요 → 구현 에이전트가 오케스트레이터에 허용 요청). 정적 파일 content-type(.js를 `text/javascript`로 매핑하는 기존 `mimetypes.add_type` 확인).
- Swagger CSS의 `data:` 배경 이미지(아이콘)가 `default-src 'self'`로 일부 안 보일 수 있음. 기능에는 영향 없으면 허용(CSP 완화 안 함).
- `/docs`가 `FRONTEND` 마운트에 의존하는 점(`vendor` 서빙)과 d13 테스트의 `FRONTEND=nofront` 조합에서 `/docs` 단언이 필요하면 fixture에서 `FRONTEND`를 실제 경로로.
- d18 `s1` 행이 어느 선행 테스트의 구독 누수에서 왔는지(d21 외) 확인. conftest 적용 후 `bus._subscribers`·`config.DB_PATH`를 로깅해 재현 불가를 확인.
- d16_ready 148행 서버 프로세스 cwd가 저장소 루트인지, resolve `lru_cache`·identity 캐시를 패치 없이 읽는 테스트가 conftest 후 FAIL하는지(전체 pytest로 수집).
- 실DB는 구현 중에도 읽기 전용 확인만(행 수 비교는 서버 미기동 시에만).
