# D2 식별·산업 데이터 (`backend/identity/`)

기준: D0 §3·§4·§6-8/9/12, D1 §3(NF3002), DESIGN §3 '실호출 검증 결과', `scripts/check_apis.py`·`scripts/out/*.json`(데이터로만 참조). 의사코드이며 구현 아님. Tool이 아니므로 `ToolResult`를 쓰지 않는다.

**배치**: `backend/tools/` 밖(`load_all()`이 tools/*.py를 전부 import). 모듈 `models.py, store.py, dart.py, build.py(CLI), resolve.py`, 데이터 `data/industry_terms.json`. `tools.base.mask`만 import. SQLite 파일은 D12와 같은 `config.DB_PATH`, 테이블만 별도. **추가 의존성**: `defusedxml`.

**운영 모델(확정)**: 캐시는 **CLI `python -m backend.identity.build`로만 구축·갱신**한다(수동). **앱은 DB를 읽기 전용으로만 쓰며 요청 경로에서 DART 호출·쓰기가 없다**(그래서 요청 경로는 DART 키와 무관). 앱은 자동 구축하지 않는다. CLI는 단일 writer이며 **동시에 CLI를 두 개 실행하지 않는다**(운영 규칙; SQLite 파일 잠금·busy_timeout에만 의존). 앱 실행 중 CLI 실행은 허용(WAL이라 읽기 가능).

## 1. 확인된 사실 vs 가정

| 구분 | 내용 |
|---|---|
| 실호출 확인 | 삼성전자 `stock_code 005930 ↔ corp_code 00126380`(DART list.json), `company.json`의 `jurir_no`(=crno)·`induty_code`(DESIGN §3), `list.json`은 `corp_cls`(Y=KOSPI) 반환, 금융위 기업명 검색은 오매칭이라 미사용 |
| **미확인(가정)** | A1 DART 일일 한도(통상 20,000건/키로 알려졌으나 미확인). A2 상장사 수(수천 개 추정) → 구축 호출 ≈ 상장사 수+1. A3 `induty_code` 자릿수·KSIC 차수(샘플 응답이 `scripts/out`에 없음). A4 `company.json`의 `corp_cls` 제공 여부. A5 한도초과/키오류 시 HTTP 200 본문 `status`(010/011/013/020, DART 문서 기준·미실호출). A6 corpCode.xml에 상장폐지 종목이 남는지. A7 종목코드 6자리 영숫자(`^[0-9A-Z]{6}$`). **A8 corpCode.xml에서 비상장은 `stock_code` 공백, 우선주는 별도 항목 없음(미실호출, 구현 시 corpCode.xml 1회 파싱으로 확인; 틀리면 우선주·비상장 필터를 별도 처리)** |

산업 매칭은 접두 문자열 비교(자릿수 무관), 시장은 캐시에 필수 아님(D3가 주가 API `mrktCtg` 사용).

## 2. SQLite 스키마

```sql
company(stock_code TEXT PRIMARY KEY,          -- stock_code 공백 행은 적재하지 않음(A8)
        corp_code TEXT NOT NULL, name TEXT NOT NULL, name_norm TEXT NOT NULL,
        listed INTEGER NOT NULL DEFAULT 1,    -- 최신 단계 A에서 사라지면 0(삭제 안 함)
        crno TEXT, induty_code TEXT, corp_cls TEXT,
        detail_status TEXT NOT NULL DEFAULT 'pending',  -- pending|ok|none|failed
        detail_at TEXT)
CREATE INDEX ix_company_corp ON company(corp_code);
CREATE INDEX ix_company_name ON company(name_norm);
CREATE INDEX ix_company_ind  ON company(induty_code);
cache_meta(key TEXT PRIMARY KEY, value TEXT)  -- corp_synced_at (UTC ISO)
```
`crno`는 `jurir_no`에서 숫자만 남겨 `^\d{13}$`일 때만 저장, 아니면 NULL. 스키마 생성은 CLI만 수행(앱의 읽기 전용 연결은 파일·테이블이 없으면 **빈 캐시**로 취급).

**complete** := `listed=1` 행 기준, `total`=`detail_status!='none'` 행 수, `total==0`이면 False, 그 외 `(pending+failed)*20 <= total`(5%, 임의 가정). **빈 캐시** := `listed=1` 행 0개. **미완료** := 빈 캐시 아님 & complete=False.

## 3. 인터페이스·실패 표현

```python
class IdentityError(Exception):   # D3~D5가 ToolFailure(e.code, e.message)로 1:1 변환
    code: Literal["MISSING_API_KEY","API_ERROR","RATE_LIMIT","TIMEOUT","NO_RESULT","INVALID_INPUT"]
    message: str                  # D2 모듈 상수의 고정 한국어 문구. 원문·키·URL 금지
@dataclass(frozen=True) class StockRef: stock_code: str; corp_code: str; name: str
@dataclass(frozen=True) class StockMatch:      # 없음/복수는 예외가 아닌 값(D8/D9가 문구 선택)
    status: Literal["ok","none","multiple"]; matches: tuple[StockRef,...]; total: int
    matched_by: Literal["code","exact","prefix","contains"] | None
@dataclass(frozen=True) class BuildReport:     # CLI 종료 요약 전용
    ok: int; none: int; failed: int; pending_left: int
    stopped_by: Literal["RATE_LIMIT","MISSING_API_KEY"] | None; complete: bool
# 아래 전부 동기, 읽기 전용(DB만)
def resolve_stock(query) -> StockMatch                       # D8
def get_corp_code(stock_code) -> str                         # D4/D5
def get_crno(stock_code) -> str                              # D4
def resolve_industry(text) -> IndustryResolution             # D3
def select_by_industry(prefixes) -> list[IndustryStock]      # {stock_code,name,induty_code}. D3
def similar_industries(text) -> list[str]                    # D9
def cache_status() -> CacheStatus                            # {total,ok,pending,failed,complete,corp_synced_at}
```

**메시지(D1 MSG 규칙 준수, 키 비노출)**: D2 전용 고정 문구 2종 — `CACHE_NOT_READY`="종목 목록이 준비되지 않았습니다. python -m backend.identity.build 를 먼저 실행하세요."(API_ERROR), `INDUSTRY_NOT_READY`="산업 데이터 구축이 완료되지 않았습니다. python -m backend.identity.build 를 다시 실행하세요."(API_ERROR). `INVALID_INPUT`="입력값이 올바르지 않습니다."(D1과 동일), `NO_RESULT`="대상을 찾을 수 없습니다."(D3~D5가 대체 가능). `sqlite3.Error`는 일반 API_ERROR 문구로 `from None` 변환. 키 관련 코드(MISSING_API_KEY/RATE_LIMIT/TIMEOUT)는 CLI 내부에서만 쓰며 문구는 D1 `MSG`와 동일.

**캐시 상태 표 (요청 경로 전체 규칙)**

| 함수 | 빈 캐시 | 미완료 | 완료 |
|---|---|---|---|
| `resolve_stock` | `CACHE_NOT_READY`(코드 직접 입력도) | 정상 | §4 |
| `get_corp_code` | `CACHE_NOT_READY` | 정상 | `listed=1` 행의 corp_code, 없음 → `NO_RESULT` |
| `get_crno` | `CACHE_NOT_READY` | 정상 | `crno` 있으면 반환, 그 외(pending/failed/none/미존재/crno NULL) → `NO_RESULT`(D4가 NO_FINANCIALS로 변환) |
| `resolve_industry` | 정상(`n_listed`=0) | 정상 | §5 |
| `select_by_industry` | `CACHE_NOT_READY` | `INDUSTRY_NOT_READY` | 접두가 `^\d{2,5}$` 아니거나 빈 목록 → `INVALID_INPUT`; `ok` 행(listed=1, induty_code 접두 일치)만, 0행이면 `[]`(D3가 NO_RESULT) |
| `similar_industries` | `[]` | 현재 `ok` 행으로 계산(빈 값 가능) | §5 |
| `cache_status` | `complete=False` | 정상 | 정상 |

D8은 `resolve_stock`의 `IdentityError`를 catch해 `ErrorRecord(tool="resolve_stock", stock_code=None, code, message)`로 기록하고 D0 §6-8/9대로 `status=error`로 처리한다(요청 경로에서 MISSING_API_KEY는 나오지 않음).

## 4. 이름→종목코드 (`resolve_stock`; 위임 D0 §6-8·9, 호출 D8, 문구 D9)

1. 입력 NFKC·strip. `^[0-9A-Z]{6}$`이면 코드 조회(`listed=1`): 있으면 `ok(matched_by="code")`, 없으면 `none`.
2. `name_norm` = NFKC·소문자·공백/`(주)`/`주식회사`/구두점 제거. 빈 문자열이면 `none`.
3. 정확(`=`) → 접두 → 포함(길이≥2) 순으로 **처음 1건 이상 나온 단계에서 멈춤**. 1건 `ok`(matched_by 기록), 0건 `none`, 2건 이상 `multiple`(자동 선택 안 함).
4. `multiple`은 `(len(name), stock_code)` 오름차순 상위 10건 + `total`. 대상은 `listed=1`만.
5. 한계: 우선주·신규 상장(다음 CLI 갱신 전)은 `none`일 수 있음(A8).

## 5. 산업 해석·유사 업종

`data/industry_terms.json` = 수작업 `[{term, prefixes:[...]}]`. `term`도 아래 정규화를 거쳐 비교하며, 정규화 후 길이<2이거나 중복이면 로딩 시 `ValueError`. 코드 값은 구현 시 KSIC 표(A3 확인 후)로 채운다(값을 지어내지 않음).

`resolve_industry(text) -> {status: ok|ambiguous|unknown, prefixes, label, options}` (LLM 미사용, 결정적):
1. 정규화: NFKC·공백 제거·접미를 **단일 패스** `re.sub(r'(업종|산업|관련주|섹터|업)$','',s)` 한 번만. 길이<2 → `unknown`.
2. 숫자 2~5자리 → 접두 코드 그대로 `ok`(D3가 `extra_industries` 재해석 시 이 경로).
3. 용어 정확 일치 → `ok`. 아니면 `query ⊂ term` 후보를 `prefixes` 집합으로 중복 제거: 1개 `ok`, 2개 이상 `ambiguous`(options ≤5, 각 `{term,prefixes,n_listed}`; 추측 안 함), 0개 `unknown`.
4. **권고(D3 결정)**: `unknown/ambiguous` → `INVALID_INPUT`(ambiguous는 options term을 message에). NO_RESULT로 하면 재계획이 헛돈다.

`similar_industries(text) -> list[str]` (D0 ② 입력, 빈 리스트=②는 no-op): `resolve_industry`가 `ok`가 아니면 `[]`. primary 접두 p(길이 L≥3, L≤2는 확장 불가)의 부모 `p[:-1]` 아래에서 **listed=1 AND detail_status='ok'** 행 중 어떤 primary 접두로도 시작하지 않는 것의 길이 L 접두를 집계, 상장사 수 내림차순→코드 오름차순으로 **전체 합산 상위 5개**, 중복 제거. 결과는 숫자 문자열 리스트로 `extra_industries`에 들어가 §5-2로 재해석된다. **채우는 주체**: 초기 plan은 `[]`, 재계획 ②를 적용하는 노드(D9 판정, D7 plan 재실행)가 `similar_industries(conditions.industry)` 결과를 채운다.

## 6. 구축 CLI (`build.py`, 유일한 writer)

`python -m backend.identity.build [--refresh] [--force]` — 시작 시 로깅 설정 후 `install_log_mask()` 호출, `DART_API_KEY==""`이면 네트워크 전에 `MISSING_API_KEY`로 종료. 결과 요약은 logging으로 건수만(`print` 금지), `BuildReport`를 만들고 `complete`가 아니면 종료 코드 1.

- **단계 A**(캐시가 비었거나 `--refresh`/`--force`): corpCode.xml 1회(자체 타임아웃, TOOL_TIMEOUT_S와 별개 상수) → **한 트랜잭션**에서 upsert(`corp_code·name·name_norm·listed=1`만 갱신, detail 컬럼 보존; `corp_code`가 바뀐 행은 detail을 pending·crno/induty NULL로 초기화)와 응답에 없는 종목 `listed=0`. 응답 종목 0건이면 `API_ERROR`로 중단. 단계 A 실패는 그대로 종료(기존 데이터 유지).
- **`--force`**: 단계 A 전에 전체 detail을 pending·crno/induty NULL로 초기화(재구축 동안 complete=False).
- **단계 B**: `listed=1`이고 `detail_status IN ('pending','failed')`인 행만 순회해 company.json 호출, 결과를 기록: 성공 → `ok`(crno·induty_code), 013 → `none`, 일시 오류(API_ERROR/TIMEOUT) → `failed`(계속). **한도 신호(429/020)·키 오류(010/011)는 즉시 중단**, 해당 행은 그대로 둠(`stopped_by`). N행(`COMMIT_EVERY`)마다 커밋, 종료·예외 시 `finally`로 남은 변경 커밋. 같은 CLI를 다시 실행하면 pending+failed만 이어서 처리(멱등·재개). 갱신은 수동(`--refresh`)이며 주기 개념 없음.
- **DART 응답 매핑**(`dart.py`)

| 입력 | 처리 |
|---|---|
| status 010/011 | `MISSING_API_KEY`(키 무효 포함) → 중단 |
| 020, HTTP 429 | `RATE_LIMIT` → 중단 |
| 013 | 행 `none` |
| httpx Timeout | `TIMEOUT` → 행 `failed` |
| 그 외 status/HTTP/연결/파싱 오류 | `API_ERROR` → 행 `failed` |

## 7. 보안·로깅 (D1 NF3002)

- 키가 쿼리스트링(`crtfc_key`)에 실리므로 `dart.py`는 예외를 잡아 **`raise IdentityError(...) from None`**. 로그는 타입명·HTTP status·건수만, 모든 텍스트는 `mask()` 통과, `str(exc)`·URL·params·응답 본문 금지.
- **corpCode 안전 파싱**: 응답 바이트 상한(`CORP_ZIP_MAX_BYTES`) → zip 멤버는 `CORPCODE.xml` 하나만 → 압축 해제 크기(`file_size`) 상한(`CORP_XML_MAX_BYTES`) 검사 후 읽기 → `defusedxml`로 파싱(DOCTYPE/ENTITY 거부). 위반 시 `API_ERROR`. 상한 값은 구현 시 실측의 여유 배수로 정함.
- SQL: 모든 쿼리 `?` 바인딩만. LIKE 입력은 `%`·`_`·`\` 이스케이프 + `ESCAPE '\'`. 읽기 연결은 호출 단위로 새로 열고(`mode=ro`), `busy_timeout`(상수), 인덱스를 LIKE에 쓰려면 `PRAGMA case_sensitive_like=ON`(또는 BINARY 콜레이션). CLI(writer)는 `journal_mode=WAL`.

## 8. 위임 메모

- D3: `select_by_industry`가 수백 건일 수 있다. 시장/시총 보강 방식과 D1 10초 상한은 D3가 설계. complete가 미완료 5%까지 허용해 일부 종목이 조용히 빠질 수 있으므로 `cache_status()`를 확인해 '산업 데이터 일부 미구축'을 D3가 표기.
- D4: `get_crno`는 동기·읽기 전용이며 `NO_RESULT`→`NO_FINANCIALS`. D5: `get_corp_code`의 `NO_RESULT`는 그대로.
- D5/D8: 동기 sqlite 함수는 요청 경로에서 `asyncio.to_thread`로 처리.
- D13: build CLI는 앱과 별개 진입점이며 **앱은 자동 구축하지 않는다**(주기 재호출 없음).

## 9. 테스트 가능성 (실호출 없음)

네트워크는 `DartSource` Protocol(`async fetch_corp_list()`, `async fetch_company(corp_code)`)로 분리, 기본 `HttpDartSource`; 테스트는 가짜 주입 + tmp DB. 점검: §3 상태 표 전 칸(빈/미완료/완료), 이름 3단계·복수 정렬·코드 직접, 산업 ok/ambiguous/unknown·접미 단일 패스·term 검증, 유사 업종(자기 제외·합산 상위 5·L≤2), 단계 A upsert(corp_code 변경·listed=0·0건 거부), 단계 B 결과 기록·한도/키 오류 중단 후 재실행 재개, `--force`, zip 폭탄/비정상 멤버/DOCTYPE 거부, 오류 매핑 표, 키를 URL에 넣은 가짜 httpx 예외에서 메시지·로그·`__cause__` 미노출, LIKE 이스케이프.

## 구현 시 확인 항목

- A1~A8 전부 확인. `company.json` 실응답 1건을 `scripts/out`에 저장(`induty_code` 자릿수·차수), 금융업 등 `jurir_no` 부재 종목, `COVERAGE_MISS_DENOM`·`COMMIT_EVERY`·zip/XML 상한 적정값, KSIC 표 출처·이용 조건, LIKE 인덱스 사용 여부(`EXPLAIN QUERY PLAN`).
- 동시성·타이밍: 구축 중 앱이 읽는 중간 상태(단계 A 직후 B 진행 중 = 미완료로 보임), CLI 동시 실행 금지 위반 시 동작(SQLite 잠금 오류 시 종료), 강제 종료 후 재실행 시 pending/failed 재개 확인, `--force` 중 앱 요청의 `INDUSTRY_NOT_READY`.

## 상위 계약에 대한 요청

1. **D0 §4 `extra_industries` 원소 형식 확정**("KSIC 접두 숫자 문자열")과 §12-②를 채우는 주체(D9 판정 후 D7 plan 재실행이 `similar_industries(conditions.industry)`로 채움)를 D0에 명시.
2. **모호/미해석 산업**: D0 §6-6은 '산업 누락'만 되묻는다. D2 권고는 `INVALID_INPUT`(규칙 2로 error). asked로 되묻기를 원하면 §6-6에 'Tool 전 `resolve_industry` 호출' 추가 필요.
3. **D0 §6-8/9**: `resolve_stock`이 `IdentityError`(캐시 미준비 API_ERROR)를 던지는 경우도 error로 종료한다는 문장 추가.
4. **DESIGN §1·README**: `backend/identity/` 패키지, `defusedxml` 의존성, D12와 공유하는 `DB_PATH` 코드 상수 확정, README 실행법에 'build CLI를 앱 실행 전에 한 번 실행' 단계 추가.
