# D12b 리포트 재검토 예정일(review_date)

기준: invest-helper.md F3015("대상 항목, 날짜, 시각 등 계획의 핵심 값과 생성 일시")·F3016("핵심 값을 수정"), DESIGN §8·§9, D12 §4·§5, D12a, D14c 저장 패널, D15 §7. **사용자 승인 방향**: 리포트에 사용자가 다시 볼 날짜 `review_date`를 두고 저장 시 지정·이후 수정 가능. 지표·기준일·산업 평균·공시는 계속 불변(D12a 원칙 유지).

## 1. 결정 요약

| 항목 | 결정 |
|---|---|
| 값 | `review_date`: `"YYYY-MM-DD"` 문자열 또는 `null`(미지정). 시간대 없음(사용자 달력 날짜) |
| 과거 날짜 | **허용**. 서버 시계와 비교하지 않는다(단순·테스트 결정적). 지난 날짜도 "이미 지난 재검토일"로 의미 있음 |
| 지우기 | 요청에서 `""` → DB `NULL`(미지정으로 되돌림). `null`/생략 → "변경 없음". 기존 `memo`의 `""` 의미와 같은 규칙, `model_fields_set` 불필요 |
| 중복 알림 | `duplicate_of`는 기존 `(stock_code, base_date)` 기준 **그대로**. `review_date`는 중복 판정과 무관 |
| D0 | 변경 없음(D0은 리포트 응답 모델을 정의하지 않음 — `grep ReportSummary docs/design/D0-contract.md` 0건) |

## 2. DB (`backend/db/sqlite.py`)

- `_DDL`은 **그대로 둔다.** `init_table()`에서 `executescript(_DDL)` 직후, 같은 트랜잭션에서:
  ```python
  if "review_date" not in {r[1] for r in con.execute("PRAGMA table_info(reports)")}:
      con.execute("ALTER TABLE reports ADD COLUMN review_date TEXT")  # 기존 DB 마이그레이션, 기존 행은 NULL
  ```
  신규 DB·기존 DB 모두 이 한 경로로 컬럼을 얻는다(경로 1개 → 테스트 1개). 기존 행 보존(ADD COLUMN은 데이터 불변), 반복 호출 멱등(기존 `test_d12` 51행의 다중 `init_table()` 호출과 호환).
- `_COLS` 끝에 `, review_date` 추가(인덱스 12). `_summary`에 `"review_date": r[12]`.
- `save_report(draft, name, memo, review_date: str | None = None)` — INSERT 컬럼·값에 추가(`None`/`""` → `NULL`). 중복 조회 SQL은 변경 없음.
- `update_report(report_id, name, memo, review_date: str | None = None)` — 화이트리스트에 `review_date` 추가: `None`이면 건드리지 않음, `""`이면 `review_date=NULL`, 그 외 값 `?` 바인딩. 키워드 기본값 `None`이라 기존 위치 인자 호출(`tests/test_d12.py` 57~99행)은 무수정.

## 3. API (`backend/api/reports.py`)

- `_Edit`에 필드 1개 + 검증기 1개:
  ```python
  review_date: str | None = Field(default=None, pattern=r"^(\d{4}-\d{2}-\d{2})?$")
  # field_validator: v가 비어 있지 않으면 date.fromisoformat(v) — 2026-02-30 등 무효 날짜는 ValueError → 422
  ```
  Pydantic `date` 타입은 쓰지 않는다(숫자 타임스탬프 등 lax 입력을 받아들이고 `""` 지우기를 표현 못 함).
- `CreateBody`·`PatchBody`는 `_Edit` 상속이라 자동 포함. `extra="forbid"` 유지 → `base_date`·`metrics` 등은 계속 422.
- `PatchBody._any`: `name`·`memo`·`review_date` 셋 다 `None`이면 422.
- `create` → `sqlite.save_report(d, body.name, body.memo, body.review_date)`, `patch` → `sqlite.update_report(report_id, body.name, body.memo, body.review_date)`.
- 응답: `ReportSummary`·`Report` 모두 `review_date`(문자열|null) 포함(`_summary` 경유로 자동). `SaveResult` 불변.
- 형식 오류는 기존 `_Route`가 `{code:"INVALID_REQUEST", message}` 422로 변환, 입력값 에코 없음.

## 4. 화면

### reports.js (D15)
- 목록 행: 기존 텍스트 뒤에 `review_date`가 문자열이고 비어 있지 않을 때만 `" · 재검토 " + review_date` 추가.
- 상세: "기준일" 줄 다음에 `p("재검토 예정일 " + (review_date || "-"))`.
- 수정 버튼 문구 `"이름·메모 수정"` → **`"이름·메모·재검토일 수정"`**.
- 수정 폼: 메모 다음에 `label("재검토 예정일", for edit-review)` + `el("input", {attrs:{type:"date", id:"edit-review"}})`, `value = rep.review_date || ""`. 네이티브 `<input type="date">`라 값은 항상 `""` 또는 `YYYY-MM-DD`.
- `buildPatchBody(cur, name, memo, review)`: `var r = String(review == null ? "" : review); if (r !== (c.review_date || "")) body.review_date = r;` — 비우면 `""` 전송 → 서버가 NULL. 4번째 인자 생략 시 `""`로 취급되므로 `cur.review_date`가 없는 기존 테스트 케이스는 결과 동일.
- `EDIT_NOTE` 갱신(§6-3 문구). `textContent`로만 넣는 규칙 유지.

### chat.js 저장 패널 (D14c) — 추가한다(입력 1개, 단순)
- 메모 다음에 `label("재검토 예정일 (선택)")` + `<input type="date" id=uid+"r">`. 패널을 새 항목으로 열 때 다른 입력과 같은 방식으로 비운다.
- `buildSaveBody(sid, stockCode, nameVal, memoVal, reviewVal)`: `reviewVal`이 비어 있지 않을 때만 `body.review_date` 추가. 4인자 호출(기존 `d14.test.js` 104~106행)은 결과 동일.

## 5. 테스트 관점

백엔드(`tests/test_d12.py`에 추가):
1. 마이그레이션: `review_date` 없는 구 스키마로 테이블을 만들고 행 1개 삽입 → `init_table()` → 컬럼 존재, 기존 행 데이터 동일·`review_date is None`. 재호출 멱등.
2. POST `review_date:"2026-12-01"` → GET 상세·목록에 그대로. 생략 시 `null`.
3. PATCH `{"review_date":"2027-01-02"}` → 200, `name`/`memo` 불변, `updated_at` 증가. `{"review_date":""}` → `null`.
4. 422: `"2026-02-30"`, `"2026/12/01"`, `"20261201"`, `123`(정수) — POST·PATCH 모두. 과거 날짜 `"2000-01-01"`은 201/200.
5. 중복: 같은 `(stock_code, base_date)`에 `review_date`만 다르게 두 번 저장 → 두 번째 `duplicate_of == [첫 id]`.

프런트(`tests/frontend/d15.test.js`, `d14.test.js`에 추가):
- `buildPatchBody`: review만 변경 → `{body:{review_date:"..."}}`, 지우기 → `{review_date:""}`, 같으면 noop(`cur.review_date` null ↔ `""` 동일 취급).
- `buildSaveBody` 5번째 인자: 값 있으면 포함, `""`면 미포함.
- `d15_dom.test.js`: 상세에 "재검토 예정일" 줄, 수정 폼에 `input[type=date]#edit-review`.

### 기존 테스트 영향 (grep 결과)
- `tests/frontend/d15_dom.test.js:39` — `btns.includes("이름·메모 수정")` → 새 버튼 문구로 **수정 필요**(유일한 문구 단언).
- 응답 키 집합을 `set(...) ==`로 단언하는 리포트 테스트 없음(`test_d12.py:173`은 오류 본문 `{code,message}`라 무관).
- `test_d12.py:195` PATCH 금지 필드 목록(`base_date` 등)은 그대로 422 — 영향 없음.
- `save_report`/`update_report` 위치 인자 호출은 기본값 덕에 무수정.
- `tests/test_d16_e2e.py:390` PATCH 응답 `updated_at` 비교 — 영향 없음.

## 6. 상위 문서 수정 (사용자 승인 방향 — 구현 단계에서 반영 예정)

### 6-1. DESIGN.md §8
- 스키마 첫 줄 끝: `disclosures_json, disclaimer, memo, created_at, updated_at)` → `disclosures_json, disclaimer, memo, review_date, created_at, updated_at)`
- `- 수정 가능 필드: \`name\`, \`memo\`만(F3016). 지표 값은 불변 — 근거가 바뀌지 않게.` →
  `- 수정 가능 필드: \`name\`, \`memo\`, \`review_date\`(재검토 예정일, YYYY-MM-DD, 선택)만(F3016). 지표·기준일·산업 평균·공시는 불변 — 근거가 바뀌지 않게.`
- 중복 줄 끝에 추가: ` \`review_date\`는 중복 판정과 무관.`

### 6-2. DESIGN.md §9
- POST 행 요청: `{session_id, stock_code, name?, memo?}` → `{session_id, stock_code, name?, memo?, review_date?}`
- PATCH 행 요청: `{name?, memo?}` → `{name?, memo?, review_date?}` (`""`이면 재검토 예정일 삭제)

### 6-3. reports.js `EDIT_NOTE` (D12a §3-2 대체)
```
이름·메모·재검토 예정일만 수정할 수 있습니다. 지표·기준일은 분석 당시 조회한 근거라 수정할 수 없습니다. 바꾸려면 채팅에서 다시 분석해 새로 저장하세요.
```

### 6-4. README.md §8 "리포트 수정(F3016)" 불릿 교체
```
- 리포트 수정(F3016): 이름(`name`)·메모(`memo`)·재검토 예정일(`review_date`, 다시 볼 날짜)을 고칠 수 있고, 리포트 삭제도 할 수 있다. 재검토 예정일은 저장할 때 지정하거나 나중에 넣고 지울 수 있다. 수정·삭제 결과는 리포트 화면에 바로 반영된다. 종목·기준일·지표 값·산업 평균·공시 목록은 수정할 수 없다. 이 값들은 저장 시점에 조회·계산한 근거를 그대로 남긴 것이라, 손으로 고치면 출처 데이터와 맞지 않는 리포트가 된다. API도 `PATCH /api/reports/{id}`에 `name`·`memo`·`review_date` 외 필드가 오면 422로 거부한다. 다른 기준일이나 종목이 필요하면 채팅에서 다시 분석한 뒤 새 리포트로 저장한다.
```
(기존 마지막 문장 "명세 F3016 예시(…)와 같은 범위다."는 범위가 넓어졌으므로 삭제.)

### 6-5. invest-helper.md 구현 상태 표기
- 120행 `리포트 저장·목록·상세·수정(이름·메모)·삭제` → `리포트 저장·목록·상세·수정(이름·메모·재검토 예정일)·삭제`
- 314행 `구현 (이름·메모 수정)` → `구현 (이름·메모·재검토 예정일 수정)`

### 6-6. 하위 설계 문서
- D12 §5 `ReportSummary`·D15 §7 수정 폼 설명은 본 문서(D12b)가 대체한다. 원문은 고치지 않고 D12b를 참조 기준으로 둔다.

## 7. 구현 시 확인 항목
- `PRAGMA table_info` 확인과 `ALTER`를 `with con:` 안에서 실행해도 `executescript` 직후 커밋 상태와 충돌하지 않는지(실행으로 확인).
- 실제 운영 DB(`config.DB_PATH`) 사본으로 서버 기동 → 기존 리포트 목록·상세가 그대로 나오고 `review_date: null`인지.
- `<input type="date">`의 좁은 화면(모바일 폭) 레이아웃이 `.save-panel`·`.edit-form`에서 깨지지 않는지.
- Pydantic 2.14.2에서 `pattern`이 `str | None`의 `None`에 적용되지 않는지(기본값 `None` 통과 확인).

> D19(`D19-report-core-values.md`)가 수정 가능 필드를 `target_per`·`target_roe`까지 확장한다. 이 문서 원문은 D19를 참조 기준으로 둔다.
