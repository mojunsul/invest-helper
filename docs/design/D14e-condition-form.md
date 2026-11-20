# D14e 조건 선택 폼 (`frontend/index.html`, `frontend/chat.js`, `frontend/style.css`)

기준: D14 §3·§6·§9·§12, D14a §4·§8·§9(토큰·컨트롤·반응형), D14b(컴포저), D14d(카드 복원), D6 §2(지표·단위), D7 §3·§4(analyze 입력 스키마), D2/D2a(산업 사전). 의사코드/명세이며 구현 아님. **백엔드·API·D0·DESIGN 변경 없음. 프런트만 변경.**

## 1. 목적·범위

일반 사용자는 ROE/PER 같은 용어로 질문을 만들기 어렵다. 채팅 입력은 그대로 두고, **산업 드롭다운 + 조건 칩(체크박스) + 기준값(기본값 채움)** 폼을 추가한다. `찾기`를 누르면 프런트가 선택값으로 **자연어 한 문장**을 조립해 **기존 `submit()` 경로(`POST /api/chat`)로 그대로 보낸다**. 문장은 카드 제목(`h2`)에 사용자 질문으로 보인다.

| 소관 | 비소관 |
|---|---|
| 폼 UI, 문장 조립 순수 함수, `submit()` 인자 확장, 입력 검증(프런트), 스타일 | analyze/plan/respond, API, 산업 사전 변경, 새 지표 지원, 폼 상태 저장, 시장(KOSPI/KOSDAQ) 선택 |

## 2. 지원 조건 확인 (칩으로 만들 수 있는 범위)

코드 근거: `backend/agent/nodes/analyze.py`의 `METRIC_LABEL`/`DEFAULT_OP`, 시스템 프롬프트 "metric은 ROE, ROA, OPM(영업이익률), PER, PBR, PSR, DEBT_RATIO(부채비율) 중 하나, 값은 %·배를 뺀 숫자, '산업 평균 이하'는 ref=industry_avg". D6 §2와 일치.

- 지원 지표 7종: ROE, ROA, OPM, PER, PBR, PSR, DEBT_RATIO. 비교는 `>=`/`<=` 두 가지, 임계값은 숫자 또는 `industry_avg`(PER 외 metric도 조회는 가능하나 D6 산업 평균은 **PER만 계산**).
- **배당은 미지원**: `backend` 전체에서 배당 지표 없음(grep 0건), D3/D4/D6에도 없음. → **"배당 많음" 칩은 만들지 않는다**(지시 반영).
- 산업 평균 비교는 **PER만** 칩으로 제공(D6 `industry_avg_per`가 PER 전용).

## 3. 사용자 흐름

1. 첫 방문(카드 없음): 폼 패널 펼침. 카드가 복원된 방문(D14d)·새 분석 후: 패널 접힘(`<details>`; 사용자가 언제든 펼침).
2. 산업 선택 → 원하는 칩 체크(체크하면 기준값 입력이 활성, 기본값 표시) → 필요 시 값 수정 → `찾기`.
3. 프런트가 문장을 조립해 `submit(문장)` 호출 → 카드가 생기고 분석 진행(기존 §6 busy 처리). 문장은 `#msg` 입력창에 넣지 않는다(입력창 초안 보존).
4. 이후 대화는 기존대로 채팅창·"현재 조건" 칩(D14b)·후속 질문. 폼은 병행 사용.

## 4. UI 구성

위치: `<header>` 아래, 기존 `<form id="form" class="search">` **위**에 별도 `<form id="cf-form" class="search cf">`(중첩 폼 금지; 숫자 입력에서 Enter = 찾기, 채팅 textarea와 간섭 없음). 내부는 `<details id="cf">` + `<summary>조건으로 찾기</summary>(`<details id="cf" open>`: 로드 시 항상 열림)`.

구성(위→아래):

1. **산업** `<label for="cf-industry">산업</label><select id="cf-industry">`: 첫 옵션 `<option value="">산업 선택</option>`(기본), 이어 §6의 30개. 도움말 한 줄 `.hint`: "목록에 없는 산업은 아래 채팅창에 직접 입력하세요."
2. **조건 칩** `<fieldset><legend>조건 (여러 개 선택 가능)</legend>` 안에 칩 행 5개. 각 행 = `<label class="cf-chip"><input type="checkbox" id="cf-{id}"> 칩이름</label>` + (값 필요한 칩) `<input type="number" id="cf-{id}-v" aria-label="…" disabled> 단위` + `<span class="hint">한 줄 설명</span>`. 체크 해제 시 값 입력은 `disabled`(값은 유지). 칩은 시각적으로 알약(pill) — 기존 `.chip` 토큰(`--surface-2`, `--radius` 999px) 재사용, 체크됨은 `--accent` 테두리·`--info-bg` 배경.
3. **찾기 버튼** `<button type="submit" id="cf-go">찾기</button>`(기존 `#send`와 같은 primary 스타일 규칙에 `#cf-go` 추가) + 오류 메시지 영역 `<p id="cf-err" class="hint" role="alert" hidden>`.

### 칩 목록 (순서 = UI 순서 = 문장 순서)

| id | 칩 이름 | 지표 | 방향 | 기본값 | 입력 단위·범위 | 한 줄 설명(쉬운 풀이) | 문장 조각 |
|---|---|---|---|---|---|---|---|
| roe | 수익성 좋음 | ROE | 이상 | 10 | `%`, min 0 max 100 step 1 | "ROE: 내 돈(자기자본) 대비 얼마나 이익을 내는지. 높을수록 장사를 잘함" | `ROE {v}% 이상` |
| opm | 본업 이익률 높음 | OPM | 이상 | 10 | `%`, min 0 max 100 step 1 | "영업이익률: 100원 팔아 본업으로 남기는 이익" | `영업이익률 {v}% 이상` |
| per | 저평가 | PER | 산업 평균 이하 | 값 없음 | 입력 없음 | "PER: 이익 대비 주가 수준. 같은 산업 평균보다 낮으면 상대적으로 싼 편" | `PER 산업 평균 이하` |
| pbr | 자산 대비 저렴 | PBR | 이하 | 1 | `배`, min 0.1 max 20 step 0.1 | "PBR: 순자산 대비 주가. 1배 이하면 장부가보다 싸게 거래" | `PBR {v}배 이하` |
| debt | 부채 적음 | DEBT_RATIO | 이하 | 100 | `%`, min 0 max 1000 step 10 | "부채비율: 자기자본 대비 빚. 낮을수록 재무가 안정적" | `부채비율 {v}% 이하` |

- 모든 문구는 analyze 프롬프트 표기와 같다(ROE / 영업이익률 / PER / PBR / 부채비율, "배"·"%"는 LLM이 제거). PER은 "산업 평균 이하"(D7 §3 예시 문구 그대로).
- ROA·PSR는 칩으로 만들지 않는다(필수 아님, 칩 5개로 충분 — YAGNI). 필요 시 `COND_DEFS` 항목 추가만으로 확장.
- 기준값 입력은 **숫자 입력만**(슬라이더 없음: 접근성·모바일 정밀도·구현 단순).

접근성: select/input 모두 `<label>` 연결, 칩 체크박스는 키보드 토글(`Space`), 오류는 `role="alert"`, 포커스 스타일 기존 `:focus-visible`. 설명 `.hint`는 `aria-describedby`로 연결.

## 5. 문장 조립 규칙

순수 함수(`chat.js` 순수 영역, export, 테스트 대상):

```
COND_DEFS = [ {id:"roe", label:"수익성 좋음", desc:"…", unit:"%", def:10, min:0, max:100, step:1, fmt:v => "ROE "+v+"% 이상"},
              {id:"opm", …}, {id:"per", label:"저평가", desc:"…", fixed:"PER 산업 평균 이하"}, {id:"pbr", …}, {id:"debt", …} ]
INDUSTRIES = [...]                                   // §6

// picks: [{id, value}] (체크된 칩, UI 순서 그대로). 반환 {ok:true, text} | {ok:false, msg, field?}
buildQuery(industry, picks):
  if !industry or not in INDUSTRIES        → {ok:false, msg:"산업을 선택하세요.", field:"cf-industry"}
  if picks.length == 0                     → {ok:false, msg:"조건을 하나 이상 선택하세요."}
  for p in picks (값 필요 칩):
     v = Number(p.value)  // 빈 문자열은 NaN 취급
     if !isFinite(v) or v < def.min or v > def.max → {ok:false, msg:"{칩이름} 기준값은 {min}~{max} 사이 숫자로 입력하세요.", field:p.id}
  parts = picks.map(def.fixed ?? def.fmt(String(Number(value))))    // 숫자는 Number→String(10.0→"10", "010"→"10")
  return {ok:true, text: industry + " 중 " + parts.join(", ") + "인 종목 찾아줘"}
```

템플릿: `{산업} 중 {조건1}, {조건2}, …인 종목 찾아줘` (각 조건이 "이상/이하"로 끝나 "…이하인 종목"이 자연스럽다).

| # | 선택 | 생성 문장 |
|---|---|---|
| 1 | 반도체 + 수익성(10) + 저평가 | `반도체 중 ROE 10% 이상, PER 산업 평균 이하인 종목 찾아줘` (DESIGN §10 대표 요청 1과 같은 의미) |
| 2 | 자동차 + 자산 대비 저렴(1) + 부채 적음(150으로 수정) | `자동차 중 PBR 1배 이하, 부채비율 150% 이하인 종목 찾아줘` |
| 3 | 게임 + 본업 이익률(15로 수정) 단독 | `게임 중 영업이익률 15% 이상인 종목 찾아줘` |
| 4 | 화학 + 5개 전부(기본값) | `화학 중 ROE 10% 이상, 영업이익률 10% 이상, PER 산업 평균 이하, PBR 1배 이하, 부채비율 100% 이하인 종목 찾아줘` |
| 5 | 저평가 단독, 산업 은행 | `은행 중 PER 산업 평균 이하인 종목 찾아줘` |

모든 문장은 `maxlength 2000` 이내(최대 약 90자). 항상 완전한 screen 조건(산업+임계값)이라 analyze가 되묻지 않는 것이 정상 경로(D7 §4-3 `missing=[]`).

## 6. 산업 목록 출처

**정적 목록을 `chat.js`에 둔다.** 근거: 현재 산업 목록을 돌려주는 API가 없다(`backend/api/`는 chat/logs/reports뿐; 산업 사전은 `backend/identity/data/industry_terms.json` 파일을 `resolve.py`가 내부 로딩). 읽기 전용 엔드포인트 추가는 백엔드 변경(범위 밖)이며 라우터·테스트·CSP/에러 경로까지 늘어난다. 사전은 D2a에서 확정된 33항목으로 변동이 드물다.

- 값 = 사전의 `term` 중 **동의어 항목 3개(이차전지, 배터리, 의약품) 제외 30개**: 반도체, 반도체장비, 전자부품, 통신장비, 전기전자, 2차전지, 은행, 저축은행, 증권, 보험, 금융, 자동차, 자동차부품, 바이오, 제약, 제약바이오, 화학, 정유, 철강, 건설, 조선, 항공, 소프트웨어, 게임, 인터넷, 통신, 유통, 음식료, 식품, 전력가스.
- 전부 사전 `term`과 **정확히 일치**하므로 `resolve_industry`가 `_norm_term` 후 완전일치(`t[0]==q`)로 `ok` 판정(모호 없음). 표시와 값이 같아 `<option value>` = 표시 텍스트.
- **드리프트 방지 테스트**(§9): 목록 각 항목이 `industry_terms.json`의 `term`에 존재하는지 검사. 사전이 바뀌어 항목이 사라지면 테스트가 실패.

## 7. 엣지 처리

| 상황 | 처리 |
|---|---|
| 산업 미선택 | 전송 안 함. `#cf-err`에 "산업을 선택하세요." 표시, 포커스를 select로 |
| 조건 0개 | 전송 안 함. "조건을 하나 이상 선택하세요." |
| 기준값 비었거나 범위 밖/NaN | 전송 안 함. 해당 칩 이름과 범위 안내, 포커스를 그 입력으로 |
| 오류 후 정상 입력 | 다음 `찾기` 시작 때 `#cf-err` 숨김 |
| 처리 중(busy) | `찾기`·select·체크박스·값 입력 전부 `disabled`(`setBusy`에서 같이 처리). `submit`의 `busy` 가드도 유지 |
| 409/422/네트워크 오류 | 기존 `submit()` 처리 그대로(D14 §6). 폼 상태는 유지되어 재시도 가능 |
| 전송 성공 | 폼 선택값은 **유지**(조건 미세 조정 후 다시 찾기 편의). 입력창 `#msg` 값은 건드리지 않음 |
| `새 분석` | 세션·카드 초기화(기존). 폼 선택값은 유지(저장 대상 아님), 패널은 첫 방문 규칙대로 열지 않아도 됨 |
| 새로고침 | 폼 선택값은 복원하지 않는다(YAGNI). 보냈던 문장은 D14d로 카드에 복원됨 |
| `asked` 직후 폼 사용 | D7 §4-1 `followup`이 적용되어 이전 조건과 병합된다. 같은 지표는 덮어쓰기라 정상, 다른 지표의 이전 constraint가 남을 수 있음(§10 확인 항목) |
| 금융 산업(은행·증권·보험·금융·저축은행)+부채 적음 | 막지 않는다. 결과 안내는 D4a·D9가 처리 |

## 8. 기존 코드 변경 지점

| 파일 | 변경 |
|---|---|
| `frontend/index.html` | `<form id="form" class="search">` 앞에 `<form id="cf-form" class="search cf">…<details id="cf">`(§4) 추가. 칩·옵션 DOM은 JS가 `COND_DEFS`/`INDUSTRIES`로 채우므로 HTML은 컨테이너(`#cf-industry`, `#cf-chips`, `#cf-go`, `#cf-err`)만. 정적 규칙(D14 §11: 인라인 스크립트 금지) 유지. `#help` 문구는 변경 없음 |
| `frontend/chat.js` 순수 영역 | `COND_DEFS`, `INDUSTRIES`, `buildQuery` 추가, `exported`에 `COND_DEFS, INDUSTRIES, buildQuery` 추가 |
| `frontend/chat.js` `init()` | ① `cf` 관련 요소 조회 ② `renderCondForm()`: select 옵션·칩 행 생성, 체크박스 `change` → 값 입력 `disabled` 토글 ③ `cf-form` `submit` 핸들러: `buildQuery` 결과가 `ok`면 `submit(r.text)`, 아니면 `#cf-err` 표시·포커스 ④ `submit()`에서 `setBusy(true)` 직후 `cf.open = false`(폼·채팅 어느 쪽 전송이든 닫힘, 수동 재열림 가능; 로드 시엔 항상 열림이며 기록 유무와 무관) ⑤ `setBusy`에 `#cf-form`의 `button, input, select` `disabled = b` 추가 |
| `frontend/chat.js` `submit()` | 시그니처 `submit(textArg)`. `text = (typeof textArg === "string" ? textArg : msg.value).trim()`. 성공 분기의 `msg.value = ""`는 **인자 없이 호출된 경우(채팅 입력)에만** 실행. 나머지(카드 생성·`pushHistory`·타임라인·칩)는 동일 경로. 기존 호출부(`submit()`)는 동작 불변 |
| `frontend/style.css` | `.cf`·`#cf summary`·`.cf-chip`(알약, 체크 상태)·`.cf-row`(칩+값+설명 줄바꿈 flex)·`#cf-go`(primary) 추가. 기존 토큰(`--surface-2`, `--accent`, `--info-bg`, `--line`, `--radius-sm`)만 사용, 다크 모드는 토큰이 처리. `@media (max-width:600px)`에서 `.cf-row`를 세로 정렬, 좌우 패딩은 기존 `form.search` 규칙 공유 |
| 변경 없음 | `common.js`, `reports.*`, 백엔드 전부, D14d `HISTORY_KEY` 로직(질문 문자열이 문장이면 그대로 저장) |

D14a~d와의 정합: id 충돌 없음(`cf-` 접두), 기존 `#chips`(현재 조건 칩)와 분리, 컴포저·`#help`·sticky 규칙 미변경, D14d 복원·`새 분석` 키 삭제 로직 미변경.

## 9. 테스트/확인 방법

자동(node, `tests/frontend/d14e.test.js`; LLM·서버 불필요):
- `buildQuery`: §5 예시 5개 정확 일치, 산업 빈 값/목록 외 값 → 오류(필드 지정), 조건 0개 → 오류, 값 빈 문자열·NaN·범위 밖(ROE 101, PBR 0, 부채 -1) → 해당 칩 오류, `"010"`/`10.0` → `10`, 칩 순서가 입력 순서와 무관하게 `COND_DEFS` 순서(picks를 UI 순서로 넘기는 규약과 일치 확인).
- `INDUSTRIES` 모든 항목이 `backend/identity/data/industry_terms.json`의 `term`에 존재, 중복 없음, 길이 30.
- `COND_DEFS`의 지표 키워드가 analyze 지원 지표(ROE/영업이익률/PER/PBR/부채비율)로 시작하는지.
- 기존 `d14.test.js`·`d15*.test.js` 회귀 통과.

수동/실호출(analyze는 LLM이라 결정적 테스트 불가 → **미확인 항목은 구현 후 실호출로 확인**):
- 서버+키 환경에서 §5 예시 1~5를 폼으로 전송, 응답의 "현재 조건" 칩과 `conditions`가 의도와 일치하는지(status가 `asked`가 아닌지) 확인.
- 이어서 busy 중 폼 비활성, 오류 메시지 3종, 모바일 폭(≤600px)·다크 모드·키보드 조작 눈으로 확인.

## 구현 시 확인 항목

- **LLM 해석 정확도**: 5개 문구(특히 `PBR 1배 이하`, `영업이익률 10% 이상`)가 항상 같은 metric/op/value로 추출되는지 실호출 샘플 확인. 어긋나면 `fmt` 문구만 조정(백엔드 변경 불가).
- **`asked` 직후 병합 잔재**: 직전 턴이 `asked`이면 followup 병합으로 이전의 다른 지표 constraint가 남아 폼 문장 의도와 달라질 수 있다. 빈도가 높으면 후속으로 "폼 전송 시 `새 분석` 안내" 문구 또는 폼 전송 전 세션 갱신을 검토(이번 범위 밖).
- 임의 상수: 기본값(ROE 10 / OPM 10 / PBR 1 / 부채 100)과 min·max·step. 근거 약함 — 사용 후 조정.
- 금융 산업에서 `부채비율`·`PBR` UNDECIDABLE이 많이 나올 수 있음(D4a/D6): 결과 해석은 기존 안내에 위임.
- 칩 설명 문구 길이가 모바일에서 줄바꿈될 때의 레이아웃, `<details>` 초기 열림 상태가 D14d 복원 카드 목록을 과도하게 밀어내지 않는지.
- `INDUSTRIES`와 사전 불일치 방지는 테스트가 잡지만 사전에 **새 산업이 추가돼도** 목록은 자동 갱신되지 않는다(의도된 단순화; 필요하면 후속으로 읽기 전용 엔드포인트).

## 수정 제안 (DESIGN.md/D0 — 반영하지 않음)

없음. 선택 사항으로, DESIGN §9(프런트 화면) 목록에 "조건 선택 폼(D14e)" 한 줄을 추가하는 것만 사용자 승인 시 반영 가능하다.
