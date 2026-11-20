# D14d 분석 카드 탭 단위 복원 (`frontend/chat.js`, `frontend/index.html`)

기준: D14 §5(세션)·§8(저장)·§12(접근성), D14c §1·§3·§7, D12b(저장 패널 `review_date`). **사용자 승인 방향**: 리포트 화면에 갔다 오거나 새로고침해도 **같은 탭**에서는 최근 분석 카드를 다시 보여준다. 서버·API·D0 변경 없음. 의사코드이며 구현 아님.

## 1. 결정 요약

| 항목 | 결정 |
|---|---|
| 저장소 | `sessionStorage` (탭 닫으면 소멸, 다른 탭과 공유 안 함 → 민감도 부담 최소. 서버 세션 id는 기존대로 `localStorage`) |
| 키 | 단일 키 `HISTORY_KEY = "invest_helper_cards"`, 값 `JSON {sid, items:[{q, res, at}]}` — `sid`가 현재 세션과 다르면 무시(다른 세션 기록 혼입 방지). 키 1개라 세션별 잔재가 쌓이지 않음 |
| 레코드 | `q` = 질문 원문(trim 후 전송한 `text`), `res` = `/api/chat` 성공 응답 객체(`r.data` 그대로), `at` = 응답 수신 시각 ISO |
| 대상 | `r.ok`인 응답만(본문 `status`가 `error`/`asked`/`limit`이어도 서버 턴이므로 저장). **처리 중 임시 카드·409/422·네트워크 오류 카드는 저장 안 함** |
| 상한 | 최근 `HISTORY_MAX = 10`건, 직렬화 길이 `HISTORY_MAX_LEN = 1_000_000`자 초과 시 오래된 것부터 버림. 최신 1건만으로도 초과하면 빈 목록(→ 키 삭제) |
| 실패 | 모든 `sessionStorage` 접근은 try/catch. 비공개 모드·용량 초과·파싱 실패 시 조용히 포기, 화면 동작 영향 없음. `JSON.parse` 실패·형식 불일치 → 기록 폐기(키 삭제 시도) |
| 새 분석 | `#new-chat` 클릭 시 키 삭제 |
| 이미 저장됨 표시 | 복원하지 않음("저장" 버튼으로 다시 보임). 재저장 시 기존 `duplicate_of` 안내로 충분 |

## 2. 순수 함수 (`chat.js` 순수 영역, export)

```
HISTORY_KEY = "invest_helper_cards"; HISTORY_MAX = 10; HISTORY_MAX_LEN = 1000000

// raw: sessionStorage 문자열(null 가능). 같은 sid의 유효 레코드 배열(오래된 순), 아니면 []
parseHistory(raw, sid):
  try v = JSON.parse(raw) catch → []
  if !isObj(v) or v.sid !== sid or !Array.isArray(v.items) → []
  return v.items.filter(x => isObj(x) && typeof x.q === "string" && isObj(x.res))

// 새 레코드를 끝에 붙이고 상한 적용. 반환 {items, raw}. raw=null이면 저장하지 말고 키 삭제
pushHistory(items, rec, sid, max = HISTORY_MAX, maxLen = HISTORY_MAX_LEN):
  out = items.concat([rec]).slice(-max)
  while out.length:
    raw = JSON.stringify({sid, items: out})
    if raw.length <= maxLen → return {items: out, raw}
    out = out.slice(1)
  return {items: [], raw: null}
```

## 3. DOM 영역 변경 (`init`)

- 접근 헬퍼(DOM 영역 내부, 3줄): `ssGet/ssSet/ssDel` — 각각 `try { sessionStorage.… } catch (e) { 무시/null }`. `common.js`의 `storageGet/Set`(localStorage 고정)은 건드리지 않는다.
- 모듈 변수 `hist = parseHistory(ssGet(HISTORY_KEY), sid)`.
- `renderCard(res, question, at)`·`cardHead(li, question, res, at)`: `at` 인자 추가, 시각 표시는 `C.fmtTime(at || new Date().toISOString())`. 기존 호출부는 인자 생략으로 동작 불변.
- `disableOldSaves()` 추출: 기존 submit의 "이전 카드 저장 버튼 disabled + 저장 패널 hidden" 두 줄을 함수로 옮기고 submit·복원이 같이 쓴다(D14c 규칙 단일 경로).
- **submit 성공 분기**(`r.ok`) 끝에:
  ```
  var p = pushHistory(hist, {q: text, res: res, at: new Date().toISOString()}, sid)
  hist = p.items; p.raw ? ssSet(HISTORY_KEY, p.raw) : ssDel(HISTORY_KEY)
  ```
  (`at`은 카드 렌더 시 쓴 값과 같은 값을 넘긴다.)
- **복원**(`init` 끝, `renderChips(null); syncEmpty()` 직전):
  ```
  hist.forEach(function (h) {           // 오래된 순
    disableOldSaves()
    var card = renderCard(h.res, h.q, h.at)
    log.insertBefore(card, log.firstChild)   // 최신이 위
  })
  if (hist.length) {
    var last = hist[hist.length - 1].res
    showTimeline(last, log.firstChild)      // 사이드 타임라인 = 최신 카드
    if (isObj(last.conditions)) renderChips(last.conditions) else renderChips(null)
    if (str(last.disclaimer)) disc.textContent = last.disclaimer
  } else renderChips(null)
  syncEmpty()
  ```
  - 결과: 최신 1건 카드만 저장 버튼 활성, 나머지는 disabled — 서버 draft(최신 턴)와 일치, D14c 규칙과 같다. 서버 재시작 등으로 draft가 없으면 기존 `DRAFT_NOT_FOUND` 문구가 그대로 처리.
  - 배너·`#status`는 복원하지 않는다(키 상태가 바뀌었을 수 있음, 다음 응답에서 갱신).
  - 각 카드의 [과정 보기]는 기존대로 해당 카드 `res`로 타임라인 교체.
- **새 분석**: 기존 초기화에 `hist = []; ssDel(HISTORY_KEY)` 추가.
- XSS: 복원도 `renderCard` 경로(모두 `textContent`/`C.el`/`C.linkify`/`C.safeUrl`)만 사용. `innerHTML` 류 금지는 `test_js_static_rules`가 계속 강제.

## 4. `index.html`

`#help` 문구: `Enter 분석 · Shift+Enter 줄바꿈 · 조건 칩을 누르면 변경 문장이 채워집니다 · 새로고침해도 이 탭에서는 최근 분석 10건이 유지됩니다`. 그 외 골격 불변(`test_html_static_rules` 영향 없음).

## 5. 테스트 관점

- 신규 node 테스트(`tests/frontend/d14.test.js`에 추가):
  - `parseHistory`: 정상 왕복, `null`/`""`/깨진 JSON/배열/`items` 비배열 → `[]`, 다른 `sid` → `[]`, `q` 비문자열·`res` 비객체 레코드 제외.
  - `pushHistory`: 11건째 push 시 가장 오래된 것 제거(10건 유지·순서 보존), `maxLen` 작게 줘서 오래된 것부터 버려짐, 단일 레코드 초과 시 `{items: [], raw: null}`, `raw`를 `parseHistory(raw, sid)`로 되읽으면 `items`와 같음.
- 기존 테스트 영향: 없음 예상(순수 함수 시그니처 불변, `renderCard`/`cardHead`는 DOM 영역이라 node 테스트 대상 아님). `test_d14.py` 정적 규칙·CSP 테스트 그대로 통과해야 함.
- 수동 확인(브라우저 1회): (1) 질문 3개 → 리포트 화면 → 뒤로/링크로 복귀 시 3장이 최신 위로 보이고 타임라인·칩이 최신 기준. (2) 최신 카드만 저장 가능, 나머지 disabled, 최신에서 저장 성공. (3) 새로고침 동일. (4) 새 분석 → 빈 화면, 새로고침해도 빈 화면. (5) 새 탭으로 열면 카드 없음(세션 id는 같음). (6) 11번째 질문 후 10장. (7) 개발자도구에서 키 값을 깨뜨린 뒤 새로고침 → 빈 화면·오류 없음. (8) 저장 대상 응답에 `<img onerror>` 문자열이 있어도 복원 시 텍스트로만 표시.

## 6. 상위 문서 수정 (사용자 승인됨, 구현 단계에서 반영 예정)

- **D14 §5 73행** 교체:
  - 현재: `- **새로고침 시 화면 복원은 하지 않는다**(서버 메모리는 이어지나 말풍선 이력은 비워짐). … 입력창 도움말로 표시.`
  - 변경: `- **새로고침·페이지 이동 후 같은 탭에서는 최근 분석 카드 10건을 복원한다**(D14d로 변경, 사용자 승인). sessionStorage에 같은 session_id의 완료 응답만 보관하며 탭을 닫으면 사라진다. 다른 탭·새 분석 후에는 복원하지 않는다(서버 맥락은 session_id로 계속 이어짐).`
- **D14c §1 18행** "범위 밖"에서 `새로고침 후 카드 복원(D14 §1·§5 결정 유지), ` 삭제하고 행 끝에 `(카드 복원은 D14d)` 추가.
- **DESIGN §9 181행** 화면 정의의 `처리 중·키 누락 안내)` 뒤에 `·같은 탭 최근 카드 복원` 삽입 → `…·처리 중·키 누락 안내·같은 탭 최근 카드 복원)`.

## 7. 구현 시 확인 항목 (FAIL 아님)

- 실제 응답 1건 직렬화 크기(대표 요청 1·3 기준)를 확인해 `HISTORY_MAX_LEN`이 10건을 담는지 본다. 부족하면 상수만 조정.
- 복원 카드가 많을 때 첫 렌더 지연 체감 여부(10건 상한이라 문제 없을 것으로 예상).
- 브라우저 뒤로가기 bfcache로 복귀하면 `DOMContentLoaded`가 다시 안 돌아도 DOM이 그대로 남아 있어 결과 동일.
