# D19 리포트 핵심 값 수정 — 사용자 판단 기준값(`target_per`·`target_roe`)

기준: invest-helper.md F3015·F3016("저장한 계획의 핵심 값 수정과 삭제, 목록에 바로 반영"), 셀프체크리스트 20번, DESIGN §8·§9, D12 §4·§7, D12a, D12b(`review_date` 이미 구현됨), D15 §6·§7. 사용자가 DESIGN.md 최소 수정을 승인함. D0·D1 변경 없음.

## 1. 현재 상태와 문제

수정 가능 = `name`·`memo`·`review_date`뿐이다. `name`·`memo`는 표시용 라벨, `review_date`는 날짜 1개라서 "계획의 핵심 값 수정"으로 읽히기엔 약하다. 목록에서 값이 바뀐 흔적(핵심 값)이 거의 안 보인다.

## 2. '핵심 값' 후보 비교

| 후보 | 성격 | 변조 위험 | 구현 | 평가 |
|---|---|---|---|---|
| A. 지표 값·산업 평균·기준일 직접 수정 | 조회·계산된 사실 | **높음**(분석 사실 변조, 출처 표시 원칙 위반) | 중 | 기각 |
| B. 종목 교체 | 리포트의 정체성 | 높음(저장된 지표와 불일치) | 중 | 기각. 다른 종목은 새 분석 후 새 리포트 |
| C. 재검토일 | 사용자 입력 날짜 | 없음 | 완료(D12b) | 유지(이미 핵심 값 1개) |
| D. **사용자 판단 기준값: 목표 PER 상한·목표 ROE 하한** | 사용자 입력 수치 | **없음**(저장 지표와 별도 컬럼, 스냅샷 불변) | 컬럼 2·필드 2 | **채택** |
| E. 원본 값 보존 + 지표별 조정값(override) | 지표 7개 × 2 | 낮음(병기) | 큼(JSON 구조·표 2열·검증 7종) | 기각(복잡). D가 같은 효과를 더 작게 냄 |

**선택: C(기존) + D.** 근거
- D는 이 서비스의 도메인 값 그 자체다. 검색 조건이 "ROE≥10, PER≤산업평균"이므로 저장 리포트에 "내 기준: PER ≤ 12, ROE ≥ 10"을 얹는 것은 "대상 항목·계획 기준 수정"에 대응한다. 사용자가 입력하는 값이라 분석 사실을 건드리지 않는다.
- 원본 보존 + 사용자 값 병기(E의 취지)를 D가 충족: 상세 지표 표의 **저장 지표는 그대로**, 바로 옆 "내 기준" 줄에 사용자 값과 **충족 여부 비교**(`저장 PER 8.20배 ≤ 내 상한 12 → 충족`)를 표시. 수정하면 비교 결과가 바로 바뀌므로 "수정 결과가 화면에 바로 반영"이 눈에 보인다(체크리스트 20번 증빙에 유리).
- 자체 평가: 원본의 '핵심 값 수정'(저장 계획의 기준 수치·날짜를 고치고 목록에 즉시 반영)을 `review_date` + `target_per` + `target_roe` 3개 핵심 값으로 충족한다. 종목·지표 직접 수정은 금융 도메인 변조 방지를 이유로 의도적으로 제외하며 README에 근거를 적는다.

## 3. 필드·검증

| 필드 | 타입 | 범위 | 의미 |
|---|---|---|---|
| `target_per` | float\|null | `0 < x ≤ 1000` | 내 PER 상한(이하면 충족) |
| `target_roe` | float\|null | `-100 ≤ x ≤ 1000` | 내 ROE 하한(%, 이상이면 충족) |

- **PATCH와 응답에만 추가**한다. POST(저장)는 변경하지 않는다(저장 패널 단순 유지; 저장 후 수정으로 입력). POST에 오면 기존대로 `extra=forbid` 422.
- `extra="forbid"` **유지**. 허용 필드가 5개로 늘 뿐 `stock_code`·`base_date`·`metrics` 등은 계속 422.
- 값 지우기: 이 두 필드만 **명시적 `null` = 삭제**, 키 생략 = 변경 없음(Pydantic `model_fields_set`). 기존 `name`/`memo`/`review_date`의 규칙(`null`=변경 없음)은 그대로 둔다. 숫자는 `""` 표현이 불가해 이 방식이 가장 단순.
- `bool`·`NaN`·`Infinity` 거부: `StrictFloat` 대신 `float` + `allow_inf_nan=False` Field 제약 + `field_validator`로 `bool` 거부(JSON `true`가 1.0이 되는 것 방지). 정수 JSON(`12`)은 허용.
- 빈 PATCH 판정: `name`/`memo`/`review_date`가 None이 아니거나 `target_per`/`target_roe`가 `model_fields_set`에 있으면 유효. 그 외(`{}`, `{"name":null}`)는 기존대로 422.
- 중복(`duplicate_of`)은 `(stock_code, base_date)` 기준 그대로. 기준값은 중복 판정과 무관.

## 4. DB (`backend/db/sqlite.py`)

D12b 방식 그대로 확장한다. `_DDL`은 건드리지 않고 `init_table()`의 마이그레이션 루프에서 컬럼 추가:
```python
have = {r[1] for r in con.execute("PRAGMA table_info(reports)")}
for col, typ in (("review_date", "TEXT"), ("target_per", "REAL"), ("target_roe", "REAL")):
    if col not in have:
        con.execute(f"ALTER TABLE reports ADD COLUMN {col} {typ}")   # col·typ는 코드 상수, 기존 행 NULL
```
- 기존 데이터 호환: ADD COLUMN은 기존 행 불변·`NULL`. 멱등.
- `_COLS` 끝에 `, target_per, target_roe`(인덱스 13·14), `_summary`에 `"target_per": r[13], "target_roe": r[14]` 추가 → **목록·상세 모두 포함**(목록에서 바로 보이게).
- `update_report(report_id, name, memo, review_date=None, targets: dict | None = None)`: `targets`는 `{"target_per": 12.0}`처럼 **바꿀 키만** 담는다(값 `None` → `NULL`). 키는 `{"target_per","target_roe"}` 화이트리스트로 필터 후 `col=?` 바인딩. 기본값 `None`이라 기존 호출 무수정.
- `save_report`는 변경 없음(신규 행은 두 컬럼 NULL).

## 5. API (`backend/api/reports.py`)

- `PatchBody(_Edit)`에 `target_per`·`target_roe` 필드와 `_any` 확장(§3). `CreateBody`는 불변(필드를 `_Edit`가 아닌 `PatchBody`에만 둔다).
- `patch`: `targets = {k: getattr(body, k) for k in ("target_per","target_roe") if k in body.model_fields_set}` → `sqlite.update_report(id, body.name, body.memo, body.review_date, targets)`.
- 응답 `Report`·`ReportSummary`에 `target_per`·`target_roe`(number|null). 오류 형식·입력값 비에코 규칙 불변(`_Route` 변환).

## 6. 화면 (`frontend/reports.js`, `style.css` 최소)

- 목록 행: 기존 텍스트 뒤에 값이 number면 `" · 내 기준 PER≤{v} ROE≥{v}"`(있는 것만) 추가. 수정 직후 `loadList`/해당 행 갱신으로 즉시 반영(기존 PATCH 성공 후 갱신 경로 재사용).
- 상세: "재검토 예정일" 줄 다음에 `p("내 기준 PER ≤ {v}배 / ROE ≥ {v}%")`(없으면 "-") + 값이 있고 `metrics.values.PER|ROE`가 number면 비교 결과 한 줄(`저장 PER 8.20배: 내 상한 이하(충족)` / `초과(미충족)`). 지표가 null이면 "비교 불가(저장 지표 없음)". 비교는 프론트 순수 함수 `compareTarget(metrics, per, roe)` 1개(서버 계산 없음, 표시용 부가 정보; 저장 지표 값은 변형하지 않음).
- 수정 버튼 `"이름·메모·재검토일 수정"` → `"이름·메모·재검토일·내 기준 수정"`. 폼에 `<input type="number" step="any" id="edit-per">`(라벨 "내 PER 상한"), `id="edit-roe"`("내 ROE 하한(%)") 추가, 현재값 채움, 비우면 삭제.
- `buildPatchBody(cur, name, memo, review, per, roe)`: `per`/`roe`는 문자열 입력. 현재값과 다를 때만 포함. 빈 문자열 → `null`(삭제), 숫자 변환(`Number`, `isFinite` 아니면 클라이언트 오류 "숫자를 입력하세요."), 범위 위반은 서버 422에 맡기고 `errText`의 INVALID_REQUEST 문구에 "기준값 범위(PER 0 초과~1000, ROE -100~1000)" 안내 추가. 인자 생략 시 변경 없음 취급 → 기존 테스트 호출 호환.
- `EDIT_NOTE`: `"이름·메모·재검토 예정일·내 기준값(PER 상한, ROE 하한)만 수정할 수 있습니다. 지표·기준일은 분석 당시 조회한 근거라 수정할 수 없습니다. 바꾸려면 채팅에서 다시 분석해 새로 저장하세요."`
- 모든 값은 `textContent`/`input.value`로만 넣는다(XSS 규칙 불변).

## 7. DESIGN.md 최소 수정(사용자 승인 범위)

§8
- 스키마 첫 줄: `memo, review_date, created_at, updated_at)` → `memo, review_date, target_per, target_roe, created_at, updated_at)`
- 수정 가능 필드 줄 → `- 수정 가능 필드: \`name\`, \`memo\`, \`review_date\`(재검토 예정일, YYYY-MM-DD, 선택), \`target_per\`·\`target_roe\`(사용자 판단 기준값: PER 상한·ROE 하한, 선택)만(F3016). 지표·기준일·산업 평균·공시는 불변 — 근거가 바뀌지 않게. 기준값은 사용자 입력이며 저장 지표와 별도로 보관·병기한다.`
§9
- PATCH 행 요청 → `{name?, memo?, review_date?, target_per?, target_roe?}` (`review_date:""`이면 삭제, `target_per`·`target_roe`는 `null`이면 삭제)

D0: **변경 불필요**(리포트 응답 모델 미정의). D1 영향 없음.
하위 문서: D12 §4·§5, D12a, D12b, D15 §7 원문은 고치지 않고 **D19를 참조 기준**으로 둔다(D12b 방식). README §8 "리포트 수정(F3016)" 불릿에 "내 기준값(PER 상한·ROE 하한)" 추가와, "종목·기준일·지표 값 수정 불가 = 변조 방지" 근거 문장 유지. invest-helper.md 120·314행 표기도 같은 문구로 갱신(구현 단계).

## 8. 영향 범위

| 파일 | 변경 |
|---|---|
| `docs/DESIGN.md` §8·§9, `README.md` §8, `invest-helper.md` 2곳 | §7 문구 |
| `backend/db/sqlite.py` | 마이그레이션 루프, `_COLS`·`_summary`, `update_report(targets)` |
| `backend/api/reports.py` | `PatchBody` 2필드·`_any`·`patch` |
| `frontend/reports.js`, `style.css` | §6 (목록 행, 상세 줄·비교, 폼 입력 2개, `buildPatchBody`, `compareTarget`, `EDIT_NOTE`, `errText` 문구) |
| 변경 없음 | `chat.js`·D14 저장 패널, `CreateBody`, D0·D1, 에이전트·툴 |

### 기존 테스트 보정
- `tests/frontend/d15_dom.test.js:39` 버튼 문구 단언 → 새 문구. `EDIT_NOTE`를 단언하는 곳이 있으면 함께 보정(구현 시 grep).
- `test_d12.py`의 PATCH 금지 필드 목록(`base_date`·`metrics`·`stock_code` 등)은 그대로 422여야 한다. 목록에 `target_per`가 있다면 제거.
- 응답 키 집합 `==` 단언·`update_report` 위치 인자 호출은 기본값 덕에 영향 없음(구현 시 grep으로 재확인).
- `tests/test_d16_e2e.py`의 PATCH 흐름은 영향 없음.

## 9. 사용자 사례 시나리오

1. **저장 → 기준값 수정 → 즉시 반영**: 삼성전자 리포트 저장(저장 PER 8.2, ROE 12.0) → 상세에서 수정: PER 상한 10, ROE 하한 15 → PATCH 200, 응답에 `target_per:10, target_roe:15`. 상세에 "내 기준 PER ≤ 10배 / ROE ≥ 15%", "PER 충족 / ROE 미충족"이 즉시 표시, 목록 행에 `내 기준 PER≤10 ROE≥15`, `updated_at` 갱신. 저장 지표·기준일은 불변.
2. **재수정·삭제**: 같은 리포트에서 PER 상한을 `7`로 변경 → 비교가 "초과(미충족)"로 바뀜. `target_roe`를 비워 저장 → PATCH `{target_roe:null}` → 목록·상세에서 ROE 기준 사라지고 PER 기준만 남음.
3. **잘못된 값 422**: PATCH `{target_per:0}`, `{target_per:-1}`, `{target_per:5000}`, `{target_roe:"abc"}`, `{target_per:true}`, `{base_date:"2025"}`, `{metrics:{}}`, `{}` → 모두 422 `{code:"INVALID_REQUEST", message}`, 입력값 비에코, DB 불변. 화면 폼에서 `abc` 입력은 클라이언트가 "숫자를 입력하세요."로 막는다.
4. **삭제**: 리포트 삭제 → 확인 단계 → 204 → 목록에서 즉시 제거, 재조회 404.
5. **구 DB 호환**: 컬럼이 없는 기존 DB로 기동 → 마이그레이션 후 기존 리포트 목록·상세 정상, 두 값 `null`, "내 기준 -" 표시.

## 10. 체크리스트 20번 증빙 계획

- 확인 방법 그대로: 계획(리포트) 관리 화면(`reports.html`)에서 ① 상세 → [수정]으로 재검토일·내 PER 상한·ROE 하한 변경 → 목록 행과 상세에 새 값 표시 스크린샷, ② 같은 항목 값 삭제(비움) 후 표시 변화, ③ 리포트 삭제 후 목록에서 사라짐 스크린샷.
- 자동 증빙: 아래 §11 테스트 통과 로그. 증빙 문서는 구현 단계에서 체크리스트 20번 칸에 "reports.html 수정·삭제 화면 + test_d12/d15 결과"로 기재.

## 11. 테스트

백엔드(`tests/test_d12.py` 추가)
1. 마이그레이션: 두 컬럼 없는(review_date까지 없는) 구 스키마 DB에 행 1개 → `init_table()` ×2 → 컬럼 3개 존재, 기존 행 값 동일, 신규 값 `None`.
2. PATCH `{target_per:10, target_roe:15}` → 200, 응답·GET 상세·목록에 반영, `metrics`·`base_date`·`name` 불변, `updated_at` 증가. 정수·실수 모두 허용.
3. PATCH `{target_roe:null}` → NULL로 삭제, `target_per` 유지. 키 생략 시 변경 없음. 다른 필드(`name`)만 보낼 때 기준값 불변.
4. 422 목록(§9-3) 전부, POST에 `target_per` 포함 시 422, `{"name":null}` 단독 422 유지.
5. `duplicate_of`: 기준값이 달라도 같은 `(stock_code, base_date)` 중복 판정 불변.
6. SQL 인젝션 키 시도(`{"target_per; DROP":1}`)는 `extra=forbid`로 422, 테이블 유지.

프런트(`tests/frontend/d15.test.js`, `d15_dom.test.js` 추가)
- `buildPatchBody`: 값 변경 → 숫자로 포함, 비움 → `null`, 변경 없음 → noop, `abc` → 오류, 인자 생략 호환.
- `compareTarget`: 충족/미충족/지표 null/기준 없음.
- DOM: 상세에 "내 기준" 줄·비교 줄, 폼에 `#edit-per`·`#edit-roe`, 목록 행에 기준값 표시, 버튼 새 문구.

수동: 브라우저에서 §10 ①~③, 좁은 폭에서 폼 줄바꿈·가로 스크롤 없음.

## 12. 구현 시 확인 항목
- Pydantic 2.14.2에서 `float | None`에 `gt/le`가 `None`에 적용되지 않는지, `true`(bool) 거부 방식(`field_validator(mode="before")`)이 동작하는지 실행 확인.
- `model_fields_set`이 `model_validator(mode="after")`에서 의도대로 보이는지.
- `PRAGMA`+`ALTER` 루프가 `executescript` 직후 같은 트랜잭션에서 충돌 없는지(D12b와 동일 점검).
- 부동소수 표시: `10.0`→`10`, `12.5` 그대로 표시되는지(`String(v)` 또는 기존 `fmtMetric` 재사용 여부 확인).
- `.edit-form`에 number input 2개 추가 시 좁은 폭 레이아웃.
- ROE 단위는 %(저장 지표 ROE와 같은 단위)임을 실제 `metrics.values.ROE` 값 크기(예: 12.0 vs 0.12)로 확인하고, 다르면 `compareTarget` 환산 대신 라벨만 보정(저장 지표 값은 변형하지 않는다).

## 13. D0·D1 수정 제안
없음.
