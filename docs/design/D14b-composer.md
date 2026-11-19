# D14b 입력창(컴포저) 개편 — 떠 있는 둥근 카드형 입력

기준: D14 §3(정적 골격)·§5(새로고침 안내 문구)·§6(전송 중 상태)·§9(Enter/Shift+Enter)·§11(정적 규칙)·§12(접근성), D14a §4(색 토큰)·§5(입력 바)·§8(컨트롤·포커스)·§9(반응형), D13 §8(CSP). 의사코드/명세이며 구현 아님. **원칙: 동작·API·id 불변, CSS 교체 + `index.html` 최소 변경. JS 변경 없음.**

사용자 피드백: "입력창 디자인이 너무 별로다" — 현재는 전폭 sticky 바 + 테두리 있는 textarea(리사이즈 손잡이 노출) + placeholder 없음 + 긴 도움말.

## 1. 범위

| 소관 | 비소관 |
|---|---|
| `form#form` 영역 외형: 둥근 카드(채팅 열 폭, 가운데, 하단에 떠 있음·그림자), 테두리 없는 textarea + placeholder, 리사이즈 손잡이 제거, 내용 따라 자동 높이(최대 높이 후 내부 스크롤), 카드 안 우측 소형 전송 버튼, 카드 전체 포커스 링(`:focus-within`), 전송 중(비활성) 모양, 도움말 소형·단축 | 전송/키 처리 로직(chat.js 그대로), 아이콘 버튼(§4 결정), 빈 입력 시 전송 버튼 흐리게 표시, 글자 수 카운터, 테마 토글, 애니메이션 |

- **상위 계약 불변**: D0 공통 계약·`docs/DESIGN.md`·D1~D15 계약(API·응답 필드·CSP·XSS 규칙·DOM id) 변경 없음. 상위 계약 수정 요청 없음.
- 변경 파일: **`frontend/style.css`**(§5 규칙 교체), **`frontend/index.html`**(§3 세 군데). `chat.js`·`common.js`·기타 파일 변경 없음.

## 2. 현재 동작 확인 (chat.js, 변경 없음)

| 사용처 | 내용 | 본 단위 영향 |
|---|---|---|
| `$("form")`, `$("msg")`, `$("send")` | id로 조회 | id 유지 → 영향 없음 |
| `setBusy(b)` | `msg.disabled = send.disabled = newChat.disabled = b`, `form.setAttribute("aria-busy", …)`, `#status`에 "분석 중입니다…" | CSS가 `#form[aria-busy="true"]`·`:disabled`로 전송 중 모양 표현(JS 추가 없음) |
| `form` `submit` / `msg` `keydown` | Enter(조합 중 제외) 전송, Shift+Enter 줄바꿈 | 그대로 |
| 응답 후 `msg.value = ""; msg.focus()` | 값 비움 | `field-sizing: content`는 값 변경에 따라 높이 자동 복귀 → JS 불필요 |

- `#help`는 JS가 건드리지 않는다(정적 텍스트).

## 3. `index.html` 변경 (정확히 이것만)

```html
<form id="form">
  <label for="msg" class="sr-only">메시지</label>
  <div class="composer">
    <textarea id="msg" maxlength="2000" rows="2" required aria-describedby="help"
              placeholder="예: 반도체 중 ROE 10% 이상인 종목"></textarea>
    <button type="submit" id="send">전송</button>
  </div>
  <p id="help">Enter 전송 · Shift+Enter 줄바꿈 · 새로고침하면 화면 기록은 지워지지만 맥락은 서버가 기억합니다</p>
</form>
```

1. **래퍼 `<div class="composer">`** 추가(textarea + 버튼만 감쌈). 카드 배경·테두리·포커스 링을 textarea+버튼에만 두고 도움말은 카드 밖 아래에 두기 위해 필요. `form#form` 자체를 카드로 쓰면 `#help`가 카드 안에 들어가 "카드 아래 작은 안내" 배치가 안 된다.
2. **`placeholder` 속성** 추가. 시각 힌트일 뿐 이름은 `<label for="msg">`가 계속 제공(placeholder를 레이블 대신 쓰지 않음).
3. **`#help` 문구 단축**. D14 §5가 요구한 새로고침 안내 취지(화면 기록은 사라지나 맥락은 서버가 기억)는 유지. `id="help"`·`aria-describedby="help"` 유지.

- `rows="2"`·`maxlength="2000"`·`required`·`type="submit"`·버튼 텍스트 "전송"은 그대로.
- **테스트 영향(grep 확인)**:
  - `tests/test_d14.py::test_html_static_rules`: 인라인 script·`on*=`·`style=`·`javascript:`·`http(s)://`·`lang`/`viewport`·`<script src>` 목록·`href="reports.html"`·`role="alert"`·`<label for>`↔`id` 대응·**속성 따옴표 누락** 검사. 추가 속성은 모두 큰따옴표, URL·인라인 스타일 없음 → 통과. `<label for="msg">` ↔ `id="msg"` 유지.
  - `tests/test_d14.py::test_static_served_with_csp`, `tests/test_d16_e2e.py`: `/`·`/style.css` 200·헤더만 확인, 본문 텍스트·`#help` 문구 검사 없음(grep `help`/`Shift`/`placeholder` 0건).
  - `tests/frontend/*.test.js`: 가짜 DOM(`getElementById`가 임의 div 반환)·순수 로직. index.html·style.css를 읽지 않음 → 영향 없음.
  - D14 §3 골격(`form > label.sr-only + textarea#msg + button[type=submit]`)은 래퍼 한 겹만 늘고 순서·요소·속성 유지, D14 §11 정적 규칙 전부 유지.

## 4. 결정 사항

- **전송 버튼: 텍스트 "전송" 유지, 아이콘 안 씀.** 인라인 SVG는 CSP상 가능하나 `aria-label`·`aria-hidden` 관리가 늘고, SVG에 `xmlns="http://www.w3.org/2000/svg"`를 쓰면 `test_html_static_rules`의 `https?://` 금지에 걸린다(HTML 안 인라인 SVG는 xmlns 생략 가능하지만 함정이 생김). 텍스트가 가장 단순하고 접근성 이름도 그대로.
- **자동 높이: CSS만.** `#msg { field-sizing: content; min-height: 1행; max-height: 상한; overflow-y: auto }`. 지원 브라우저(Chromium 123+, Safari 최근 버전)는 내용 따라 커지고 상한 이후 내부 스크롤. 미지원(Firefox 등)은 `rows="2"` 고정 높이 + 내부 스크롤로 **기능 손실 없는 폴백**. JS 자동 높이 코드는 추가하지 않는다(폴백이 충분히 사용 가능).
- **포커스: 카드 전체 링.** textarea 자체 outline은 끄고(`#form textarea:focus-visible { outline: none }`) 대신 `.composer:focus-within`에 accent 링을 준다. textarea는 포커스 시 항상 `:focus-visible`이므로 표시가 사라지는 경우가 없다 — D14a §8 "outline:none 금지"의 **명시적 예외**(동등 이상의 대체 표시가 있음). 전송 버튼은 기존 `:focus-visible` 윤곽을 그대로 가진다(카드 링 + 버튼 윤곽 동시 표시 허용).

## 5. CSS 규칙 (`frontend/style.css`에서 `form#form`·`#form textarea`·`#help` 블록 교체)

```
form#form {                                   /* 투명 하단 고정 영역(카드를 띄우는 받침) */
  position: sticky; bottom: 0; z-index: 1;
  width: 100%; max-width: 820px; margin: 0 auto;
  padding: 12px 16px 8px;
  background: linear-gradient(to bottom, transparent, var(--bg) 35%);  /* 뒤로 지나가는 말풍선이 카드 아래에서 비치지 않게 */
  display: flex; flex-direction: column; gap: 6px;
}
.composer {                                   /* 카드 */
  display: flex; align-items: flex-end; gap: 8px;
  background: var(--surface); border: 1px solid var(--line); border-radius: 22px;
  padding: 6px 6px 6px 14px;
  box-shadow: 0 4px 16px rgba(31, 35, 40, .10), var(--shadow);   /* 다크는 --shadow:none이라 테두리만 남음 → §구현 시 확인 */
}
.composer:focus-within { border-color: var(--accent); box-shadow: 0 0 0 2px var(--accent); }
#form textarea {
  flex: 1; min-width: 0;
  border: 0; background: transparent; padding: 8px 0; border-radius: 0;
  resize: none;
  font-size: 16px;                            /* iOS Safari 포커스 시 확대 방지 */
  line-height: 1.5;
  field-sizing: content;
  min-height: calc(1.5em + 16px);             /* 1행 */
  max-height: min(40vh, 12rem);               /* 이후 내부 스크롤 */
  overflow-y: auto;
}
#form textarea:focus-visible { outline: none; }    /* 카드 :focus-within 링이 대체 */
#send {                                       /* 기존 주 버튼 색 유지, 크기만 소형·알약형 */
  flex: none; min-height: 36px; padding: 6px 16px; border-radius: 999px;
}
#form[aria-busy="true"] .composer { opacity: .7; }
#form textarea:disabled { cursor: not-allowed; }
#help { margin: 0; padding: 0 14px; font-size: .75rem; color: var(--muted); text-align: center; }
```

- 기존 `form#form > * { min-width: 0; }`·`#form textarea { flex:1; resize: vertical; }`·`#help { flex-basis:100% … }`·`form#form { … border-top … }` 블록은 위로 **대체**(삭제).
- 그림자 rgba 1개는 D14a §10 "rgba 그림자 예외"에 해당. 토큰화를 원하면 `:root`에 `--shadow-lg`로 올려도 됨(구현 재량).
- 색은 모두 기존 토큰(`--surface`·`--line`·`--accent`·`--bg`·`--muted`). 라이트/다크 자동 추종, 새 색 리터럴 없음.
- `#send` 색·hover·`:disabled`(opacity .55)·`:focus-visible` 윤곽은 기존 공통 규칙(D14a §8)을 그대로 상속.
- `@media (max-width: 600px)`: 기존 `form#form { padding-left/right: 12px }` 유지(선택자 목록에 그대로 있음). 360px에서 카드 폭 = 336px, 버튼 ≈60px → textarea 약 250px로 충분. 추가 규칙 불필요.
- `footer#disclaimer`는 form 아래 흐름에 그대로(변경 없음).

## 6. 검증 방법

- **자동(기존 테스트 그대로 통과, 추가 테스트 없음)**: `pytest tests/test_d14.py tests/test_d15.py tests/test_d16_e2e.py` 및 `node --test tests/frontend/`. 전체 `pytest` 회귀 통과.
- **정적 점검**: `git diff --stat`에 `frontend/style.css`·`frontend/index.html`만; index.html diff가 §3의 세 변경(래퍼 div, placeholder, #help 문구)뿐; `style.css`에 `http`·`@import`·`url(` 없음; `chat.js` 무변경.
- **수동 브라우저 체크리스트**(Chrome 필수 + Firefox 또는 Safari 1개, 라이트·다크 각각):
  1. 입력창이 채팅 열 폭의 둥근 카드로 하단에 떠 보이고 그림자(라이트)/테두리(다크)로 구분됨. 리사이즈 손잡이 없음. placeholder 보임(muted 색).
  2. 여러 줄 입력(Shift+Enter) 시 카드가 위로 커지고 상한 이후 내부 스크롤(Chrome). Firefox는 2행 고정 + 내부 스크롤.
  3. Enter 전송, Shift+Enter 줄바꿈, 한글 IME 조합 중 Enter 중복 전송 없음(D14 §9 불변). 빈 입력 전송 안 됨.
  4. 전송 후 입력이 비워지고 카드 높이가 1행으로 돌아옴, 포커스 복귀.
  5. 전송 중: 카드 흐려짐, textarea·전송·새 대화 비활성, "분석 중…" 상태 줄. 응답 후 원상복귀.
  6. 포커스: 클릭/Tab으로 textarea 진입 시 카드 전체 accent 링, Tab으로 전송 버튼 이동 시 버튼 윤곽 보임.
  7. 스크롤해도 카드가 하단 고정, 카드 아래로 말풍선이 비치지 않음(그라데이션 받침), 마지막 말풍선이 카드에 가려 못 읽는 일 없음(스크롤 끝까지 내리면 보임).
  8. 도움말이 카드 아래 작은 글씨로 1~2줄, 스크린리더가 textarea 설명으로 읽음(aria-describedby).
  9. 폭 360px: 카드·버튼 한 줄, 페이지 가로 스크롤 없음, iOS Safari(가능하면) 포커스 시 확대 없음.
  10. DevTools Console CSP 위반 0건.

## 구현 시 확인 항목 (FAIL 아님)

- `field-sizing: content`와 `rows="2"` 상호작용: 지원 브라우저에서 빈 상태 높이가 `min-height`(1행)로 가는지, 아니면 2행으로 잡히는지. 1행이 안 되면 `min-height`만 조정(rows는 폴백용으로 유지).
- 다크 모드에서 카드가 배경과 충분히 구분되는지(`--surface` #161b22 vs `--bg` #0d1117 + `--line` 테두리). 약하면 다크에서만 그림자 대신 테두리 색을 한 단계 밝게.
- `linear-gradient` 받침이 말풍선 마지막 줄을 과하게 가리는지 — 가리면 `main`에 `padding-bottom` 추가 또는 그라데이션 시작점 조정.
- iOS Safari 가상 키보드 위 sticky 동작(D14a §11과 동일 항목).
- `.composer:focus-within` 링과 `#send:focus-visible` 윤곽(outline-offset 2px)이 겹쳐 보기 나쁜지 — 나쁘면 `#send:focus-visible { outline-offset: 1px }` 정도만 조정.
- 반경 22px·`max-height: min(40vh, 12rem)`·opacity .7·폰트 16px·그림자 값은 임의 값.
