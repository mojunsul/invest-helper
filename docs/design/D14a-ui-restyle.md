# D14a UI 리스타일 — "깔끔한 채팅 앱" 외형 (`frontend/style.css` 교체)

기준: D14 §2·§3·§7·§8·§9·§12(채팅 화면 구조·클래스·접근성), D15 §4·§5·§6·§9·§13(리포트 화면·표·접근성), D13 §8(CSP), `backend/main.py` CSP 헤더. 의사코드/명세이며 구현 아님. **원칙: 기능·API·DOM 불변, CSS만 교체. 빌드·프레임워크·외부 리소스 없음.**

## 1. 범위

| 소관 | 비소관 |
|---|---|
| `frontend/style.css` 전면 재작성: 색 토큰(라이트/다크), 말풍선(사용자 우측·봇 좌측), 간격, 뱃지, 타임라인 패널, 저장 영역, 입력창, 리포트 목록·상세·표(카드형), 오류·빈 상태, 포커스, 반응형 | 동작·API 호출·응답 해석(D14/D15 그대로), 채팅 `answer` 안의 파이프(`\|`) 텍스트 표를 실제 `<table>`로 변환하는 일(JS 변경 필요 → §9), 테마 수동 전환 토글(요청 없음), 아이콘·애니메이션 |

- **상위 계약 불변**: D0 공통 계약·`docs/DESIGN.md`·D1~D15 계약(API·응답 필드·CSP·XSS 규칙·DOM id/class)을 바꾸지 않는다. 상위 계약 요청 없음.
- 변경 파일은 **`frontend/style.css` 하나**. `index.html`·`reports.html`·`chat.js`·`common.js`·`reports.js`는 **변경 없음**(§3 근거).

## 2. 제약 (CSP·테스트)

- CSP `default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'` → 외부 폰트/CDN/`@import url(http…)` 금지. 폰트는 시스템 스택만: 본문 `system-ui, -apple-system, "Segoe UI", "Apple SD Gothic Neo", "Malgun Gothic", "Noto Sans KR", sans-serif`, 고정폭 `ui-monospace, "SF Mono", "D2Coding", Menlo, Consolas, monospace`.
- 이미지·SVG data URI도 쓰지 않는다(`default-src 'self'`에서 `img-src`가 data:를 막음). 장식은 border/background/border-radius/box-shadow만.
- 테스트 영향(grep 확인):
  - `tests/test_d14.py::test_html_static_rules`·`tests/test_d15.py::test_reports_html_rules` — HTML 파일 대상(`style=`·인라인 script·`http(s)://`·`<label for>` 등). HTML을 안 고치므로 영향 없음.
  - `tests/test_d14.py`·`test_d16_e2e.py` — `/style.css` 200 응답만 확인. 내용 검사 없음.
  - `tests/frontend/d14.test.js`·`d15.test.js`·`d15_dom.test.js` — 순수 로직·가짜 DOM(className 문자열 등). CSS를 읽지 않음.
  - → CSS 교체로 깨질 테스트 없음. **CSS 파일에도 `http://`·`https://` 리터럴을 넣지 않는다**(테스트 대상은 아니나 D14 "외부 URL 참조 부재" 정적 규칙 취지 유지).

## 3. 대상 요소 목록 (현재 마크업·JS가 만드는 것 그대로, 변경 없음)

| 화면 | 선택자 | 출처 |
|---|---|---|
| 공통 | `header`, `header h1`, `nav`, `a`, `main`, `footer#disclaimer`, `button`/`input`/`textarea`, `.sr-only`, `[hidden]`, `:focus-visible` | 두 HTML |
| 채팅 | `#banner`(role=alert, hidden 토글), `ol#log`, `#status`, `form#form`, `textarea#msg`, `button#send`, `#help`, `button#new-chat` | index.html |
| 채팅 말풍선 | `li.user > .answer` / `li.bot`(+`.status-asked`·`.status-limit`·`.status-error`, error면 `.error` 추가) / `li.error[role=alert]`(시스템 오류) | chat.js `userBubble`·`renderBot`·`errorBubble` |
| 봇 내부 | `.badge`(answered면 없음), `.answer`(linkify된 텍스트·`a`), `details > summary`(처리 과정 …), `ol.timeline > li.tl-step / .tl-call / .tl-result / .tl-sep` | chat.js `renderBot`·`renderTimeline` |
| 저장 | `.save-area > .save-row(button + span)`, `.save-form`(label·input·textarea·확정·취소 button, hidden 토글), `.save-msg`(aria-live, 오류 시 role=alert), 저장 후 `button:disabled`("저장됨") | chat.js `renderSave` |
| 리포트 | `#msg`(**div**, role=alert), `#list-view`, `p#empty.empty`, `ul#list > li(button.link + 텍스트 + span.del)`, `span.confirm`, `#detail-view`, `button#back`, `#detail`(h2·p·h3·ul), `.tbl-wrap > table.metrics`(caption.sr-only·thead th·tbody th[scope=row]·td.num), `.memo`, `.disclaimer`, `form.edit-form` | reports.html·reports.js |

- **id 충돌 주의**: `#msg`는 index.html에선 `textarea`, reports.html에선 알림 `div`. 현재 CSS의 `#msg { flex:1 … }`가 양쪽에 걸린다. 새 CSS는 입력창을 `#form textarea`로, 알림을 `div#msg`로 **요소 한정 선택자**로 분리한다(마크업 변경 없이 해결).
- "저장 가능 상태"는 클래스가 따로 없다: `.save-area` 존재 = 저장 가능(최신 봇 말풍선에만 남음, D14 §8), 저장 완료 = `.save-row button:disabled`. 이 두 상태를 CSS로 표현한다.

## 4. 색 토큰 (`:root`, 다크 오버라이드)

```
:root {
  color-scheme: light dark;
  --bg:        #f6f7f9;   /* 페이지 */
  --surface:   #ffffff;   /* 카드·봇 말풍선·헤더·입력 바 */
  --surface-2: #f0f2f5;   /* 표 머리·summary·코드성 영역 */
  --fg:        #1f2328;   /* 본문 (surface 대비 ≈15:1) */
  --muted:     #57606a;   /* 보조 텍스트 (surface 대비 ≈6:1) */
  --line:      #d0d7de;
  --accent:    #0969da;   /* 링크·주 버튼·포커스 (surface 대비 ≈5.2:1) */
  --accent-fg: #ffffff;   /* accent 배경 위 글자 */
  --user-bg:   #0969da;  --user-fg: #ffffff;   /* 사용자 말풍선 */
  --err:       #cf222e;  --err-bg: #ffebe9;    /* 오류 (err-bg 대비 ≥4.5:1) */
  --warn:      #7d4e00;  --warn-bg: #fff8c5;   /* limit 뱃지·배너 */
  --info:      #0550ae;  --info-bg: #ddf4ff;   /* asked 뱃지 */
  --shadow:    0 1px 2px rgba(31,35,40,.08);
  --radius: 14px;  --radius-sm: 8px;
}
@media (prefers-color-scheme: dark) {
  :root {
    --bg: #0d1117; --surface: #161b22; --surface-2: #21262d;
    --fg: #e6edf3; --muted: #9198a1; --line: #30363d;
    --accent: #4493f8; --accent-fg: #0d1117;
    --user-bg: #1f6feb; --user-fg: #ffffff;
    --err: #ff7b72; --err-bg: #3d1214;
    --warn: #e3b341; --warn-bg: #3a2a00;
    --info: #79c0ff; --info-bg: #0c2d4f;
    --shadow: none;
  }
}
```

- 색은 **모두 토큰 경유**. 현재 하드코딩(`#fff`, `#e8f0fe`, 배너 `#fff4e5` 등)은 전부 토큰으로 치환.
- `color-scheme: light dark`로 폼 컨트롤·스크롤바도 테마를 따른다.
- 수동 테마 토글은 두지 않는다(요청 범위 밖, JS 필요).

## 5. 레이아웃·간격

- `body`: `margin:0; background:var(--bg); color:var(--fg); line-height:1.55; min-height:100vh(+100dvh 덮어쓰기); display:flex; flex-direction:column`. 본문 폰트 15~16px.
- 공통 폭: `main`, `form#form`의 내용 폭 `max-width: 820px; margin: 0 auto; width:100%`, 좌우 패딩 16px(모바일 12px).
- `header`: `position: sticky; top:0; z-index:1; background:var(--surface); border-bottom:1px solid var(--line)`. h1 1.05rem, `nav` gap 12px, `flex-wrap: wrap`(좁은 폭에서 줄바꿈).
- **입력 바**(`form#form`): `position: sticky; bottom: 0; background: var(--surface); border-top:1px solid var(--line)`; `textarea`는 `flex:1; min-width:0; resize: vertical; border-radius: var(--radius-sm); padding 10px 12px`; `#send`는 주 버튼. `#help`는 `flex-basis:100%`, 0.8rem, muted. (sticky라 HTML 순서 유지한 채 화면 하단 고정. footer는 그 아래 흐름에 남음.)
- `footer#disclaimer`: 작은 글씨(0.8rem), muted, 가운데 정렬, 상단 경계선. 상시 표시(D14 §3 요구) 유지.
- `#status`: muted, `min-height:1.5em`, 말풍선 목록 아래 여백.

## 6. 채팅 요소 스타일

| 요소 | 규칙 |
|---|---|
| `#log` | 마커 없음, `display:flex; flex-direction:column; gap:12px; padding:16px 0` |
| `#log > li` 공통 | `max-width: min(85%, 680px)`(모바일 92%), `padding:10px 14px`, `border-radius:var(--radius)`, `overflow-wrap:anywhere`, `min-width:0` |
| `li.user` | `align-self:flex-end; background:var(--user-bg); color:var(--user-fg); border-bottom-right-radius:4px`(꼬리 느낌). 안의 `.answer`는 **본문 폰트**(`li.user .answer { font-family: inherit }`) — 사용자 글은 표가 없음 |
| `li.bot` | `align-self:flex-start; background:var(--surface); border:1px solid var(--line); box-shadow:var(--shadow); border-bottom-left-radius:4px` |
| `li.error`(시스템 오류·`status=error` 봇 둘 다) | `align-self:flex-start; background:var(--err-bg); border:1px solid var(--err); color:var(--fg)`; 본문 글자는 `--fg` 유지(가독성), 테두리·뱃지로 오류 표시 |
| `.answer` | `white-space:pre-wrap; overflow-wrap:anywhere`(D14 §7 유지), 봇 쪽은 고정폭 폰트 0.92rem(파이프 표 정렬, D14 §7). 링크 `color:var(--accent)`, 밑줄 유지 |
| `.badge` | `display:inline-block; font-size:.75rem; font-weight:600; padding:2px 8px; border-radius:999px; margin-bottom:6px; border:1px solid currentColor` |
| `li.status-asked .badge` | `color:var(--info); background:var(--info-bg)` |
| `li.status-limit .badge` | `color:var(--warn); background:var(--warn-bg)` |
| `li.status-error .badge` | `color:var(--err); background:var(--surface)` (err-bg 위에서 구분되게) |
| answered | 뱃지 없음(JS가 안 만듦) → 규칙 불필요 |
| `#banner` | 경고 카드: `background:var(--warn-bg); color:var(--fg); border:1px solid var(--warn); border-radius:var(--radius-sm); margin:12px auto; max-width:820px; padding:10px 14px` (모바일 좌우 12px 마진) |
| `#new-chat` | 보조(외곽선) 버튼 |

- **처리 과정 패널** (`li.bot details`):
  - `details { margin-top:10px; border-top:1px solid var(--line); padding-top:8px; font-size:.85rem }`
  - `summary { cursor:pointer; color:var(--muted); list-style 기본 삼각형 유지; padding:4px 0; border-radius:var(--radius-sm) }`, `summary:hover { color:var(--fg) }`. 네이티브 `details`라 키보드 토글 그대로, `summary:focus-visible` 윤곽(§8).
  - `ol.timeline { margin:8px 0 0; padding:8px 8px 8px 28px; background:var(--surface-2); border-radius:var(--radius-sm); max-height: 50vh; overflow:auto; overflow-wrap:anywhere }`
  - `.timeline li` 고정폭 0.8rem, `padding:2px 0`; `.tl-call` fg, `.tl-result` muted; `.tl-sep { list-style:none; margin-left:-20px; color:var(--muted); font-weight:600; padding:6px 0 }`.
- **저장 영역**:
  - `.save-area { margin-top:10px; padding-top:10px; border-top:1px dashed var(--line); display:flex; flex-direction:column; gap:6px }`
  - `.save-row button`: accent 외곽선 소형 버튼(`color:var(--accent); border-color:var(--accent)`); `:disabled`("저장됨")는 `color:var(--muted); border-color:var(--line); cursor:default`.
  - `.save-form`: 기존 세로 배치 유지 + `padding:10px; background:var(--surface-2); border-radius:var(--radius-sm); max-width:420px`. 첫 버튼(확정)은 `.save-form button:first-of-type`로 주 버튼 스타일(마크업 변경 없이 구분).
  - `.save-msg`: 0.85rem; `.save-msg[role="alert"] { color:var(--err) }`.

## 7. 리포트 화면 스타일

| 요소 | 규칙 |
|---|---|
| `div#msg` | `div#msg:not(:empty)`일 때만 알림 카드(`background:var(--surface); border:1px solid var(--line); border-left:4px solid var(--accent); padding:10px 14px; border-radius:var(--radius-sm); margin:12px 0`). 빈 상태는 박스 없음. **`display:none` 쓰지 않음**(role=alert live region 유지) |
| `#list` | 마커 없음, `display:grid; gap:10px; padding:16px 0` |
| `#list > li` | 카드: `background:var(--surface); border:1px solid var(--line); border-radius:var(--radius); padding:12px 16px; box-shadow:var(--shadow); color:var(--muted); overflow-wrap:anywhere`. `button.link`는 `display:block; color:var(--fg); font-weight:600; font-size:1rem; margin-bottom:4px`(카드 제목), hover 시 accent·밑줄 |
| `span.del` / `.confirm` | `display:inline-flex; gap:8px; align-items:center; flex-wrap:wrap; margin-top:6px`; "삭제"·"삭제 확정" 버튼은 `#list button:not(.link)`·`.confirm button:first-of-type`로 위험 색(`color:var(--err); border-color:var(--err)`) |
| `p#empty.empty` | 빈 상태 카드: 점선 테두리, 가운데 정렬, muted, padding 24px |
| `#back` | 보조 버튼, 아래 여백 12px |
| `#detail` | 카드(`background:var(--surface); border:1px solid var(--line); border-radius:var(--radius); padding:16px 20px`). `h2` 1.25rem, `h3` 1rem + 상단 여백, 메타 `p` 간격 축소 |
| `.tbl-wrap` | `overflow-x:auto; max-width:100%; border:1px solid var(--line); border-radius:var(--radius-sm); margin:12px 0` — **넓은 표는 래퍼 안에서만 가로 스크롤** |
| `table.metrics` | 카드형: `border-collapse:collapse; width:100%; min-width:320px`; `th,td { padding:8px 12px; border-bottom:1px solid var(--line); text-align:left }`; `thead th { background:var(--surface-2); font-size:.85rem; color:var(--muted) }`; `tbody th { font-weight:600 }`; `tbody tr:last-child > * { border-bottom:0 }`; `td.num { text-align:right; font-variant-numeric: tabular-nums }`; 줄무늬 `tbody tr:nth-child(even) { background:var(--surface-2) }`(선택) |
| `.memo` | `white-space:pre-wrap; overflow-wrap:anywhere; background:var(--surface-2); padding:10px 12px; border-radius:var(--radius-sm)` |
| `.disclaimer` | `pre-wrap`, 0.8rem, muted, 상단 경계선 |
| `form.edit-form` | `.save-form`과 같은 규칙 공유(선택자 묶기), 제출 버튼 `button[type="submit"]` 주 버튼 |

## 8. 공통 컨트롤·접근성

- **버튼 기본**: `font:inherit; padding:6px 14px; border-radius:var(--radius-sm); border:1px solid var(--line); background:var(--surface); color:var(--fg); cursor:pointer; min-height:36px`. hover 시 `background:var(--surface-2)`. `:disabled { opacity:.55; cursor:not-allowed }`.
- **주 버튼**(`#send`, `.save-form button:first-of-type`, `.edit-form button[type="submit"]`): `background:var(--accent); color:var(--accent-fg); border-color:var(--accent)`.
- `button.link`는 기본 버튼 규칙을 덮어 배경·테두리·패딩 없음(D15 동작 유지).
- **입력**(`input`, `textarea`): `font:inherit; background:var(--surface); color:var(--fg); border:1px solid var(--line); border-radius:var(--radius-sm); padding:8px 10px`. `::placeholder { color:var(--muted) }`.
- **포커스**: `:focus-visible { outline:2px solid var(--accent); outline-offset:2px }` 유지(모든 버튼·링크·summary·입력). 입력창은 추가로 `border-color:var(--accent)`. `outline:none` 금지. 사용자 말풍선(accent 배경) 안에는 포커스 가능한 요소가 없어 윤곽 대비 문제 없음.
- **대비**: 본문·보조 텍스트·링크·뱃지 텍스트는 각 배경 대비 4.5:1 이상(§4 값 기준, 구현 시 실측 §11). 상태는 색만이 아니라 뱃지 텍스트로 전달(D14 §12 유지).
- `prefers-reduced-motion`: 전환 효과를 쓰지 않으므로 불필요.
- `.sr-only`, `[hidden] { display:none !important }` 그대로 유지(`hidden` 토글을 JS가 사용).

## 9. 반응형

- `html, body { overflow-x: hidden }`은 **쓰지 않는다**(넘침을 가리면 원인이 숨음). 대신 넘칠 수 있는 요소마다 처리: `.answer`·`#list > li`·`.timeline`은 `overflow-wrap:anywhere`, 표는 `.tbl-wrap` 스크롤, 모든 flex 자식 `min-width:0`, 입력은 `max-width:100%`.
- `@media (max-width: 600px)`: 말풍선 `max-width:92%`, 좌우 패딩 12px, `header` h1 0.95rem·`nav` 줄바꿈, `.save-form`/`.edit-form` `max-width:none`, `#detail` padding 14px, 표 셀 padding 6px 8px.
- **채팅 `answer`의 파이프 텍스트 표**: 서버(D9 `respond.py`)가 마크다운 형식 텍스트로 보낸다. `<table>` 변환은 JS 파싱이 필요해 본 단위 범위 밖. 본 단위는 고정폭 + `pre-wrap`으로 유지(좁은 폭에서는 줄바꿈되어 정렬이 깨질 수 있음 — 기존과 동일). 정렬을 지키려 `white-space:pre` + 말풍선 내부 가로 스크롤로 바꾸면 긴 산문 줄도 줄바꿈되지 않으므로 채택하지 않는다. 채팅 표 카드화가 필요하면 후속 D14b(JS에서 `|` 행 블록만 `<table>`로 렌더, textContent 유지)로 제안.

## 10. 검증 방법

- **자동(기존 테스트 그대로 통과)**: `pytest tests/test_d14.py tests/test_d15.py tests/test_d16_e2e.py`(HTML 정적 규칙·정적 서빙 200·CSP 헤더) 및 `node --test tests/frontend/`(d14·d15·d15_dom). 전체 `pytest` 회귀 통과. 추가 테스트는 만들지 않는다(CSS 전용 변경, 스타일 단위 테스트는 가치 낮음).
- **정적 점검**: `git diff --stat`에 `frontend/style.css`만 나타남; `style.css`에 `http`·`@import`·`url(` 없음(grep); 색 리터럴은 `:root`/다크 블록 안에만(그 밖 규칙은 `var(--…)`만 — grep `#[0-9a-fA-F]{3,6}`로 확인, `rgba` 그림자 토큰 예외).
- **수동 브라우저 체크리스트**(서버 기동 후 Chrome·Safari 중 1개 이상, 라이트·다크 각각 — macOS 시스템 설정 또는 DevTools "Rendering › prefers-color-scheme" 에뮬레이션):
  1. 채팅: 사용자 말풍선 우측·봇 좌측, 간격 여유, 긴 메시지 줄바꿈, 페이지 가로 스크롤 없음.
  2. 대표 요청(DESIGN §10): answered(뱃지 없음), asked("추가 정보 필요"), limit("조건 완화 제안"), error("처리 실패" + 오류 말풍선) 뱃지 색·텍스트 구분.
  3. "처리 과정 (단계 N · 도구 호출 N · 재계획 N)" 접기/펼치기(마우스·키보드 Enter/Space), 재계획 구분선 `.tl-sep` 구분, 긴 JSON 줄바꿈, 패널 내부 스크롤.
  4. 저장: [저장] → 폼 펼침(확정 주 버튼) → 저장 성공 문구·링크, 버튼 "저장됨" 비활성 모양, 오류 시 빨간 문구. 새 응답 후 이전 말풍선 저장 영역 사라짐(기존 동작).
  5. "새 대화" 버튼 모양, 전송 중 입력·전송·새 대화 disabled 모양, "분석 중…" 상태 줄.
  6. 입력 바 하단 고정(스크롤해도 보임), Enter/Shift+Enter 동작 불변, 포커스 윤곽 보임.
  7. 키 누락 배너(`.env` 키 제거) 경고 카드, 서버 종료 상태 전송 → 네트워크 오류 말풍선.
  8. 면책 footer 상시 표시.
  9. 리포트: 목록 카드, 빈 목록 안내 카드, 삭제 → 확인 줄(위험 색) → 결과 알림 카드, 상세 카드·지표 표(숫자 우측 정렬·산업 평균 열)·메모·면책, 이름·메모 수정 폼, 존재하지 않는 `#id=999` → 알림.
  10. 폭 360px(DevTools 기기 모드): 두 페이지 모두 페이지 가로 스크롤 없음, 표는 `.tbl-wrap` 안에서만 스크롤, 헤더 줄바꿈 정상.
  11. 키보드만으로 두 페이지 전 기능 도달, Tab 순서 불변, 모든 포커스 대상에 윤곽.
  12. DevTools Console에 CSP 위반 0건.

## 11. 구현 시 확인 항목 (FAIL 아님)

- §4 색 값의 실제 대비(DevTools 대비 표시 또는 계산): 특히 다크 `--muted`/`--surface-2`, `--err` on `--err-bg`, 뱃지 텍스트/배경, 다크 `--user-fg` on `--user-bg`(≈4.6:1 경계). 부족하면 값만 조정.
- `position: sticky` 입력 바가 iOS Safari에서 가상 키보드와 겹치는지, `100dvh` 미지원 브라우저 폴백(`100vh` 먼저 선언).
- 고정폭 폰트에서 한글 전각폭으로 파이프 표 정렬이 어긋나는 정도(D14 §13과 동일 항목) — 폰트 후보 순서만 조정.
- `.save-form button:first-of-type`가 확정 버튼을 정확히 가리키는지(현재 chat.js 자식 순서: label, input, label, textarea, 확정, 취소). chat.js 순서가 바뀌면 선택자도 갱신.
- `#list button:not(.link)`가 "삭제"/"취소"까지 위험 색으로 칠하는지 — "취소"는 `.confirm button:last-of-type`로 기본색 복원 필요 여부 확인.
- `div#msg:not(:empty)`: JS가 빈 문자열로 지울 때 `:empty`가 성립하는지(텍스트 노드 잔존 여부) 실제 확인.
- 다크 모드에서 `details` 기본 삼각형·`select`/스크롤바 색이 `color-scheme`을 따르는지.
- 간격·반경·폰트 크기·브레이크포인트(600px)·`max-height:50vh` 등은 임의 값.
