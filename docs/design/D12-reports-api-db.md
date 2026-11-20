# D12 reports API + DB (`backend/db/sqlite.py`, `backend/api/reports.py`)

기준: DESIGN §1·§8·§9, D0 §4·§6-9, D9 §5-8, D10 §5, D11 §3(DB 소유 방식), 구현 코드 `respond.py`(`_drafts_base`)·`state.MetricRow`·`config.DB_PATH`. 의사코드이며 구현 아님. DESIGN·D0는 수정하지 않고 요청만 올린다.

## 1. 목적·범위

| 항목 | D12 소관 | 비소관 |
|---|---|---|
| `report_draft` 스키마 확정 | 확정(§2). D9 `respond.py`가 만드는 키 6개를 **그대로 수용**(코드 변경 없음) | draft 생성(D9), 세션 보관(D10) |
| `reports` 테이블 DDL, CRUD 함수 | D12 소유(§3~4) | `run_logs`(D11) |
| 라우트 `/api/reports*`, 오류 응답 | D12(§6) | `/api/chat`·`/api/logs`(D13), 앱 조립·라우터 등록(D13) |
| 화면 | – | 목록·수정·삭제 렌더 D15, 채팅의 "저장" 버튼 연결 D14 |

저장은 Tool이 아니라 API가 직접 수행(DESIGN §8). 채팅 `report_save` 의도는 안내문만 만들고(D0 §6-9) 실제 저장은 `POST /api/reports`다.

## 2. `report_draft` 스키마 (확정)

세션 메모리의 `report_draft` = `{종목코드(6자리 str): Draft}`. D9가 이미 만드는 형태와 동일하다.

```
Draft = {
  stock_code:   str            # 6자리
  stock_name:   str            # 표시명(조회 실패 시 코드 문자열일 수 있음)
  base_date:    str | None     # 시세 기준일 "YYYY-MM-DD" 또는 None(lookup은 MetricRow.actual_basis, 값 형식은 D9 산출 그대로)
  metrics:      MetricRow      # {values{지표:float|None}, fiscal_period, actual_basis, estimated, reasons{지표:사유코드}}
  industry_avg: dict           # screen: {"PER":{"value":float|None,"n":int},...}, lookup: {}
  disclosures:  list[dict] | None   # [{title,date,url}]; None = 조회 안 함
}
```

- 키 6개는 `reports` 컬럼과 1:1(`metrics`→`metrics_json`, `industry_avg`→`industry_avg_json`, `disclosures`→`disclosures_json`). 정합 조정 불필요, D9 변경 요청 없음.
- `base_date`는 형식 보장이 없다(screen은 재무기준 줄에서 파싱, lookup은 `actual_basis`). **D12는 불투명 문자열 또는 None으로 저장**하고 중복 판정에만 쓴다.
- 신뢰 경계: draft는 서버 코드가 만든 값이라 저장 시 구조 재검증은 **최소 방어만**(`stock_code`가 요청 코드와 일치, `metrics` 가 dict). 값 검증·정규화는 하지 않는다(근거 불변·단순성).

## 3. DB (`backend/db/sqlite.py`)

D11 방식 그대로: 모듈이 자기 DDL을 소유, 호출마다 `sqlite3.connect(config.DB_PATH)`(호출 시점에 읽어 테스트가 monkeypatch 가능; 같은 DB 파일 공유), `CREATE ... IF NOT EXISTS`라 `logger_agent.init_table()`과 **초기화 순서 무관**. 연결 풀·WAL 없음. 쓰기는 모듈 `threading.Lock` 하나. `identity/store.py`와 같은 파일이므로 `timeout`(busy)을 같은 상수로 맞출지는 구현 시 확인.

```sql
CREATE TABLE IF NOT EXISTS reports(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  name TEXT NOT NULL, stock_code TEXT NOT NULL, stock_name TEXT NOT NULL, base_date TEXT,
  metrics_json TEXT NOT NULL, industry_avg_json TEXT NOT NULL, disclosures_json TEXT,
  disclaimer TEXT NOT NULL, memo TEXT NOT NULL DEFAULT '',
  created_at TEXT NOT NULL, updated_at TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS reports_stock ON reports(stock_code, base_date);
```

- DESIGN §8 컬럼 그대로(+NOT NULL/기본값, 인덱스는 구현 세부). `disclosures_json`은 draft의 `None`을 SQL NULL로(= 조회 안 함) 저장해 빈 리스트(`[]`, 공시 없음)와 구분.
- `created_at/updated_at`: UTC ISO8601 문자열(`datetime.now(timezone.utc).isoformat()`).
- **모든 SQL은 `?` 파라미터 바인딩**, f-string/format으로 값을 SQL에 넣지 않는다(인젝션 방지). 동적 컬럼은 PATCH의 `name`/`memo` 두 개 고정 화이트리스트로만 구성(§4).

### 함수 시그니처

```python
def init_table() -> None: ...                                   # 멱등. 앱 시작 시 D13이 호출(logger_agent.install과 순서 무관)
def save_report(draft: dict, name: str | None, memo: str | None) -> tuple[int, list[int]]:
    # (new_id, duplicate_of). name 기본 f"{stock_name} 리포트", memo 기본 "". disclaimer는 상수 DISCLAIMER 저장.
    # 중복 조회(stock_code=? AND base_date IS ?)와 INSERT를 같은 락·같은 트랜잭션에서 수행. 중복이어도 저장은 진행(F3015).
def list_reports() -> list[dict]: ...                           # ReportSummary 목록, created_at 내림차순(id 내림차순 보완)
def get_report(report_id: int) -> dict | None: ...              # Report(§5). 없으면 None
def update_report(report_id: int, name: str | None, memo: str | None) -> dict | None: ...
    # None이 아닌 필드만 갱신 + updated_at. 지표·기준일 컬럼은 이 함수가 건드리지 않음(F3016). 없으면 None
def delete_report(report_id: int) -> bool: ...                  # 삭제 여부
```

- `DISCLAIMER`는 `respond.py`의 상수를 재사용한다(문구 단일 출처). 노드 모듈 import가 부담이면 구현 시 공용 위치로 옮기는 것은 구현 판단(문구는 불변).
- 이 모듈은 FastAPI·memory를 import하지 않는다(순수 DB). 테스트가 임시 DB로 단독 검증 가능.

## 4. 입력 검증 (Pydantic, `api/reports.py`)

| 필드 | 규칙 |
|---|---|
| `session_id` | `str`, 1~64자 |
| `stock_code` | `^\d{6}$` (정규화는 호출 측 책임이라는 D10 규약 — 여기서 검증) |
| `name` | 선택. strip 후 1~100자(빈 문자열이면 미지정으로 취급하지 않고 422). 미지정이면 기본값 |
| `memo` | 선택. 0~1000자 |
| PATCH 본문 | `name`/`memo` **외 필드는 거부**(`extra="forbid"`; 지표 값 수정 불가를 계약으로 강제). 둘 다 없으면 422 |
| `{id}` 경로 | `int`(≥1). 변환 실패는 422 |

길이 상한은 임의 상수이며 구현 시 조정 가능. 클라이언트가 지표·기준일·공시를 보내도 **POST 모델에서 정의하지 않은 필드는 무시가 아니라 `extra="forbid"`로 422**(조작 시도 명시 거부; DESIGN §9).

## 5. 응답 모델

```
# D12b가 `review_date`를 추가해 이 정의를 대체한다(기준: docs/design/D12b-review-date.md).
ReportSummary = {id, name, stock_code, stock_name, base_date, created_at, updated_at}
Report        = ReportSummary + {metrics, industry_avg, disclosures, disclaimer, memo}
                (metrics/industry_avg/disclosures는 JSON 역직렬화된 객체; disclosures는 null 가능)
SaveResult    = {id, duplicate_of: [int]}      # POST, 201
Error         = {code, message}                # 모든 오류. 스택·키·SQL 비노출(NF3002)
```

`Report.metrics`는 draft의 `metrics`(MetricRow 5키 전체, reasons 포함)를 그대로 돌려준다 — D15가 `*`(estimated)·사유를 렌더하는 근거.

## 6. 라우트 (DESIGN §9 기준, 라우트 변경 없음)

`APIRouter(prefix="/api/reports")`를 `reports.py`가 export, D13이 앱에 등록하고 오류 핸들러를 공통 `{code, message}`로 통일(아래 코드 문자열이 계약).

| 라우트 | 성공 | 오류 |
|---|---|---|
| `GET /api/reports` | 200 `[ReportSummary]` | – |
| `GET /api/reports/{id}` | 200 `Report` | 404 `REPORT_NOT_FOUND` |
| `POST /api/reports` | **201** `{id, duplicate_of}` | 404 `DRAFT_NOT_FOUND`("먼저 해당 종목을 조회하세요."), 422 `INVALID_REQUEST` |
| `PATCH /api/reports/{id}` | 200 `Report` | 404 `REPORT_NOT_FOUND`, 422 `INVALID_REQUEST` |
| `DELETE /api/reports/{id}` | 204 (본문 없음) | 404 `REPORT_NOT_FOUND` |
| (모든 라우트) 예기치 못한 예외 | – | 500 `INTERNAL_ERROR`, 고정 문구 `"요청을 처리하지 못했습니다."` (원문은 마스킹 로그에만) |

- `POST`가 DESIGN §9 표에 상태코드를 적지 않았으므로 201(생성)로 확정. 클라이언트는 2xx면 성공으로 취급하면 되고 D14/D15는 본문의 `id`만 쓴다.
- DELETE는 멱등이 아니다(없으면 404). 로그 DELETE(D13)와 달리 사용자 화면에서 "이미 삭제됨"을 알릴 수 있게 함.
- 라우트 함수는 동기 `def`로 두어 FastAPI 스레드풀에서 실행(동기 sqlite로 이벤트 루프 차단 방지). 구현 단순성 우선.
- 오류 `code`의 Pydantic 422 기본 응답(`detail` 배열)은 쓰지 않고 D13의 공통 핸들러가 `INVALID_REQUEST`로 변환(메시지는 필드명 수준, 입력값 에코 금지 — 키가 값에 섞여 들어와도 응답에 반사되지 않게).

## 7. 저장 흐름 (report_save → DB)

```
POST /api/reports {session_id, stock_code, name?, memo?}
 1. 검증(§4)
 2. draft = memory.get_report_draft(session_id, stock_code)      # D10, 깊은 복사본. None → 404 DRAFT_NOT_FOUND
 3. 방어: draft["stock_code"] == stock_code 아니면 404 DRAFT_NOT_FOUND(맵 키와 내용 불일치 = 비정상)
 4. (id, dup) = sqlite.save_report(draft, name, memo)            # 중복이어도 저장
 5. 201 {id, duplicate_of: dup}
```

- 채팅 `report_save` 의도(D9)는 "리포트 초안이 준비되었습니다. 저장은 리포트 저장 기능을 이용하세요."만 응답한다. 사용자가 화면의 저장 동작을 하면 D14가 위 POST를 호출한다. D12는 채팅 경로를 알지 못한다.
- 값(지표·산업평균·공시·기준일·종목명)은 전부 서버 세션 draft에서만 온다. 요청 본문의 `name/memo` 외는 저장되지 않음 → 모델·클라이언트가 수치를 지어내거나 조작 불가(DESIGN §9).
- 저장은 **스냅샷**이다. 이후 세션 draft가 교체돼도 저장된 리포트는 불변(수정은 name/memo만).
- 중복: 같은 `(stock_code, base_date)`가 이미 있어도 새 행을 만들고 이전 id들을 `duplicate_of`에 오름차순으로 반환. `base_date`가 None끼리는 동일로 취급(`IS`). 사용자 확인 UI는 D14/D15.
- 한계(감수): 서버 재시작·세션 퇴출 후에는 draft가 없어 저장 불가(DESIGN §7 수용 범위). 다중 요청 경합에서 중복 판정은 같은 락 안에서 수행되어 직렬 일관.

## 8. XSS·인젝션

- **저장은 원문 그대로**(이스케이프·치환·태그 제거 하지 않음). `name`/`memo`/`stock_name`/공시 `title` 등 모든 문자열은 변형 없이 저장·반환한다. 이중 이스케이프 방지 및 데이터 원형 보존이 이유.
- **렌더 책임은 D15**: 모든 사용자·외부 유래 문자열(특히 `name`, `memo`, `stock_name`, 공시 `title`)을 `textContent`/텍스트 노드로만 삽입하고 `innerHTML`·인라인 핸들러 문자열 조립을 금지. 공시 `url`은 `http(s)://`로 시작하는 경우에만 링크화(`javascript:` 차단). D12는 이 규칙을 D15 설계에 넘긴다(§9 인터페이스).
- API 응답은 JSON(`application/json`)만. HTML을 반환하는 라우트 없음. 오류 메시지는 고정 문구이며 입력값을 반사하지 않음.
- SQL: 전 구문 파라미터 바인딩(§3). 정렬·컬럼명 등 구조 요소에 사용자 입력 불사용.
- 인증·CSRF: DESIGN 범위에 없음(단일 사용자 로컬 도구 전제). 별도 대응 없음.

## 9. D13/D15가 쓰는 인터페이스 요약

| 사용처 | 호출 |
|---|---|
| D13 앱 시작 | `sqlite.init_table()` 호출, `reports.router` 등록, 공통 오류 핸들러로 `{code, message}` 변환(§6 코드 문자열 5종: `REPORT_NOT_FOUND`·`DRAFT_NOT_FOUND`·`INVALID_REQUEST`·`INTERNAL_ERROR`) |
| D13 `/api/chat` | D12 의존 없음(draft는 `memory.run_turn`이 갱신). 응답에 "저장 가능 종목"을 노출할지는 D13/D14 결정 |
| D14 | 저장 버튼 → `POST /api/reports {session_id, stock_code, name?, memo?}`; 201의 `duplicate_of`가 비어 있지 않으면 "이미 저장된 동일 종목·기준일 N건" 안내; 404 `DRAFT_NOT_FOUND`는 안내문 표시 |
| D15 | 목록 `GET /api/reports`, 상세 `GET /api/reports/{id}`, 수정 `PATCH`(name/memo만 입력 UI 제공), 삭제 `DELETE`(204). **문자열은 텍스트로만 렌더, url은 http(s)만 링크**(§8). `disclosures==null`이면 "공시 조회 안 함", `[]`이면 "공시 없음"으로 구분 표기. `metrics.estimated`면 `*` 표기와 면책 `disclaimer` 표시 |

## 10. 상위 계약 요청 (직접 수정 안 함, 승인 대기)

1. **DESIGN §1 파일 트리**: `db/sqlite.py`는 `reports`, `run_logs` 둘을 같은 파일이 갖는다고 되어 있으나, D11이 `run_logs` DDL·함수를 `events/logger_agent.py`에 두었다. 정합을 위해 §1 트리 주석을 "db/sqlite.py # reports (run_logs는 events/logger_agent.py)"로 수정 요청. (대안: `sqlite.py`에 공용 연결 헬퍼를 두는 리팩터, 이번 범위 아님.)
2. **DESIGN §9**: `POST /api/reports`의 성공 상태코드 201, 오류 응답 `code` 문자열 4종(`REPORT_NOT_FOUND`·`DRAFT_NOT_FOUND`·`INVALID_REQUEST`·`INTERNAL_ERROR`)을 명세에 추가해 줄 것(현재는 `{code, message}`만 규정). 불승인 시에도 구현은 이 설계를 따르되 값만 조정.
3. **D0/D9**: 변경 요청 없음(draft 6키 수용).

## 11. 구현 시 확인 항목 (FAIL 아님)

- `identity/store.py`·`logger_agent`와 같은 DB 파일에 동시 쓰기 시 `database is locked` 빈도 → busy timeout 값 공통화 필요 여부.
- `base_date` 실제 값 형식(screen `_basis_line` 파싱 결과 vs lookup `actual_basis` "2024Q4" 류)이 섞여 중복 판정이 어긋나지 않는지 실호출로 확인. 필요하면 D9 쪽에서 형식 통일 요청.
- `reasons`·`industry_avg` JSON 직렬화(NaN/inf 값 유무) — `json.dumps(allow_nan=False)`로 걸리는지 확인.
- 100종목급 draft에서 POST는 1건만 저장하므로 크기 문제 없음. `GET /api/reports` 목록 크기 상한/페이지네이션은 두지 않음(필요 시 후속).
- 길이 상한(name 100, memo 1000, session_id 64) 적절성.
- `DISCLAIMER` 상수 import 위치(순환 import 여부).
- 공시 `url`이 http(s) 외 스킴을 포함하는 경우 데이터 자체는 저장하되 D15가 차단(D5 출력 확인).

## 12. 테스트 관점

- `sqlite`(임시 `DB_PATH`): `init_table` 멱등·`logger_agent.init_table`과 호출 순서 무관; `save_report` 컬럼 매핑(한글 보존 `ensure_ascii=False`, `disclosures` None↔`[]` 구분, `disclaimer` 저장); 중복 판정(같은 코드·기준일 → `duplicate_of` 포함하고 저장 진행, 다른 기준일/코드 → 빈 리스트, `base_date=None`끼리 동일 취급); `update_report`가 name/memo만 바꾸고 metrics·base_date 불변, `updated_at` 갱신, `created_at` 불변; 없는 id → None/False; 삭제 후 조회 None; SQL 인젝션 문자열(`'; DROP TABLE reports;--`)을 name/memo에 넣어도 테이블 유지·값 원문 그대로 반환; XSS 문자열(`<script>…`) 원문 보존.
- API(`TestClient`, mock `memory.get_report_draft`): POST 201 + 조회 일치; 세션/종목 없음 404 `DRAFT_NOT_FOUND`; 잘못된 `stock_code`·빈 name·긴 memo·여분 필드(`metrics` 등) 422 `INVALID_REQUEST`; 응답 본문에 입력값 미반사; PATCH 지표 필드 시도 422, 빈 본문 422, 정상 수정; DELETE 204 후 GET 404, 재DELETE 404; 응답 형식 `{code, message}`만(스택·경로·키 없음); 내부 예외 시 500 고정 문구.
- 통합: mock Tool/LLM으로 screen 1건 실행 → 같은 세션 draft로 POST → GET 값이 draft와 일치, lookup 병합 draft도 저장 가능, 세션 draft 교체 후에도 기존 리포트 불변.
