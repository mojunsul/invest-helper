# D14 채팅 화면 (`frontend/index.html`, `frontend/chat.js`, `frontend/common.js`, `frontend/style.css`)

기준: DESIGN §1·§9 화면 정의, D13 §4·§5·§8·§9(응답 필드·CSP·XSS 책임), D12 §4·§6·§9(저장), D9(answer 구성·status), D10(세션), D0 §3(stage/replan 값 집합). 의사코드/명세이며 구현 아님. 상위 계약(DESIGN·D0~D13)은 수정하지 않고 §10에 요청만 올린다. **원칙: 바닐라 JS, 빌드·프레임워크 없음, 구현이 가장 단순한 쪽.**

## 1. 범위

| 소관 | 비소관 |
|---|---|
| 대화 입력·전송·로딩·오류 표시, `answer` 렌더, 타임라인 패널(steps/tool_calls/replans), 저장 버튼, 면책·키 누락 안내, reports.html 링크 | 응답 생성(D9)·API(D12/D13), 리포트 목록·수정·삭제(D15), 로그 열람 화면 |

- **로그 열람 UI는 제공하지 않는다**(D13 §9가 D14 결정으로 위임; DESIGN §9 화면 정의에 없음, 타임라인은 응답의 steps/tool_calls로 충족). `/api/logs*`는 사용하지 않는다.
- SSE 실시간 진행 표시 없음(D13 §10 기본값). 전송 중에는 "분석 중…" 표시만.

## 2. 파일 구성 (CSP `script-src 'self'` 준수)

| 파일 | 내용 | 비고 |
|---|---|---|
| `frontend/index.html` | 정적 골격(§3). `<script src="common.js">` → `<script src="chat.js">` 두 줄만. **인라인 `<script>`·`on*=`·인라인 이벤트 금지** | `style=` 속성도 쓰지 않는다(CSP는 style `'unsafe-inline'` 허용이나 스타일은 CSS 파일로 통일) |
| `frontend/common.js` | **D15와 공유**하는 유틸(§4). 전역 객체 `Common` 1개(`window.Common`)로 노출, 모듈 시스템 없음 | 파일명·export 형태 제안 — **D15 설계에서 확정**(D15가 같은 파일을 쓰되 필요한 함수가 다르면 D15가 추가 제안) |
| `frontend/chat.js` | D14 전용 로직: 상태·DOM 조립·이벤트 바인딩. `DOMContentLoaded` 후 초기화 | |
| `frontend/style.css` | D14·D15 공용 스타일(제안, D15에서 확정) | |

- 모든 경로는 상대(`common.js`, `/api/...`). 외부 CDN·폰트·이미지 없음(CSP `default-src 'self'`).
- **순수 로직 분리(테스트 용이)**: DOM을 만지지 않는 함수는 `common.js`(공용) 또는 `chat.js` 상단 "순수 영역"에 두고, 파일 끝에서 `if (typeof module !== "undefined") module.exports = {...}`로 node에서 `require` 가능하게 한다(브라우저에선 `module` 미정의라 무해, 번들러 불필요). DOM 접근은 `document`가 있을 때만 실행되도록 초기화 함수(`init`)로 격리하고 `typeof document !== "undefined"`일 때만 호출.

## 3. 화면 구조 (`index.html` 정적 골격)

```
<html lang="ko"> <head> meta charset/viewport, <title>, link style.css
<body>
  <header>  제목 · 링크 "저장된 리포트" → reports.html · 버튼 "새 대화"
  <div id="banner" role="alert" hidden>        # 키 누락 안내 등 (§7)
  <main>
    <ol id="log" role="log" aria-live="polite" aria-label="대화">   # 메시지 목록
    <div id="status" aria-live="polite">                           # 로딩/오류 한 줄 (§6)
  <form id="form">
    <label for="msg" class="sr-only">메시지</label>
    <textarea id="msg" maxlength=2000 rows=2 required>  <button type="submit">전송
  <footer id="disclaimer">                      # 면책 상시 표시 (§8)
```

- **면책 문구 상시 표시**: `footer`에 항상 보임. 초기 텍스트는 정적 HTML에 하드코딩(마지막 응답 전에도 보이게: "본 서비스는 투자 권유가 아니며 전문가 조언을 대신하지 않습니다"에 해당하는 DESIGN §3 문구) → 응답 수신 시 `res.disclaimer`로 **textContent 교체**(문구 단일 출처는 서버, 정적 값은 폴백). `answer` 끝에도 서버가 이미 면책을 붙이므로(D9) 말풍선 내 면책은 그대로 표시.
- 사용자 메시지·봇 응답은 `<li>` 말풍선. 봇 `<li>`는 본문(`<div class="answer">`), 상태 뱃지, 저장 영역, 타임라인 `<details>`를 자식으로 가진다.

## 4. 공용 유틸 `common.js` (D15 공유 제안)

| 함수 | 역할 | 비고 |
|---|---|---|
| `Common.el(tag, {class, text, attrs}, children…)` | 요소 생성 헬퍼. `text`는 **항상 `textContent`**. `innerHTML`·`insertAdjacentHTML`·`outerHTML` 대입 없음(전 파일 금지, §11 정적 점검) | |
| `Common.safeUrl(s)` | `new URL(s)` 파싱 후 `protocol`이 `http:`/`https:`일 때만 정규화 URL 반환, 아니면 `null` | 순수 함수, node 테스트 대상 |
| `Common.linkify(text)` → 노드 배열 | 문자열에서 `https?://\S+` 토큰을 찾아 `safeUrl` 통과분만 `<a href target="_blank" rel="noopener noreferrer">`(텍스트=원문 URL)로, 나머지는 텍스트 노드로 | 정규식 분할 후 노드 생성 — HTML 문자열 조립 없음. 순수(노드 생성부 제외 분할 로직은 `splitUrls(text)`로 분리해 테스트) |
| `Common.api(method, path, body?)` → `{ok, status, data}` | `fetch` 래퍼. `Content-Type: application/json`. 2xx면 `data`=파싱 JSON(204는 null). 비2xx면 `data`=`{code,message}`(본문이 JSON·두 키가 문자열일 때만 신뢰, 아니면 `{code:"HTTP_ERROR", message:"요청에 실패했습니다."}`). 네트워크 예외·JSON 파싱 실패는 `{ok:false,status:0,data:{code:"NETWORK_ERROR", message:"서버에 연결하지 못했습니다."}}` | 호출자가 예외를 신경 쓰지 않게 **throw하지 않는다**. 서버 `message`는 서버가 입력 반사 없이 만든 고정 문구이나 렌더 시 여전히 textContent |
| `Common.storageGet(k)` / `storageSet(k,v)` | `localStorage` 접근을 try/catch로 감싼 것(사생활 모드·차단 시 `null`/무시) | |
| `Common.fmtTime(iso)` | 표시용 시각(`HH:MM:SS`). 파싱 실패 시 원문 | |
| `Common.REASON_TEXT`, `STATUS_TEXT` 등 표시 매핑 | 코드→한글 문구(§9). D15의 `ReasonCode` 표시와 공유 가능 | 매핑에 없는 코드는 **코드 원문 표시**(서버 값 추가에 안 깨짐) |

- 전역 오염은 `Common` 하나. D15는 `Common.api`·`el`·`safeUrl`·`linkify`·`storage*`를 그대로 쓴다.

## 5. 세션 관리

```
SESSION_KEY = "invest_helper_session_id"
getSessionId():
  sid = Common.storageGet(KEY)
  if sid 없음 or UUID 정규식(D13 §4와 동일) 불일치:
     sid = crypto.randomUUID()          # 미지원/예외 시 폴백: 난수 16바이트로 v4 UUID 조립 (crypto.getRandomValues)
     Common.storageSet(KEY, sid)        # 실패해도 계속 진행 — 메모리 변수에 보관해 이 페이지 수명 동안은 유지
  return sid
```

- 모듈 변수 `sid`에 캐시. `localStorage` 실패(비공개 모드 등) 시 새로고침마다 새 세션이 되는 것을 수용(오류 아님).
- **새 대화**: 확인 없이 새 UUID 생성·저장, 말풍선 목록 비움, 상태/배너 초기화. 이전 세션의 서버 로그·메모리는 건드리지 않는다(삭제 API 호출 안 함). 전송 중에는 비활성.
- **새로고침 시 화면 복원은 하지 않는다**(서버 메모리는 이어지나 말풍선 이력은 비워짐). 서버에 대화 조회 API가 없고 §10-요청 없이 로컬 저장은 크기·민감도 부담. 안내 문구 1줄("새로고침하면 화면 대화 내용은 사라지지만 맥락은 서버가 기억합니다")을 입력창 도움말로 표시.

## 6. 전송·상태 처리

```
submit(form):                               # addEventListener('submit'), preventDefault
  text = textarea.value.trim()
  if not text or busy: return               # 빈 입력/중복 전송 무시 (클라 검증; 서버 422도 처리)
  busy = true; 입력·전송·새대화 버튼 disabled; status = "분석 중입니다… (최대 수십 초 걸릴 수 있습니다)"
  말풍선(user) 추가(textContent)
  r = await Common.api("POST", "/api/chat", {session_id: sid, message: text})
  busy = false; 컨트롤 복구; status 비움
  if r.ok: renderBot(r.data) ; textarea 비우기 ; 포커스 복귀
  else:    renderError(r)                   # textarea 내용 유지(재시도 가능)
```

- **Enter 전송 / Shift+Enter 줄바꿈**: `keydown`에서 처리(IME 조합 중 `isComposing`이면 무시 — 한글 입력 필수 확인).
- **오류 표시**(시스템 말풍선 `class="error"`, `role` 불필요 — `#status`의 `aria-live`로 전달):

| 상황 | 표시 |
|---|---|
| 409 `SESSION_BUSY` | "이전 요청을 처리 중입니다. 잠시 후 다시 시도하세요."(서버 message 대신 고정 문구도 무방). 사용자 말풍선은 **추가하지 않거나 제거**, 입력 유지 |
| 422 `INVALID_REQUEST` | 서버 `message` 표시(필드명 수준). 사용자 말풍선 제거, 입력 유지 |
| 5xx / 기타 4xx | 서버 `message`(없으면 고정 폴백) |
| 네트워크 오류(`status 0`) | "서버에 연결하지 못했습니다." |
| 200 + `status=="error"` | **오류가 아닌 정상 응답처럼** `answer`를 `error` 스타일 말풍선으로 표시(D13 §9). 타임라인·면책 갱신은 동일 |

- 타임아웃: 클라이언트 임의 타임아웃을 두지 않는다(LLM 장시간 호출, D13에 SSE 없음). 브라우저/서버 기본에 맡김 — 구현 시 확인 §12.

## 7. 응답 렌더 `renderBot(res)`

외부 유래 문자열은 **전부 textContent/텍스트 노드**. 응답 필드가 없거나 타입이 틀려도 죽지 않게 `Array.isArray(x) ? x : []`·`typeof === "string"` 가드(방어적 읽기; 서버 계약은 항상 존재).

1. **answer**: `<div class="answer">`에 `white-space: pre-wrap`(CSS). 내용은 `Common.linkify(answer)` 노드들로 채움(공시 URL 클릭 가능; `http(s)`만, `rel="noopener noreferrer"`, `target="_blank"`). 마크다운/HTML 해석 없음. 표 정렬 유지를 위해 `.answer`는 **고정폭 폰트**(`font-family: ui-monospace, "D2Coding", monospace`) 사용.
2. **status 뱃지**: `answered`=표시 없음, `asked`="추가 정보 필요"(되묻기), `limit`="조건 완화 제안", `error`="처리 실패". 말풍선에 `data-status` 대신 **CSS 클래스**(`status-asked` 등)로 구분 + 뱃지 텍스트(색만으로 구분하지 않음).
3. **missing_api_key**: `res.missing_api_key === true`이면 `#banner`에 "API 키가 설정되지 않았습니다. 서버의 `.env`를 확인하세요."(고정 문구; 어느 키인지 모름·값 노출 없음) 표시(hidden 해제). 이후 응답에서 false이면 숨김(응답마다 갱신). 닫기 버튼 없음(단순).
4. **disclaimer**: `res.disclaimer`(비어 있지 않은 string)로 `#disclaimer` textContent 갱신.
5. **saveable**: §8.
6. **타임라인**: §9.
7. 렌더 후 `#log` 하단으로 스크롤(`scrollIntoView` 또는 `scrollTop`). 말풍선 수 상한 없음(새 대화로 비움).

## 8. 저장 UI (`saveable[]`)

- 봇 말풍선 하단에 `saveable` 각 항목마다 한 줄: `[저장] {stock_name} ({stock_code})` 버튼. 버튼을 누르면 해당 줄 아래에 **인라인 미니 폼**(이름 `<input maxlength=100>` placeholder = `{stock_name} 리포트`(D12 기본값), 메모 `<textarea maxlength=1000>`, `[확정]`·`[취소]`)을 펼친다. `<label>` 연결 필수. `prompt()`/`confirm()` 사용 안 함.
- 확정 시:
```
name = input.trim()  (빈 값이면 필드 자체를 body에서 생략 → 서버 기본값. 공백만 입력은 빈 값으로 간주)
memo = textarea.value (빈 문자열이면 생략)
body = {session_id: sid, stock_code, [name], [memo]}      # 그 외 필드(지표·기준일 등) 절대 보내지 않음 (extra=forbid, D12 §4)
r = await Common.api("POST", "/api/reports", body)         # 처리 중 해당 폼 버튼 disabled (중복 저장 방지)
```
- 결과 표시(폼 아래 한 줄 메시지, `aria-live`):

| 결과 | 표시 |
|---|---|
| 201, `duplicate_of=[]` | "저장했습니다. (리포트 #{id})" + 링크 "저장된 리포트 보기"→`reports.html`. 폼 닫고 버튼은 "저장됨"으로 비활성 |
| 201, `duplicate_of` 비어있지 않음 | 위 + "같은 종목·기준일로 이미 저장된 리포트가 {n}건 있습니다(#id…). 중복은 reports.html에서 삭제할 수 있습니다." — **이미 저장은 완료됨**(D12 §7 "저장 진행"), 확인 대화 없음 |
| 404 `DRAFT_NOT_FOUND` | "저장할 결과가 없습니다. 먼저 해당 종목을 조회하세요."(서버 문구 사용 가능) — 서버 재시작·LRU 밀림 시 발생 |
| 422 `INVALID_REQUEST` | 서버 message(필드명) 표시, 폼 유지 |
| 500 `INTERNAL_ERROR`·네트워크 | 고정/서버 message, 폼 유지(재시도) |

- 같은 종목 저장 버튼은 "저장됨" 후에도 **재저장 허용하지 않음**(UI만; 서버는 중복 허용). 같은 화면에서 다시 저장하려면 새 응답의 saveable로.
- `saveable`이 **응답마다 현재 세션의 전체 draft**이므로 이전 말풍선의 저장 버튼도 서버 draft가 남아 있는 동안 유효하다. 최신 draft는 후속 턴에서 갱신될 수 있음(D12 §2: 종목별 덮어쓰기)을 허용 — 이전 말풍선에서 저장하면 **최신 draft가 저장됨**. 혼동 방지를 위해 **가장 최근 봇 말풍선의 저장 버튼만 활성**, 이전 말풍선의 저장 영역은 새 응답 수신 시 제거(구현 단순).

## 9. 타임라인 패널 (접이식)

각 봇 말풍선에 `<details><summary>처리 과정 (단계 {n} · 도구 호출 {m} · 재계획 {r})</summary>…</details>`(기본 접힘, 네이티브 `details`로 키보드·스크린리더 지원, JS 불필요). 세 배열이 모두 비면 패널 생략.

- 본문은 `<ol>` 하나. `steps[]`와 `tool_calls[]`를 **`seq` 오름차순 병합**한 단일 시간순 목록(둘 다 같은 세션 이벤트의 seq를 공유 — D13 §5-3 `seq`). 항목 종류:
  - step 행: `{stage 한글} · {started_at~ended_at 시각}` + `ended_at==null`이면 "(중단)". `stage==="analyze"`면 `intent`·`reason` 추가 표시.
  - tool_call 행: `call`: `▶ {tool} 호출 {input 요약} {cache_hit ? "(캐시)" : ""}`, `result`: `◀ {tool} {ok ? "성공" : "실패 " + error_code} · {duration_ms}ms · {summary}`. `input`은 객체이므로 `JSON.stringify`한 문자열을 **textContent**로(길면 CSS로 줄바꿈/말줄임; 값 가공 없음).
- **재계획 구분선**: 병합 목록을 순회하며 항목의 `round`가 이전 항목보다 커질 때 그 앞에 구분 행 삽입: `── 재계획 {round}: {TRIGGER_TEXT[trigger]} → {CHANGE_TEXT[change]} ──`(`replans[round-1]` 사용, 없으면 "재계획 {round}"만). `round` 점프(2→4)가 나와도 건너뛴 구간에 대해 `replans[...]`를 순서대로 모두 출력하지 않고 **해당 `round-1` 하나만**(단순). `replans` 배열이 비어 있으면 구분선 없음.
- 병합 시 동일 seq 충돌은 없다(이벤트 seq는 세션 단위 유일). 정렬은 안정 정렬(숫자 `seq`), seq가 숫자가 아닌 항목은 뒤로.
- 표시 매핑(코드→한글, 미정의 값은 원문): `STAGE`={analyze:"의도 분석", plan:"계획", execute:"실행", judge:"판정", respond:"응답 작성"}, `TRIGGER`={no_pass:"조건 충족 종목 없음", no_candidates:"후보 종목 없음"}, `CHANGE`={market_expand:"시장 확대", similar_industry:"유사 업종 확대", limit_up:"후보 수 상한 확대"}. `ReasonCode` 문구는 `answer`에 이미 서버가 렌더하므로 D14는 **verdicts를 별도 표로 그리지 않는다**(answer가 단일 출처, D13 §5-1). `verdicts`·`industry_avg`는 소비하지 않음.

## 10. 상위 계약 요청 (직접 수정 안 함, 승인 대기)

없음. D13 §5-1·§9, D12 §6·§9 계약 그대로 사용한다. 참고(요청 아님):
- D12 `duplicate_of`·D13 `saveable`이 **세션 draft 전체**를 주는 점을 §8 UI가 이미 반영(최신 응답 말풍선만 저장 활성).
- reports.html 링크는 확장자 포함 고정(D13 §12 "html=True에서 `/reports`는 404").
- `common.js`·`style.css`는 D15가 재사용하는 **D14↔D15 합의 사항**이므로 D15 설계가 이름·함수를 바꾸려면 D14 문서를 같이 개정(상위 계약은 아님).

## 11. 테스트 관점 (브라우저 없이 가능한 범위)

- **node 순수 로직 테스트**(의존성 없음, 내장 `node:test`/`assert`; `common.js`·`chat.js`의 `module.exports` 사용): `safeUrl`(`http`/`https` 통과, `javascript:`·`data:`·`vbscript:`·`file:`·상대경로·빈 문자열·대소문자 변형 `JaVaScRiPt:`·공백/개행 삽입 → null), `splitUrls`(문장 끝 구두점 `.`·`,`·`)` 처리, URL 없는 문자열, 한글 인접, 여러 URL), `getSessionId` 로직(저장값 유효/무효/없음/storage 예외 → 새 UUID·형식 통과), UUID 폴백 생성기 형식(v4), `Common.api`의 응답 해석(가짜 `fetch` 주입: 200 JSON, 201, 204, 409/422/404 `{code,message}`, 비JSON 5xx, `fetch` reject, 두 키가 없는 JSON 오류 → 폴백), 타임라인 병합(`seq` 정렬, `round` 증가 시 구분선 위치, `replans` 비었을 때, `ended_at=null`, 모르는 stage/trigger/change 코드 원문 표시), 저장 body 구성(빈 name/memo 생략, 여분 필드 없음), 배열 방어 읽기(필드 누락 응답).
- **정적 점검**(node/grep 스크립트 또는 수동): `frontend/*.html`에 인라인 `<script>`(src 없는 script)·`on[a-z]+=`·`style=`·`javascript:` 부재; `frontend/*.js`에 `innerHTML`·`outerHTML`·`insertAdjacentHTML`·`document.write`·`eval`·`new Function` 부재(주석 제외); `index.html`의 `<script src>`가 같은 오리진 상대경로뿐; 외부 URL(http 리터럴) 참조 부재; `<label for>`/`lang`/`viewport` 존재.
- **서버 연동 스모크**(D13 TestClient 또는 uvicorn 기동 후 `curl`): `/`·`/chat.js`·`/common.js`·`/style.css` 200, 응답 헤더 CSP 존재.
- **수동 확인 시나리오**(브라우저, 시연 전 1회): (1) 첫 로드 → UUID 생성·새로고침 후 동일(개발자도구 localStorage). (2) 대표 요청 5개 순차(DESIGN §10): 1·3 표+공시 링크 렌더, 2 재계획 구분선·limit 뱃지, 4 chitchat(타임라인 단계 적음), 5 asked 뱃지·도구 호출 0. (3) 저장: 이름 생략/입력, 같은 종목 재저장 → 중복 안내, 서버 재시작 후 이전 말풍선 저장 → `DRAFT_NOT_FOUND` 문구. (4) 연타·전송 중 재전송 불가, 두 탭에서 같은 세션 동시 → 409 문구. (5) `.env` 키 제거 상태 → 배너+`status=error` 말풍선. (6) 서버 종료 상태 전송 → 네트워크 오류 문구, 입력 보존. (7) XSS: 사용자 메시지·모의 서버 응답(`<img onerror>`, `javascript:` URL, `<script>`)이 텍스트로만 보이고 실행되지 않음, 개발자도구 Console에 CSP 위반 0건. (8) 한글 IME 입력 후 Enter 중복 전송 없음. (9) 키보드만으로 입력→전송→details 펼침→저장 가능, 포커스 표시.

## 12. 접근성 최소 요건

`lang="ko"`, 폼 컨트롤마다 `<label>`(시각 숨김은 `.sr-only`), 메시지 영역 `role="log"`+`aria-live="polite"`, 오류/배너 `role="alert"`, 버튼은 `<button>`(div 클릭 금지), 모든 상호작용 요소 키보드 도달·`:focus-visible` 윤곽 유지, 상태를 색만으로 전달하지 않음(뱃지 텍스트), 본문 대비 4.5:1 이상 색 선택, 반응형(`viewport` meta, 좁은 폭에서 가로 스크롤 없이 `pre-wrap`·`overflow-wrap:anywhere`), 전송 중 `aria-busy`.

## 13. 구현 시 확인 항목 (FAIL 아님)

- `crypto.randomUUID`는 secure context(localhost 포함)만 지원 — `127.0.0.1` 접속은 secure context로 취급되나 LAN IP 접속 시 미지원이면 §5 폴백 사용을 실호출 확인.
- 장시간 요청: 브라우저/프록시 기본 타임아웃 내에 `/api/chat`이 끝나는지(재계획 3회 시 실측), 필요 시 로딩 문구에 경과 시간 표시.
- `answer`의 표 정렬(한글 전각폭)이 고정폭 폰트에서 어긋나는 정도 — 폰트 후보·`white-space: pre` vs `pre-wrap` 실사용 확인.
- URL 정규식이 한글 조사·괄호와 붙은 URL 끝을 어떻게 자르는지(공시 링크 형식 D5 실제 값으로 확인), 말줄임 길이 상수.
- 이전 말풍선 저장 영역 제거 정책(§8)이 시연에서 불편하면 완화(구현 판단).
- `details` 안 `input` JSON이 매우 길 때 표시 길이 상한(임의 상수) — 현재 상한 없음, 스크롤/말줄임 CSS.
- 입력창 `maxlength=2000`과 서버 `strip` 후 길이 차이(공백) 경계.
- `style.css` 색상·간격은 임의 값(디자인 요구 없음).
- `module.exports` 가드가 브라우저에서 `ReferenceError`를 내지 않는지(`typeof module` 사용) 확인.
