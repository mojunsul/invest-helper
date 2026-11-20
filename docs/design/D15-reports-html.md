# D15 저장 리포트 화면 (`frontend/reports.html`, `frontend/reports.js`, `common.js`·`style.css` 확정)

기준: DESIGN §1·§8·§9, D12 §2·§5·§6·§8·§9(응답 모델·오류 code·XSS는 D15 책임), D13 §8(CSP: 인라인 script·`on*=` 금지, 오류 `{code,message}`), D14(공유 `common.js`/`style.css`, 구현됨), D9 `respond.py`(`REASON_KO`·`METRICS`·`fmt`·`PCT`·`DISCLAIMER`), `agent/state.py`(`MetricRow`). 의사코드/명세이며 구현 아님. 상위 계약은 수정하지 않는다(§11). **원칙: 바닐라 JS, 빌드·프레임워크 없음, 구현이 가장 단순한 쪽.**

## 1. 범위

| 소관 | 비소관 |
|---|---|
| 목록, 상세(지표·산업평균·공시·면책), 이름/메모 수정(PATCH), 삭제(DELETE), 오류 문구, 빈 목록 안내, 채팅 화면 링크 | API·DB(D12), 저장 버튼(D14), 지표 수정(계약상 불가), 검색·정렬·페이지네이션(없음; 서버가 `created_at` 내림차순) |

- 화면은 **단일 페이지 2뷰**: 목록 뷰 / 상세 뷰를 같은 `reports.html` 안에서 JS로 전환(URL 해시 `#id=3` 사용: 새로고침·뒤로가기 유지, 서버 라우트 추가 없음). 별도 `report.html`을 만들지 않는다(파일 수 최소; D13 §12 정적 서빙은 `html=True`라 `/reports.html`만 유효).
- 리포트는 **스냅샷**이다(D12 §7). 수정 가능한 것은 이름·메모뿐이고 UI도 그 두 필드만 제공한다.

## 2. 파일 구성 (CSP `script-src 'self'` 준수)

| 파일 | 변경 | 비고 |
|---|---|---|
| `frontend/reports.html` | 신규 | `<script src="common.js">` → `<script src="reports.js">` 두 줄만. 인라인 `<script>`·`on*=`·`style=`·`javascript:`·외부 URL 금지(D14 정적 점검 규칙 동일 적용) |
| `frontend/reports.js` | 신규 | 상단 "순수 영역"(§8) + `init`. 끝에 `if (typeof module !== "undefined") module.exports = {...}` (D14 방식 동일) |
| `frontend/common.js` | **한 줄만 수정**(§3) | 추가 함수 없음 |
| `frontend/style.css` | **추가만**(§9) | 기존 규칙 불변. 파일 끝에 D15 블록 append |
| `frontend/index.html`·`chat.js` | 변경 없음 | `href="reports.html"` 링크는 D14에 이미 있음 |

## 3. `common.js` 확정 (D14 코드 보존)

D14가 제안한 `el / safeUrl / splitUrls / linkify / api / storageGet·Set / fmtTime / STATUS_TEXT`를 **이름·시그니처 그대로 확정**한다. D15는 `el`·`safeUrl`·`api`·`fmtTime`만 사용(`linkify`·storage는 미사용).

- **유일한 수정: `REASON_TEXT = {}` 를 채운다.** D15가 채우기로 결정(근거: `MetricRow.reasons` 코드를 사용자 문구로 보여야 하는 곳이 D15 상세뿐이고, 서버 `REASON_KO`는 서버 쪽 상수라 프론트가 import 불가). 키 9개는 `state.ReasonCode`와 1:1, 값은 `respond.REASON_KO`와 **동일 문구**:
  `THRESHOLD_NOT_MET:"기준 미충족"`, `NO_FINANCIALS:"재무정보 없음"`, `FETCH_FAILED:"조회 실패(일시 오류 가능)"`, `NO_MARKET_CAP:"시가총액 확인 불가"`, `NEGATIVE_EARNINGS:"순이익 적자(PER 산출 불가)"`, `NEGATIVE_EQUITY:"자본잠식"`, `NONPOSITIVE_REVENUE:"매출 0 이하"`, `NONPOSITIVE_ASSETS:"자산 0 이하"`, `INDUSTRY_AVG_UNAVAILABLE:"산업 평균 산출 불가"`.
- D14 하위호환 근거: `REASON_TEXT`는 `chat.js`·`d14.test.js`·`test_d14.py` 어디서도 참조되지 않음(grep 확인) → 값 채움은 D14 동작·테스트에 영향 없음. export 형태(`Common.REASON_TEXT`)·다른 함수는 무변경. 매핑에 없는 코드는 **코드 원문 표시**(D14 §4 규칙).
- 지표 라벨(`METRIC_LABEL`)·단위(`PCT`)·지표 순서는 reports.js 내부 상수로 둔다(common.js에 넣지 않음: D14가 안 쓰므로 공유 대상 아님). 값은 D9/`analyze.METRIC_LABEL`과 동일: `ROE,ROA,OPM(영업이익률),PER,PBR,PSR,DEBT_RATIO(부채비율)`, 퍼센트 지표 `{ROE,ROA,OPM,DEBT_RATIO}`는 `x.xx%`, 나머지 `x.xx배`(D9 `fmt`와 동일).

## 4. 화면 구조 (`reports.html` 정적 골격)

```
<html lang="ko"> head: charset/viewport/<title>저장된 리포트</title>/link style.css
<body>
  <header> <h1>저장된 리포트</h1> <nav><a href="index.html">채팅으로 돌아가기</a></nav>
  <main>
    <div id="msg" role="alert" aria-live="polite"></div>     # 전역 오류/결과 한 줄 (§7)
    <section id="list-view">  <p id="empty" hidden>…</p>  <ul id="list">
    <section id="detail-view" hidden>  <button type="button" id="back">← 목록</button>  <div id="detail"></div>
  <footer id="disclaimer">투자 권유가 아니며 전문가 조언을 대신하지 않습니다.</footer>
  <script src="common.js"></script> <script src="reports.js"></script>
```

- 링크는 상대 경로, 확장자 포함(`index.html`; D13 §12). 동적 요소는 전부 `Common.el`로 생성.
- footer 면책은 정적 폴백. 상세 뷰에서는 **그 리포트의 `disclaimer`**(저장 시점 문구)를 상세 본문 하단에 별도 표시한다(footer는 건드리지 않음 — 단순).

## 5. 목록 뷰

```
loadList():
  r = await Common.api("GET", "/api/reports")
  if !r.ok: showMsg(errText(r)); 목록 비움; return
  rows = Array.isArray(r.data) ? r.data : []
  if rows 비었음: #empty 표시("저장된 리포트가 없습니다. 채팅 화면에서 종목을 조회한 뒤 [저장]을 눌러 보세요." + 채팅 링크) ; return
  각 rows: <li> [<button class="link">{name}</button>] {stock_name}({stock_code}) · 기준일 {base_date ?? "-"} · 저장 {created_at 표시}  [삭제 버튼]
```

- **메모는 목록에 표시하지 않는다**: `ReportSummary`에는 `memo`가 없다(D12 §5). N+1 상세 호출은 하지 않는다 → **목록은 이름·종목·기준일·저장시각만, 메모는 상세에서 표시**(요구의 "메모"는 상세로 충족, 구현 단순). 목록에서 메모가 필요하면 §11 요청 참고.
- 항목 클릭(이름 버튼) → `location.hash = "#id={id}"` → 상세 뷰. `hashchange` 리스너 하나로 뷰 전환(§6).
- 저장시각 표시: `created_at`(UTC ISO) → 로컬 `YYYY-MM-DD HH:MM`. `Common.fmtTime`은 시각만 주므로 reports.js의 `fmtDateTime(iso)`(순수, 파싱 실패 시 원문)를 둔다. `base_date`는 불투명 문자열(D12 §2: `"2024Q4"` 류일 수도) → **그대로 textContent**, None이면 `-`.
- 각 항목 삭제 버튼은 §7 공통 삭제 흐름. 정렬은 서버 순서 그대로(재정렬 안 함).

## 6. 상세 뷰

```
route():                                   # 초기 로드와 hashchange에서 호출
  id = parseHashId(location.hash)          # /^#id=(\d+)$/ 이고 ≥1일 때만 정수, 아니면 null
  id == null ? showList() : showDetail(id)

showDetail(id):
  r = await Common.api("GET", "/api/reports/"+id)
  404 REPORT_NOT_FOUND → showMsg("리포트를 찾을 수 없습니다(이미 삭제됐을 수 있습니다).") + 목록 링크(뷰는 목록으로)
  기타 오류 → showMsg(errText(r)) ; 성공 → renderDetail(r.data)
```

`renderDetail(rep)`는 방어적 읽기(`typeof`/`Array.isArray`/`isObject` 가드)로 아래를 순서대로 그린다. 모든 문자열은 textContent.

1. **헤더**: `name`(h2), `{stock_name} ({stock_code})`, 기준일, 저장 `created_at`, 수정 `updated_at`(created와 다를 때만).
2. **지표 표**(`<table>` + `<caption class="sr-only">`, `<th scope>`): 행은 고정 순서 7지표. 열: 지표 | 값 | 산업 평균.
   - 값 셀: `metrics.values[m]`가 number면 `fmtMetric(m,v)`; 아니면 `REASON_TEXT[metrics.reasons[m]] || "재무정보 없음"`(D9 lookup 표기와 동일 규칙; 매핑에 없는 reason 코드는 코드 원문).
   - 산업 평균 셀: `industry_avg[m]`가 `{value,n}`이고 `value`가 number면 `fmtMetric + " (n=" + n + ")"`; 그 외(없음/`value` null)는 `-`. `industry_avg`가 `{}`(lookup)이면 열 전체가 `-`이므로 **열 자체를 생략**(모든 행에서 값이 없을 때).
   - `metrics.values`에 없는 지표 행도 `-`/reason으로 그린다(키 누락 방어). 지표 7개 외 추가 키는 무시.
3. **재무기준 줄**: `metrics.fiscal_period`(없으면 `-`) + `metrics.estimated === true`이면 `*`. `estimated`면 주석 줄 "* 요청 기준 연도 재무가 없어 직전 확정 연도 값으로 대체(추정)"(D9 문구 동일). `actual_basis`가 있고 `fiscal_period`와 다르면 "시세·재무 기준: {actual_basis}" 한 줄 추가.
4. **공시** (D12 §9 3상태 구분):
   - `disclosures === null` → "공시는 조회하지 않았습니다."
   - `[]` → "관련 공시가 없습니다."
   - 비어있지 않은 배열 → `<ul>`, 항목 `{title, date, url}`: `date`(문자열일 때만) + 제목. 제목은 textContent; `Common.safeUrl(url)`이 통과하면 제목을 `<a href target="_blank" rel="noopener noreferrer">`로 감싸고, **null이면 링크 없이 텍스트만**(`javascript:`·`data:` 등 차단). `title`이 문자열이 아니거나 항목이 객체가 아니면 해당 항목 건너뜀.
   - 그 외 타입(배열도 null도 아님) → "공시 정보를 표시할 수 없습니다."(방어).
5. **메모**: `memo`가 비어있으면 "메모 없음", 아니면 `<p class="memo">`(CSS `white-space: pre-wrap`, 줄바꿈 보존; textContent).
6. **면책**: `rep.disclaimer`(비어있지 않은 string)를 `<p class="disclaimer">`로 표시, 아니면 footer 정적 문구에 맡김.
7. **편집·삭제 영역**(§7).

## 7. 수정·삭제·오류 처리

### 수정 (PATCH)
- (D12b가 이 수정 폼 설명을 대체한다: 재검토 예정일 추가, 버튼 `[이름·메모·재검토일 수정]`.) 상세 하단 `[이름·메모 수정]` 버튼 → 인라인 폼 펼침: `<label for>` 연결된 이름 `<input maxlength=100>`(현재값 채움), 메모 `<textarea maxlength=1000>`(현재값), `[저장]`·`[취소]`. `prompt()`/`confirm()` 사용 안 함.
- **body 구성(순수 함수 `buildPatchBody(cur, nameInput, memoInput)`)**: `name = nameInput.trim()`; 빈 문자열이면 **클라이언트에서 막고** "이름은 비울 수 없습니다."(서버는 빈 name을 422, D12 §4). `memo`는 길이 0 허용(비우기 = 메모 삭제). **현재값과 다른 필드만** body에 포함. 둘 다 같으면 요청하지 않고 "변경 사항이 없습니다."(서버가 빈 본문을 422로 거부하므로). `name`/`memo` 외 키는 절대 보내지 않는다(`extra=forbid`).
- 처리 중 폼 버튼 disabled(중복 방지). 성공(200)이면 응답 `Report`로 `renderDetail` 재렌더 + "수정했습니다." 메시지. 실패 시 폼 유지(입력 보존).

### 삭제 (DELETE)
- 목록·상세 공통 `[삭제]` 버튼 → **확인 단계**: `confirm()`을 쓰지 않고(D14와 동일 원칙, 테스트·접근성 일관) 같은 위치에 인라인 "정말 삭제할까요? [삭제 확정] [취소]"로 전환(버튼 두 개 생성/제거만, 상태 변수 불필요). 확정 시 `DELETE`(204, `data=null`).
- 성공: 목록 뷰에서는 해당 `<li>` 제거 후 비면 빈 목록 안내; 상세 뷰에서는 `location.hash=""`로 목록 복귀 + "삭제했습니다." 404 `REPORT_NOT_FOUND`도 "이미 삭제된 리포트입니다."로 안내하고 목록에서 제거(목적 달성으로 취급).

### 오류 문구 (순수 함수 `errText(r)`)
`r = {ok:false,status,data:{code,message}}`. 고정 문구를 우선하고(서버 `message`는 고정 문구라 안전하나 반사 위험 0이라도 textContent), 없으면 서버 message, 그것도 없으면 일반 문구.

| code | 문구 |
|---|---|
| `REPORT_NOT_FOUND` | 리포트를 찾을 수 없습니다(이미 삭제됐을 수 있습니다). |
| `INVALID_REQUEST` | 서버 `message`(필드명 수준) 표시: 입력 길이(이름 1~100자, 메모 0~1000자)를 확인하세요. |
| `INTERNAL_ERROR` | 서버 `message`(`요청을 처리하지 못했습니다.`) |
| `NETWORK_ERROR`(status 0) | 서버에 연결하지 못했습니다. |
| 그 외(`HTTP_ERROR` 포함) | `data.message` 또는 "요청에 실패했습니다." |

`DRAFT_NOT_FOUND`는 D15 호출에서 발생하지 않으므로 매핑하지 않는다(알 수 없는 코드 폴백으로 충분). 메시지는 `#msg` 한 곳에 표시(새 동작마다 덮어씀, `role="alert"`).

## 8. 순수 로직 분리 (`reports.js` 상단, node 테스트 대상)

DOM 비접근 함수만 `module.exports`: `fmtMetric(metric, v)`, `metricCell(metrics, m)`(값 or reason 문구), `avgCell(industryAvg, m)`, `disclosureView(disclosures)` → `{kind:"none"|"empty"|"list"|"invalid", items:[{title,date,href|null}]}`(여기서 `Common.safeUrl` 사용), `parseHashId(hash)`, `buildPatchBody(cur, name, memo)` → `{error}` | `{noop:true}` | `{body}`, `errText(r)`, `fmtDateTime(iso)`. DOM 조립 함수(`renderDetail` 등)는 `init`에서만 호출하며, `typeof document !== "undefined"`일 때만 `init` 실행(D14 방식).

## 9. `style.css` 추가분 (기존 규칙 불변, 파일 끝 append)

목록 `ul#list`(마커 제거·항목 테두리), `.link`(버튼을 링크처럼), `table.metrics`(`border-collapse`, `th/td` 패딩·테두리, 숫자 열 우측 정렬), `.memo`/`.disclaimer`(`white-space: pre-wrap; overflow-wrap: anywhere`), `.edit-form`(D14 `.save-form` 재사용 가능하면 클래스 재사용), `.confirm`(삭제 확인 줄), `.empty`. 좁은 폭에서 표는 `overflow-x: auto` 래퍼 안에 둔다(페이지 가로 스크롤 방지). `[hidden]`·`.sr-only`·`:focus-visible`은 D14 정의 재사용. 색은 기존 CSS 변수 사용, 임의 값 허용.

## 10. XSS·보안 규칙 (D12 §8 인계 이행)

- 서버/사용자 유래 문자열(`name`, `memo`, `stock_name`, `stock_code`, `base_date`, `fiscal_period`, `actual_basis`, 공시 `title`·`date`·`url`, reason 코드, 오류 `message`)은 **전부 `textContent`/텍스트 노드**. `innerHTML`·`outerHTML`·`insertAdjacentHTML`·`document.write`·`eval`·`new Function` 금지(정적 점검, D14 `test_js_static_rules`가 `frontend/*.js` 전체에 적용되므로 자동 포함).
- 링크는 공시 `url`뿐이며 `Common.safeUrl`(http/https만)을 통과해야 `<a>` 생성, 항상 `target="_blank" rel="noopener noreferrer"`. `javascript:`·`data:`는 텍스트만.
- 해시 값은 정수 정규식 통과분만 경로에 사용(`/api/reports/` + 정수). 문자열을 그대로 URL·DOM에 넣지 않음.
- 이벤트는 전부 `addEventListener`(속성 핸들러 없음). CSP `script-src 'self'` 위반 요소 없음.

## 11. 상위 계약 요청 (직접 수정 안 함, 승인 대기)

없음. 참고(요청 아님):
- 목록에 메모를 보여 주려면 D12 `ReportSummary`에 `memo`(또는 요약)를 추가해야 한다. 현재는 상세에서만 표시(§5)하는 것으로 충분하다고 판단 — 사용자가 목록 메모 표시를 필수로 원하면 D12 변경 요청으로 올려 달라.
- `common.js`는 `REASON_TEXT` 값 채움만 변경(§3): D14 문서 §4 표의 "`REASON_TEXT` … D15 공유" 취지와 일치, D14 문서 개정 불필요.

## 12. 테스트 관점 (브라우저 없이 가능한 범위; D14와 동일 방식)

- **node 단위**(`tests/frontend/d15.test.js`, `node:test`): `fmtMetric`(% / 배 / null→`-`), `metricCell`(값, null+reason→문구, null+모르는 reason→코드 원문, reason 없음→"재무정보 없음", 키 누락), `avgCell`(정상 `n` 표기, `value:null`, `{}`), `disclosureView`(null→none, `[]`→empty, 정상 목록 href, `javascript:`/`data:`/상대경로 url→href null, `title` 누락 항목 건너뜀, 비배열 객체→invalid), `parseHashId`(`#id=3` 통과; `#id=0`·`#id=-1`·`#id=1.5`·`#id=3abc`·`#id=<script>`·빈 해시→null), `buildPatchBody`(변경 필드만 포함, 둘 다 동일→noop, 빈 name→error, memo 빈 문자열 허용, 여분 키 없음), `errText`(코드별 문구, 알 수 없는 코드 폴백, 네트워크), `fmtDateTime`(정상/파싱 실패 원문), `Common.REASON_TEXT` 9키가 `state.ReasonCode`와 일치(키 집합 비교는 pytest에서 `typing.get_args`와 대조).
- **pytest 래퍼·정적 점검**(`tests/test_d15.py`, D14와 동일 구조): node 테스트 실행(node 없으면 skip), `reports.html` 정적 규칙(`test_html_static_rules`는 `frontend/*.html` 전체 glob이라 자동 포함; 추가로 `<script src>`가 `["common.js","reports.js"]`, `href="index.html"` 존재, `<label for>`↔`id` 일치는 D15 전용 검사), JS 금지 토큰 점검은 D14 `test_js_static_rules` 자동 포함, `REASON_TEXT` 키 = `ReasonCode` 집합.
- **TestClient 정적 서빙**: `/reports.html`·`/reports.js` 200, CSP `script-src 'self'`·`nosniff` 헤더(D14 `test_static_served_with_csp`와 동일 패턴), `/reports`(확장자 없음)는 404임을 확인해 링크가 확장자를 포함해야 함을 고정.
- **D14 회귀**: 기존 `tests/frontend/d14.test.js`·`tests/test_d14.py` 무수정 통과(common.js 변경은 `REASON_TEXT` 값 채움뿐).
- **수동 확인 시나리오**(브라우저, 시연 전 1회): (1) DB 비어 있을 때 빈 목록 안내+채팅 링크. (2) 채팅에서 screen 종목·lookup 종목·공시 있는/없는/조회 안 한 케이스 각각 저장 후 목록·상세 확인(공시 3상태 문구, `*` 추정 표기, 산업 평균 열 유무, 값 없는 지표의 사유 문구). (3) 이름/메모 수정 → 지표·기준일 불변, `updated_at` 갱신; 빈 이름 클라 차단; 101자 이름·1001자 메모는 `maxlength`로 입력 제한. (4) 삭제 확인→취소/확정, 상세에서 삭제 후 목록 복귀, 다른 탭에서 이미 삭제한 항목 삭제/조회 → 404 문구. (5) 서버 종료 상태 → 네트워크 오류 문구. (6) XSS: `name`/`memo`에 `<img src=x onerror=alert(1)>`·`<script>`, 공시 `javascript:` URL(DB에 직접 삽입)이 텍스트로만 보이고 Console CSP 위반 0건. (7) `#id=abc`·존재하지 않는 id 직접 접속. (8) 키보드만으로 목록→상세→수정→삭제 확인 가능, 뒤로가기로 뷰 복귀.

## 13. 접근성 최소 요건

D14 §12 동일: `lang="ko"`, 폼 컨트롤마다 `<label>`, 표는 `<caption>`·`<th scope>`, 오류/결과 `#msg`에 `role="alert"`, 모든 동작은 `<button>`/`<a>`(div 클릭 금지), 삭제 확인 전환 시 새 버튼으로 포커스 이동, `:focus-visible` 유지, 색만으로 상태를 전달하지 않음, 좁은 폭에서 가로 스크롤 없음(표만 `overflow-x:auto`).

## 14. 구현 시 확인 항목 (FAIL 아님)

- `base_date` 실제 값 형식(D12 §11: screen 파싱값 vs lookup `actual_basis`)이 목록에서 어색하지 않은지 실데이터 확인(표시는 원문 그대로).
- `industry_avg`의 실제 키 구조(`{"PER":{"value","n"}}`)가 screen draft에서 지표 7개 전부가 아니라 일부일 수 있음 — 없는 키는 `-`로 처리(§6)되는지 실데이터 확인.
- 목록 건수가 많아질 때 렌더 성능·무한 길이(페이지네이션 없음; 필요 시 후속).
- `created_at` UTC → 로컬 표시 포맷·타임존, `fmtDateTime` 파싱 실패 케이스.
- 해시 라우팅과 `StaticFiles(html=True)` 상호작용(새로고침 시 `/reports.html#id=3` 정상 서빙) 실호출 확인.
- 삭제 확인 인라인 UI 포커스 이동·키보드 흐름 실사용 확인; 불편하면 `confirm()` 허용으로 완화(구현 판단).
- 지표 표 라벨·순서가 D9 `METRIC_LABEL`/`METRICS`와 어긋나지 않는지(D9 상수 변경 시 reports.js 동기화 필요 — 단순 중복으로 수용).
- `REASON_TEXT` 문구가 D9 `REASON_KO`와 drift하지 않는지(테스트가 키만 비교; 문구 비교는 구현 시 pytest로 `REASON_KO`와 대조 가능).
- 목록 항목 수정 후 목록 `name` 갱신 반영(상세 → 목록 복귀 시 재조회로 단순 처리).
