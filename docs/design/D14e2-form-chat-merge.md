# D14e-2 조건 폼 선택 + 채팅 입력 병합 (`frontend/chat.js`, `frontend/index.html`, `frontend/style.css`)

기준: D14e(조건 폼, §5 `buildQuery`·§7 엣지), D14e-1(전송 시 폼 닫힘), D14d(카드 복원), D14b(컴포저), `backend/agent/nodes/analyze.py`(SYSTEM_PROMPT·followup). 의사코드/명세이며 구현 아님. **백엔드·API·D0·DESIGN 변경 없음. 프런트만 변경.** DESIGN/D0 수정 제안 없음.

## 1. 목적·결정 요약

폼에 선택값을 둔 채 하단 채팅창(`#msg`)에서 전송하면 **선택 내용 + 입력 텍스트를 한 문장(한 `POST /api/chat`)으로 보낸다.** 서버는 `message` 한 줄만 받아 analyze(LLM)가 한 번에 해석하므로 프런트에서 문자열만 합친다.

핵심 규칙 한 줄: **"폼이 열려 있고 선택이 있으면 병합, 닫혀 있으면 텍스트만."** 눈에 보이는 선택만 적용되므로 별도의 초기화 로직이 필요 없다(§4).

## 2. 결합 문장 규칙

순수 함수 추가(`chat.js` 순수 영역, export, 테스트 대상):

```
// industry: select 값, picks: [{id,value}] (D14e 규약), text: 채팅 입력(trim 후)
// 반환 {ok:true, text, applied:boolean} | {ok:false, msg, field?}
mergeQuery(industry, picks, text):
  hasInd = INDUSTRIES.includes(industry)
  if !hasInd and picks.length == 0         → {ok:true, text, applied:false}        // 선택 없음: 텍스트만
  if hasInd and picks.length > 0:
      r = buildQuery(industry, picks)       // D14e §5 그대로 (값 검증 포함)
      if !r.ok → return r                   // 기준값 오류: 전송 안 함(§3)
      out = r.text + " " + text             // "…인 종목 찾아줘 {텍스트}"
  elif hasInd:                              // 산업만
      out = industry + " 중 " + text
  else:                                     // 칩만(산업 없음)
      값 검증(D14e §5의 칩 검증 부분 재사용) 실패 → {ok:false,…}
      out = parts.join(", ") + " 조건. " + text
  if out.length > 2000 → {ok:false, msg:"선택한 조건과 입력을 합치면 너무 깁니다. 입력을 줄여 주세요."}
  return {ok:true, text:out, applied:true}
```

구현 단순화: `buildQuery`에서 칩 문장 조각 생성(`parts`)을 내부 헬퍼 `condParts(picks)`(검증+조각 배열, 실패 시 오류 객체)로 뽑아 `buildQuery`와 `mergeQuery`가 공유한다. `buildQuery`의 외부 동작·테스트 불변.

**순서 = 선택(폼) 먼저, 사용자 텍스트 뒤.** 근거: ① 선택은 구조화된 기본 조건, 텍스트는 보충·수정이므로 충돌 시(예: 폼 ROE 10%, 텍스트 "ROE는 15%로") LLM이 뒤 문장을 우선 반영하기 쉽다. ② `buildQuery` 문장이 "…종목 찾아줘"로 끝나 완결 문장이라 뒤에 공백 하나만 두면 자연스럽다. ③ analyze는 사용자 메시지 전체를 데이터로 보고 industry/constraints/want_disclosures/tools를 추출하며(SYSTEM_PROMPT), 문장 순서에 의존하는 규칙이 없다.

예시(폼 선택 → 입력 → 전송 문장):

| # | 폼 선택 | 채팅 입력 | 전송(=카드 제목) |
|---|---|---|---|
| 1 | 반도체 + 수익성(10) + 저평가 | `공시도 같이 보여줘` | `반도체 중 ROE 10% 이상, PER 산업 평균 이하인 종목 찾아줘 공시도 같이 보여줘` |
| 2 | 자동차 + 부채 적음(100) | `ROE는 15% 이상으로` | `자동차 중 부채비율 100% 이하인 종목 찾아줘 ROE는 15% 이상으로` |
| 3 | 산업만: 게임 | `영업이익률 15% 이상 종목 찾아줘` | `게임 중 영업이익률 15% 이상 종목 찾아줘` |
| 4 | 칩만: 저평가 + 수익성(10), 산업 없음 | `은행 종목만` | `ROE 10% 이상, PER 산업 평균 이하 조건. 은행 종목만` |
| 5 | 없음 | `삼성전자 PER 알려줘` | `삼성전자 PER 알려줘` (현행과 동일) |

산업·칩 문구는 D14e와 동일하므로 analyze가 이미 검증된 표기(ROE/영업이익률/PER/PBR/부채비율)로 읽는다. 예 4처럼 산업이 텍스트에만 있어도 analyze가 industry로 뽑는다. 못 뽑으면 정상 경로인 `asked`(산업 되묻기)로 간다. 예 1·2의 "찾아줘" 뒤 보충문은 `want_disclosures`·`ref` 등 추가 추출을 기대하며, 해석 품질은 §7에서 실호출 확인.

## 3. 케이스별 동작

| 상태 | 동작 |
|---|---|
| 아무 선택 없음 | 텍스트만 전송(현행 불변) |
| 산업만 | `"{산업} 중 {텍스트}"` 병합. 오류 없음 |
| 칩만(산업 없음) | `"{조건들} 조건. {텍스트}"` 병합. 막지 않음(텍스트에 산업이 있을 수 있고, 없으면 analyze가 되묻는 정상 경로) |
| 산업+칩 | `buildQuery` 문장 + 공백 + 텍스트 |
| 선택한 칩의 기준값이 비었음/범위 밖 | **전송 안 함.** `#cf-err`에 D14e와 같은 메시지, 폼을 열고(`cf.open = true`) 해당 입력에 포커스. `#msg` 초안 보존. 근거: 값을 조용히 버리면 사용자 의도와 다른 질문이 나가 가장 놀랍다 |
| 텍스트 비어 있음 | 현행대로 전송 안 함(`submit`의 `!text` 가드). 선택만으로 보내려면 `찾기` 버튼 |
| 입력이 `/^(산업\|시장\|기준일)을 /`로 시작(조건 칩 prefill로 만든 변경 문장) | 폼이 열려 있어도 **병합하지 않고 텍스트 그대로 전송**(`submit()` 병합 분기의 `!/^(산업\|시장\|기준일)을 /.test(text)` 예외) |
| 합친 길이 > 2000 | 전송 안 함, `#cf-err`에 §2 메시지 |
| 폼 `찾기` 경로 | **불변**(`buildQuery` 그대로, 텍스트 입력 무시, `#msg` 초안 보존) |

## 4. 선택 상태 정책 (초기화 vs 유지)

**선택값은 초기화하지 않고 유지한다. 대신 "폼이 열려 있을 때만" 병합한다.**

- 적용 조건: `cf.open === true`. D14e-1에 따라 모든 전송(폼·채팅) 시작 시 폼이 닫히므로, **전송 직후의 후속 채팅은 자동으로 병합되지 않는다.** 숨은 선택이 의도치 않게 붙는 일이 구조적으로 없다("보이는 선택 = 적용되는 선택").
- 사용자가 선택을 다시 쓰려면 폼을 수동으로 다시 열어야 하고, 그때 선택값이 그대로 보인다. D14e §7의 "전송 성공 시 폼 선택값 유지(미세 조정 재탐색 편의)"와도 충돌 없음 → **`찾기` 경로의 선택 유지 정책 불변.**
- 대안(전송 후 초기화)은 `찾기` 후 조건 미세 조정 흐름을 깨고 초기화 코드(select/체크/값 리셋)가 늘어 기각. 대안(항상 병합)은 닫힌 폼의 숨은 선택이 붙어 기각.
- **실패 시 복원**: `submit()`이 409/422/네트워크 오류로 끝나면(`r.ok` 아님) 전송 시작 때 닫았던 폼을 `wasOpen`으로 복원한다. 안 그러면 재시도 때 선택이 사라져 보이는 것과 달리 적용도 안 되어 사용자가 놀란다. 성공 시는 닫힌 채 유지(D14e-1 그대로).
- 새로고침/`새 분석`: D14e §7 그대로(폼 선택 복원 안 함 / 유지). 로드 시 폼은 항상 열림이고 선택은 비어 있어 병합은 `applied:false`.

## 5. 표시, D14d 복원 호환

- 전송 문장 = 병합된 문장 전체를 `submit`의 `text`로 쓴다 → `pendingCard`·`renderCard`의 카드 제목(h2), 오류 카드, `pushHistory({q:text,…})`가 **모두 병합 문장 그대로**. D14d `HISTORY_KEY` 구조·복원 로직 불변(q는 문자열). 새로고침 후 카드 제목도 병합 문장으로 복원된다.
- 채팅창에 별도 사용자 말풍선은 없다(카드가 질문 표시). 카드 제목이 길어질 수 있으나 최대 2000자 한도 내에서 기존 `h2` 줄바꿈 규칙에 맡긴다(일반 질문 입력과 동일).
- 전송 성공 시 `#msg.value = ""`(현행). 실패 시 초안 유지(현행).

## 6. UI 단서 (필요: 한 줄 안내)

선택이 적용되는지 모르면 놀라므로 **컴포저 위 한 줄**을 추가한다.

- `index.html`: `#form` 안 `.composer` 바로 위 `<p id="cf-note" class="hint" aria-live="polite" hidden></p>`.
- 문구(구현 일치): `선택한 조건(반도체 · 수익성 좋음 · 저평가)이 아래 입력창에 쓴 내용과 함께 전송됩니다. (입력 없이 보내려면 ‘찾기’)`. 산업만 선택이면 `선택한 산업(게임)이 …`(조건명 없으면 "조건" 대신 "산업"). 칩 항목은 값 검증과 무관하게 칩 label 목록(`d.label`)만 쓴다 → 값 오류에 영향받지 않는 단순 표시.
- `updateNote()`: 표시 조건 = `cf.open && (산업 선택됨 || 체크된 칩 ≥ 1)`, 아니면 `hidden`. 호출 시점: `cf-form`의 `change` 이벤트(select·체크박스; 이벤트 버블링 하나로 처리), `cf`의 `toggle` 이벤트, `renderCondForm()` 끝, `새 분석`은 불필요. 값 입력(`input`)은 문구에 영향 없으므로 구독하지 않는다.
- `help` 문구(`#help`)는 변경 없음. 스타일은 기존 `.hint` 재사용, 신규 CSS는 `#cf-note` 여백 한 줄 이하(선택).

## 7. 변경 지점

| 위치 | 변경 |
|---|---|
| `chat.js` 순수 영역 | `condParts(picks)` 추출, `buildQuery`가 이를 사용(동작 불변). `mergeQuery` 추가, `exported`에 `mergeQuery` 추가 |
| `chat.js` `init()` | `collectPicks()` 헬퍼(기존 `cfForm` submit 핸들러의 picks 수집 코드를 함수로 빼 `찾기`·채팅 경로 공유). `updateNote()` + 이벤트 구독(`cfForm` change, `cf` toggle). `cfNote = $("cf-note")` 조회 |
| `chat.js` `submit()` | 채팅 경로(`!fromForm`)에서 `text` 계산 직후, `cf.open`이고 입력이 `/^(산업\|시장\|기준일)을 /`로 시작하지 않으면(시작하면 병합 없이 텍스트 그대로 전송) `mergeQuery(cfIndustry.value, collectPicks(), text)` 호출 → `!ok`: `cfErr` 표시·`cf.open=true`·`field` 포커스 후 return(**`setBusy` 이전**이라 busy 미변경). `ok`: `text = r.text`. `setBusy(true)` 전에 `var wasOpen = cf.open;`, 실패 분기(409/422/오류 카드) 끝에서 `cf.open = wasOpen` |
| `chat.js` `setBusy` | 변경 없음(칩 영역 disabled는 기존). 단 `updateNote`는 busy와 무관 |
| `index.html` | `#cf-note` 한 줄 추가(§6) |
| `style.css` | 선택(§6). 필요 시 `#cf-note{margin:0 0 .25rem}` |
| 변경 없음 | `찾기` 경로, `pendingCard`/`renderCard`/`pushHistory`, D14d 복원, `common.js`, 백엔드 전부 |

## 8. 테스트/확인 방법

자동(node, `tests/frontend/d14e2.test.js`, 서버·LLM 불필요; `d14e.test.js` 회귀 포함):
- `mergeQuery`: §2 표 예 1~5 문자열 정확 일치, 산업만/칩만/둘 다/없음 4분기에서 `applied` 값, 칩 값 빈 문자열·범위 밖(ROE 101)·NaN → `ok:false`(산업만 선택 시에는 칩이 없으므로 통과), 합친 길이 2001자 → `ok:false`, 길이 정확히 2000자 → ok.
- `buildQuery` 기존 테스트 전부 통과(리팩터 회귀).
- jsdom 없이 가능한 범위만 자동화; DOM 동작은 수동.

수동(브라우저, 서버+키 환경):
- 폼 열림+선택 후 채팅 전송 → 카드 제목이 병합 문장, "현재 조건" 칩에 선택 반영, 직후 폼 닫힘·안내 문구 사라짐. 이어서 후속 채팅은 텍스트만 전송.
- 닫힌 폼을 다시 열면 이전 선택이 보이고 안내 문구가 다시 뜨며, 그 상태 전송은 병합.
- 서버 중지 상태(네트워크 오류)에서 병합 전송 → 폼이 다시 열려 있고 `#msg` 초안 유지.
- 기준값 지운 채 채팅 전송 → 전송 안 됨, 오류+포커스. `찾기` 경로·새로고침 카드 복원 정상.

## 구현 시 확인 항목

- **LLM 해석**(실호출): §2 예 1·2·4가 의도한 industry/constraints/`want_disclosures`로 추출되는지. 예 2처럼 폼과 텍스트가 같은 지표를 충돌시킬 때 후자 우선인지(어긋나도 사용자 텍스트가 결과에 반영되는지만 확인, 문구 조정은 구분자/순서 수준으로).
- 산업만 선택 + 종목 조회형 텍스트(`삼성전자 PER 알려줘`)가 `반도체 중 삼성전자 PER 알려줘`로 나가 lookup으로 인식되는지. 어색하면 산업만 케이스 접두 문구만 조정(예: `산업: {산업}. {텍스트}`).
- 직전 턴이 `asked`이면 followup 병합으로 이전 조건이 남는 현상(D14e §10과 동일, 이번 범위 밖).
- 안내 문구가 모바일(≤600px)에서 컴포저를 과도하게 밀지 않는지(한 줄 말줄임/줄바꿈 허용).
- `toggle` 이벤트는 `details.open` 프로그램적 변경에도 발생(비동기)하므로 `updateNote`가 상태를 `cf.open`에서 직접 읽도록 해 순서 문제를 피한다.
