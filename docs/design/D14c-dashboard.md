# D14c 검색형 대시보드 (`frontend/index.html`, `chat.js`, `style.css`, `backend/api/chat.py` 소폭)

기준: DESIGN §2(의도 6종)·§4·§5·§9, D0 §4(Conditions·MetricRow·Verdict), D13 §5·§9, D14(세션·전송·저장·타임라인·접근성), D14a(색 토큰·반응형), D14b(composer), D15(common.js 공유), D17 §8(filing_qa 응답). 명세 F3017~F3019. 의사코드이며 구현 아님. **원칙: 바닐라 HTML/CSS/JS, 빌드·프레임워크 없음, 구현이 가장 단순한 쪽.** DESIGN·D0는 수정하지 않고 §10에 제안만 올린다.

## 1. 목적

- 말풍선 채팅 UI(D14a·D14b)를 **검색형 대시보드**(A안, 사용자 승인)로 바꿔 투자 분석 도구처럼 보이게 한다.
- 입력은 그대로 자연어 1줄이다. 조건 추출은 Agent(analyze)가 맡는다. 폼으로 조건을 직접 넘기지 않는다.
- 질문 1회는 **분석 실행 카드 1장**이 된다. 카드는 의도에 따라 표·지표 카드·근거 카드·텍스트로 렌더한다.
- 명세 매핑:

| 명세 | 이 화면이 충족하는 방식 |
|---|---|
| F3017 | `/api/chat`·`/api/reports` 그대로 사용(경로 불변, 응답 필드만 추가 §5) |
| F3018 | "대화 화면" = 대시보드(입력 = 대화, 실행 카드 목록 = 대화 기록), "계획 관리 화면" = reports.html(불변) |
| F3019 | 사이드 패널: 실행 단계·Tool 호출 이력·재계획 구분선(D14 `timelineRows` 재사용), 처리 중 상태, 오류 안내(카드 + 키 누락 배너) |

- 범위 밖: 로그 열람 UI, SSE 실시간 진행, 조건 삭제 칩(D0 §6-1 "삭제는 범위 밖"). (카드 복원은 D14d)

## 2. 화면 구성

### 2-1. 넓은 화면 (≥ 900px)

```
┌──────────────────────────────────────────────────────────────────────────┐
│ 투자 도우미                                   [저장된 리포트]  [새 분석] │ header
├──────────────────────────────────────────────────────────────────────────┤
│ ┌──────────────────────────────────────────────────────────┐ ┌────────┐ │
│ │ 예: 반도체 중 ROE 10% 이상, PER 산업 평균 이하              │ │  분석  │ │ form#form (상단)
│ └──────────────────────────────────────────────────────────┘ └────────┘ │
│ Enter 분석 · Shift+Enter 줄바꿈 · 조건 칩을 누르면 변경 문장이 채워집니다   │ #help
│ 현재 조건: [산업 반도체] [시장 전체] [ROE ≥ 10%] [PER ≤ 산업 평균]        │ ul#chips
├───────────────────────────────────────────────┬──────────────────────────┤
│ main: ol#log (최신 카드가 위)                   │ aside#side (sticky)      │
│ ┌───────────────────────────────────────────┐ │ ┌──────────────────────┐ │
│ │ "반도체 중 ROE 10%…"  [종목 탐색] 12:03:10  │ │ │ ⚠ API 키 누락 배너    │ │ #banner
│ │ 후보 12개 중 3개가 조건을 충족했습니다.      │ │ └──────────────────────┘ │
│ │ 산업 평균 PER 14.20배 (n=10, 후보 중앙값…)  │ │ 분석 중입니다…           │ #status
│ │ ↻ 재계획 1회: 충족 종목 없음 → 시장 확대     │ │ ▼ 실행 과정 (단계 9 ·    │ details#side-panel
│ │ ▼ 충족 3                                   │ │   도구 14 · 재계획 1)   │
│ │ ┌────────┬──────┬──────┬────────┬──────┐  │ │  의도 분석 · …· screen  │
│ │ │종목     │ ROE  │ PER  │재무기준 │      │  │ │  ▶ find_stock_… 호출   │
│ │ │삼성전자 │12.1% │ 9.8배│FY2025  │[저장]│  │ │  ◀ … 성공 · 120ms · …   │
│ │ └────────┴──────┴──────┴────────┴──────┘  │ │  ── 재계획 1: … ──       │
│ │ ▶ 불충족 7   ▶ 판단불가 2                  │ │  …                      │
│ │ [저장 패널: 이름/메모/확정/취소]            │ │                         │
│ │ [과정 보기]  ▶ 전체 응답 텍스트             │ │                         │
│ │ 투자 권유가 아니며 …(면책)                  │ │                         │
│ └───────────────────────────────────────────┘ │                         │
│ ┌ 이전 카드 … ┐                                │                         │
├───────────────────────────────────────────────┴──────────────────────────┤
│ 투자 권유가 아니며 전문가 조언을 대신하지 않습니다.                        │ footer#disclaimer
└──────────────────────────────────────────────────────────────────────────┘
```

### 2-2. 좁은 화면 (< 900px)

```
header
form#form · #help · #chips
aside#side   ← 한 열, 카드 목록 위. #banner·#status는 항상 보이고, details#side-panel은 접힘
main#log     ← 카드 목록
footer
```

### 2-3. 정적 골격 (`index.html`)

```
<header> h1 "투자 도우미" · nav( a href="reports.html" "저장된 리포트", button#new-chat "새 분석" )
<form id="form" class="search">
  <label for="msg" class="sr-only">분석할 질문</label>
  <div class="composer"> textarea#msg(maxlength=2000 rows=1 required aria-describedby="help") · button#send type=submit "분석" </div>
  <p id="help">…</p>
  <div class="chips-wrap"><span id="chips-label">현재 조건</span><ul id="chips" aria-labelledby="chips-label"></ul></div>
</form>
<div class="layout">
  <aside id="side" aria-label="실행 상태">
    <div id="banner" role="alert" hidden></div>
    <div id="status" aria-live="polite"></div>
    <details id="side-panel" open><summary id="side-sum">실행 과정</summary><ol id="timeline" class="timeline"></ol></details>
  </aside>
  <main>
    <p id="empty" class="empty">질문을 입력하면 분석 결과가 여기에 쌓입니다.</p>
    <ol id="log" aria-label="분석 실행 기록"></ol>
  </main>
</div>
<footer id="disclaimer">투자 권유가 아니며 전문가 조언을 대신하지 않습니다.</footer>
<script src="common.js"></script><script src="chat.js"></script>
```

- 기존 id(`form/msg/send/new-chat/log/status/banner/disclaimer`)는 유지해 JS 변경 폭과 test_d14 정적 점검을 그대로 둔다. `role="alert"`·`href="reports.html"`·스크립트 2줄도 유지한다.
- `#log`에서 `role="log"`·`aria-live`를 뺀다. 카드 전체를 읽어 주면 너무 길어서다. 완료는 `#status`(aria-live)가 한 줄로 알린다(§4-1).
- 버튼 이름 "새 대화"는 "새 분석"으로 바꾼다. 동작은 D14 §5와 같다(새 UUID 발급, 카드·칩·사이드 비움).

### 2-4. 사이드 접힘

- CSS: `@media (min-width: 900px)`에서 `.layout { display:grid; grid-template-columns: minmax(0,1fr) 320px }`. `aside`는 2열에 두고 `position: sticky; top: <header 높이>; max-height: calc(100dvh - …); overflow:auto`. 900px 미만은 한 열이고, `aside`가 DOM 순서대로 먼저 온다.
- JS: `init()`에서 `matchMedia("(max-width: 899px)").matches`이면 `#side-panel.open = false`로 한 번만 접는다. 리사이즈 추적은 하지 않는다(단순화). 이후 사용자가 직접 열고 닫는다.
- 네이티브 `<details>`를 써서 키보드·스크린리더 지원에 추가 JS가 필요 없다.

## 3. 의도·status별 렌더링

카드 공통 머리: 질문 원문(textContent), 의도 뱃지(`INTENT_TEXT`), status 뱃지(기존 `STATUS_TEXT`, answered는 뱃지 없음), 시각(`fmtTime(now)`).
카드 공통 꼬리: `[과정 보기]` 버튼(§6) · `<details><summary>전체 응답 텍스트</summary><div class="answer">linkify(answer)</div></details>`(텍스트형 카드에서는 생략) · `<p class="disclaimer">res.disclaimer</p>`.

`viewKind(res)` 판정 순서는 아래와 같다. 위에서부터 처음 일치하는 행을 쓴다.

| # | 조건 | kind | 본문 렌더 |
|---|---|---|---|
| 1 | `status` ∈ {asked, error} | text | `answer` 전체를 `.answer`(linkify)로 표시. asked=info 스타일, error=err 스타일. filing_qa `NO_INDEX`·lookup 오류·screen 오류(재계획 이력 포함)도 여기 해당 |
| 2 | `intent` ∈ {screen, refine} 이고 `verdicts` 비어 있지 않음 (answered·limit) | table | §3-1 |
| 3 | `intent`=lookup 이고 `metrics`에 키 1개 이상 (answered) | metric | §3-2 |
| 4 | `intent`=filing_qa 이고 `evidence` 비어 있지 않음 (answered) | evidence | §3-3 |
| 5 | 그 외 (chitchat, report_save answered, filing_qa `NO_RESULT`, intent 누락·미지값) | text | 1과 같고 스타일은 기본 |

- `headline(answer)` = `answer.split("\n\n")[0]`. respond는 screen·limit·lookup·filing_qa 요약을 첫 문단(개행 없는 한 문장 이상)에 둔다(D9 `_screen`/`_lookup`, D17 `_filing`). 구조화 카드는 이것을 헤드라인으로 쓴다. 전체 원문은 꼬리 details에 항상 남는다(단일 출처 보존).

### 3-1. table (screen/refine, answered·limit)

```
headline
[산업 평균 PER {v}배 (n={n}, 후보 집합 중앙값 · 공식 평균 아님){ · 표본 부족}]   ← industry_avg.PER 있을 때만. v=null → "산출 불가"
[↻ 재계획 {k}회: {TRIGGER}→{CHANGE}, …]                                       ← replans 비어 있지 않을 때
limit일 때: "조건을 충족한 종목이 없습니다. 위 조건 칩을 눌러 기준을 바꿔 다시 분석하세요."   (.hint)
<details open?> <summary>충족 {P}</summary> 표 </details>
<details>        <summary>불충족 {F}</summary> 표 </details>
<details>        <summary>판단불가 {U}</summary> 표 </details>
.save-panel (hidden)
```

- 그룹은 `groupVerdicts(verdicts)`로 나눈다. 순서는 서버 `verdicts[]` 순서(=후보 순)를 그대로 둔다. 0건 그룹은 생략한다. 처음 보이는 그룹 하나만 `open`(answered면 충족, limit면 불충족).
- 탭 대신 `<details>`를 쓴다. 상태 관리 JS·ARIA tablist가 필요 없다.
- 표 열: `종목`(이름 + 코드) · 조건 지표 열(`conditions.constraints`의 metric, 중복 제거, 순서 유지) · `재무기준`(`fiscal_period` + `estimated`면 `*`) · `사유` · (`want_disclosures`면) `공시` · (저장 가능하면) 동작 열.
  - 지표 값: `fmtMetric(m, metrics[code].values[m])`. ROE/ROA/OPM/DEBT_RATIO는 `{v.toFixed(2)}%`, 나머지는 `배`, null은 `-`.
  - FAIL 행에서 `failed`에 든 지표 셀은 `class="miss"`를 달고 값 뒤에 텍스트 " ✗"를 붙인다. 색만으로 구분하지 않기 위해서다.
  - 사유: FAIL이면 `"{라벨들} 미충족"`. `reasons` 중 THRESHOLD_NOT_MET 외 코드는 `REASON_TEXT`로 병기한다. UNDECIDABLE이면 `reasons.map(REASON_TEXT)`, 미정의 코드는 원문. PASS면 빈칸.
  - 공시: `disclosures[code]`의 각 `{date,title,url}` → `safeUrl(url)` 통과 시 `<a target=_blank rel="noopener noreferrer">{date} {title}</a>`, 실패 시 텍스트. 키가 없으면 "-".
  - 동작: `code ∈ saveable` 이면 `<button class="save-btn" aria-label="{name}({code}) 리포트 저장">저장</button>`. 아니면 빈칸이다.
  - `*`가 있으면 표 아래에 "* 요청 기준 연도 재무가 없어 직전 확정 연도 값으로 대체(추정)" 한 줄을 붙인다.
- 표는 기존 `.tbl-wrap` + `table.metrics`(D15 스타일)를 재사용한다. 숫자 셀은 `td.num`이다.
- 텍스트 표(D9)는 `MAX_ROWS=20`에서 자르지만 카드 표는 `verdicts` 전체를 그린다(후보 상한 D3 `MAX_LIMIT=100`).

### 3-2. metric (lookup answered)

```
headline
{stock_name}({code}) · 재무기준 {fiscal_period}{*}
<dl class="metric-grid">  ROE 12.10% | ROA … | 영업이익률 … | PER 9.80배 | PBR … | PSR … | 부채비율 …  </dl>
[저장]  (code ∈ saveable)
.save-panel (hidden)
```

- 대상 코드는 `Object.keys(metrics)[0]`이다. lookup은 대상 종목 1개만 조회한다(D8). 이름은 `saveable`에서 같은 코드를 찾고, 없으면 코드를 쓴다.
- 값이 null인 지표는 `REASON_TEXT[reasons[m]] || "재무정보 없음"`로 표시한다(D9 `_lookup`과 같은 규칙).
- 지표 7종 순서·라벨은 `METRICS`, `METRIC_LABEL`이다(서버 `METRIC_LABEL`과 같은 값을 chat.js에 상수로 둔다).

### 3-3. evidence (filing_qa answered, 근거 있음)

```
headline   (요약 1~3문장, [n] 인용 포함)
<ol class="evidence">
  <li> [1] {corp_name} · {title} · {date} · 접수번호 {rcept_no}
       섹션: {section}
       <blockquote>{text}</blockquote>
       <a href=safeUrl(url) target=_blank rel="noopener noreferrer">DART 원문 보기</a>   (safeUrl 실패 시 url 텍스트)
</ol>
<p class="hint">※ 공시 원문 발췌이며, 공시 접수일 이후 변동은 반영되지 않습니다.</p>
```

- 번호 `[n]`은 배열 순서(1부터)이다. respond 근거 블록과 같은 순서라 요약의 `[n]`과 맞는다(D17 §8).
- 링크 텍스트는 고정 문구이고 접근 이름은 `aria-label="{corp_name} {title} DART 원문 (새 창)"`이다.

### 3-4. 처리 중·전송 실패 (D14 §6 동작 유지)

| 상황 | 표시 |
|---|---|
| 전송 중 | 최상단에 **임시 카드**(질문 + "분석 중…", `aria-busy="true"`), `#status`="분석 중입니다… (최대 수십 초 걸릴 수 있습니다)", 입력·분석·새 분석·칩 버튼 disabled |
| 200 | 임시 카드를 결과 카드로 교체한다 |
| 409 / 422 | 임시 카드를 제거하고 입력을 유지한다. `#status`에 BUSY 문구 또는 서버 message(`role` 없이 aria-live). 별도 오류 카드는 만들지 않는다 |
| 5xx·네트워크 | 임시 카드를 error 카드(텍스트 = 서버 message 또는 고정 폴백)로 바꾸고 입력을 유지한다 |

## 4. 데이터 매핑 (응답 필드 → UI)

| 응답 필드 | 기존/신규 | UI |
|---|---|---|
| `answer` | 기존 | 텍스트형 카드 본문, 구조화 카드의 headline(첫 문단)·"전체 응답 텍스트" |
| `status` | 기존 | viewKind, status 뱃지, 카드 스타일 클래스 `status-*` |
| `verdicts[]` | 기존 | 표 행·그룹·사유(`reasons`,`failed`)·이름 |
| `industry_avg` | 기존 | 산업 평균 PER 줄(`PER.value`,`PER.n`, `n<3`이면 "표본 부족") |
| `steps[]`,`tool_calls[]`,`replans[]` | 기존 | 사이드 타임라인(`timelineRows` 그대로), 카드의 재계획 줄 |
| `disclaimer` | 기존 | footer·카드 꼬리 |
| `missing_api_key` | 기존(D13) | `#banner` 표시/숨김(응답마다 갱신) |
| `saveable[]` | 기존(D13) | 행/카드 저장 버튼 표시 여부·이름 |
| **`intent`** | **신규** | viewKind, 의도 뱃지 |
| **`conditions`** | **신규** | 조건 칩, 표의 지표 열·`want_disclosures` |
| **`metrics`** | **신규** | 표 지표 값·재무기준·`*`, lookup 지표 카드 |
| **`disclosures`** | **신규** | 표의 공시 열 |
| **`evidence`** | **신규** | filing_qa 근거 카드 |

### 4-1. 완료 알림 문구 (`#status`, `statusLine(res)`)

answered+table이면 "분석 완료 — 충족 {P} · 불충족 {F} · 판단불가 {U}", answered(그 외)이면 "분석 완료", asked이면 "추가 정보가 필요합니다", limit이면 "조건을 충족한 종목이 없습니다", error이면 "처리하지 못했습니다". 다음 전송 때 덮어쓴다.

### 4-2. 조건 칩 (`condChips(conditions)` → `[{label, prefill}]`)

| 원천 | label | prefill (입력창에 채울 문장, 사용자가 이어 씀) |
|---|---|---|
| `industry` | `산업 {industry}` | `산업을 ` |
| `market` | `시장 {market}`, null이면 `시장 전체` | `시장을 ` |
| `base_date` (값 있을 때만) | `기준일 {base_date}` | `기준일을 ` |
| `constraints[]` 각각 | `{라벨} {≥/≤} {value}{%/배}` 또는 `{라벨} {≥/≤} 산업 평균` | `{라벨} 기준을 ` |
| `pending_metrics[]` 각각 | `{라벨} 기준값 필요` | `{라벨} 기준을 ` |
| `want_disclosures` true | `공시 포함` | (없음, 버튼 아님·표시만) |

- `industry`가 null이고 constraints·pending이 모두 비면 칩 영역에 "아직 조건이 없습니다"를 표시한다.
- 칩은 `<li><button type="button" class="chip" aria-label="{label} 조건 변경 문장 입력">{label}</button></li>`이다. 클릭하면 `msg.value = prefill`, 포커스, 커서를 끝으로 옮긴다. **자동 전송하지 않는다.** 사용자가 "15% 이상으로 바꿔줘"처럼 이어 써서 보내면 analyze가 refine으로 처리한다(F3010·D0 §6-1 refine 행 재사용, 백엔드 변경 없음). prefill에 현재 숫자를 넣지 않는다. 이전 값이 다시 추출되는 오염을 막기 위해서다.
- 칩 갱신: 응답의 `conditions`가 객체일 때만 다시 그린다. null(예외 폴백)이면 이전 칩을 유지한다. chitchat·filing_qa·lookup 응답의 `conditions`는 메모리 값 그대로라(D10) 그대로 그려도 맞다. "새 분석"이면 비운다.

## 5. 백엔드·계약 변경

### 5-1. 판단 근거 (실제 코드 확인 결과)

- `backend/api/chat.py build_response`는 `answer,status,verdicts,industry_avg,steps,tool_calls,replans,disclaimer,missing_api_key,saveable`만 내려준다. `verdicts` 항목은 `{stock_code,stock_name,status,reasons,failed}`이고 **지표 값이 없다**. D13 §5-1에 "지표 값은 싣지 않는다"는 결정이 있다.
- 기존 필드만으로 할 수 있는 것: 그룹·사유·산업 평균·타임라인·저장 버튼.
- 기존 필드만으로 **할 수 없는 것**과 대안 비교:

| 필요 | 기존 필드로? | 대안 | 결정 |
|---|---|---|---|
| 표 지표 값·재무기준 | 불가 | `answer`의 파이프 표 파싱: 20행 절단, 미충족·판단불가 값 없음, 문자열 형식 결합 | `metrics` 추가 |
| lookup 지표 7종 | 불가 | `answer`의 `- 라벨: 값` 줄 파싱 | `metrics` 추가(같은 필드) |
| 조건 칩 | 불가 | `answer`의 "조건:" 줄 파싱: screen answered/limit에만 있음 | `conditions` 추가 |
| filing 근거 카드 | 불가 | "근거 공시" 블록 파싱: 4줄 형식 결합 | `evidence` 추가 |
| 의도별 렌더 분기 | 불가 | `steps[analyze].intent`: asked·chitchat도 있으나 analyze 실패 시 없음 | `intent` 추가(단순·명시) |
| 공시 열 | 불가 | answer 파싱 | `disclosures` 추가 |

- 결론: **응답에 5필드를 추가한다.** 모두 final state에 이미 있는 값(D0 §4 AgentState)을 그대로 내보내므로 새 계산·새 state 필드·그래프 변경이 없다. 요청 스키마, 다른 라우트, 이벤트, AgentState(D0)도 바뀌지 않는다.

### 5-2. `build_response` 변경 (의사코드)

```
정상 경로에 추가:
  "intent":      st.get("intent"),                 # Intent | null
  "conditions":  st.get("conditions"),             # Conditions(D0 §4) | null
  "metrics":     st.get("metrics") or {},          # {code: MetricRow} (D0 §4, D12 Report.metrics와 같은 형태)
  "disclosures": st.get("disclosures") or {},      # {code: [{title,date,url}]}
  "evidence":    st.get("filing_hits") or [],      # search_filing_text data 행 그대로(D17 §6)
실패(failed) 경로에 추가: intent=None, conditions=None, metrics={}, disclosures={}, evidence=[]
```

- 필드는 항상 존재한다(D13 규칙: "모두 비어도 항상 존재").
- **보안·신뢰 경계**: 모두 서버 코드가 만든 값이다. `metrics`는 이미 `/api/reports/{id}`로 노출되는 형태이고(D12 §5), `evidence`의 `text`는 응답 `answer`에 이미 발췌로 포함된다. 키·스택·원문 오류는 없다. 프론트는 전부 textContent로 그리고 URL은 `safeUrl`로 거른다.
- **DESIGN §9 "서버가 draft를 읽음" 원칙과의 관계**: 저장 요청은 여전히 `{session_id, stock_code, name?, memo?}`뿐이다. 프론트가 받은 `metrics`를 저장 요청에 싣지 않는다(`buildSaveBody` 불변, 서버 extra=forbid). 표시용 노출일 뿐 조작 경로가 아니다.
- 크기: 후보 상한 100 × MetricRow(숫자 약 13개) 정도로 수십 KB 이내다. 절단하지 않는다.
- `backend/schemas.py`에는 응답 모델이 없다(dict 반환)로 확인했다. 스키마 파일은 변경하지 않는다.

### 5-3. 문서 영향 (단위 문서)

- D13 §5-1 "지표 값은 싣지 않는다"와 §9 표는 이 문서로 대체된다. 구현 단계에서 D13 §5-1에 "D14c에서 `intent/conditions/metrics/disclosures/evidence` 추가" 주석 1줄만 단다.
- D17 §9 "`/api/chat` 응답 스키마 불변"도 같은 방식으로 주석 1줄을 단다.
- D14 §7~§9의 렌더 규칙(말풍선, "verdicts를 표로 그리지 않음")은 이 문서로 대체된다. 세션(§5)·전송(§6)·저장 결과 문구(§8 표)·타임라인 매핑(§9 매핑 상수)·접근성(§12)은 유지한다. D14a·D14b의 말풍선·하단 sticky composer 스타일은 이 문서의 레이아웃으로 대체된다(색 토큰·컨트롤 스타일은 유지).
- 상위 문서(DESIGN §9) 변경 부분은 §10에 분리했다.

## 6. 파일별 변경 범위

| 파일 | 변경 |
|---|---|
| `backend/api/chat.py` | `build_response` 정상·실패 dict에 5키 추가(§5-2). 그 외 불변 |
| `frontend/index.html` | §2-3 골격으로 교체. 스크립트 2줄·`role="alert"`·`href="reports.html"`·`label for` 유지. 인라인 script/`style=`/`on*=` 없음 |
| `frontend/chat.js` | **순수 영역 유지**(`uuidV4, getSessionId, timelineRows, buildSaveBody` 불변). **순수 함수 추가·export**: `INTENT_TEXT`, `METRICS`, `METRIC_LABEL`, `fmtMetric(m,v)`, `condChips(cond)`, `groupVerdicts(vs)`, `viewKind(res)`, `headline(answer)`, `statusLine(res)`, `avgText(industryAvg)`, `tableModel(res)`(→ `{cols:[metric], groups:[{status, rows:[{code,name,cells:[{text,miss}],basis,reason,disc:[{text,href}],saveable}]}], estimated}`). DOM 영역: `renderBot`를 `renderCard(res, question)`로 바꾸고 kind별 `renderTable/renderMetric/renderEvidence/renderText`, `renderChips`, `showTimeline(res)`(사이드 `#timeline`·`#side-sum` 갱신; 카드 `[과정 보기]` 클릭 시 해당 res로 호출하고 `#side-panel.open=true`, 좁은 화면이면 `#side.scrollIntoView()`; 새 응답은 자동 선택, 선택 카드에 `.selected`), 임시 카드(§3-4), 저장 패널 |
| 저장 패널 | 기존 `renderSave` 폼 로직(이름·메모·확정·취소, 결과 문구 표 D14 §8)을 **카드당 패널 1개 `openSave(card, item)`** 로 바꾼다. 행/카드 `[저장]`을 누르면 패널 제목 "{name}({code}) 리포트 저장"을 갱신하고 열어 포커스를 둔다. 저장 성공 시 그 행 버튼은 "저장됨"으로 바꾸고 disabled. 새 응답을 받으면 **이전 카드의 `.save-btn`을 전부 disabled, 패널은 hidden**(D14 §8 "최신 결과만 저장" 정책 유지) |
| `frontend/common.js` | 변경 없음(`el, safeUrl, linkify, api, storage*, fmtTime, REASON_TEXT, STATUS_TEXT` 재사용) |
| `frontend/style.css` | 말풍선 규칙(`#log > li.user/.bot`, `max-width:85%`, `li.bot details` 등)과 하단 sticky `form#form`을 제거하고 다음을 추가: 상단 `form.search`(max-width 1200px, sticky 아님), `.chips`/`.chip`(pill, `--surface-2`, focus-visible), `.layout`(§2-4 grid), `aside#side`(sticky, ≥900px), `#log > li.run`(카드: `--surface`, border, radius, shadow, `min-width:0`), `.run.status-asked/limit/error` 좌측 4px 테두리(토큰 info/warn/err) + 기존 뱃지, `.run.selected` 윤곽, `.run[aria-busy=true]` 흐림, `td.miss`(`--err` 글자색, 굵게), `.metric-grid`(`grid-template-columns: repeat(auto-fill, minmax(140px,1fr))`), `ol.evidence`/`blockquote`(`--surface-2`, `white-space:pre-wrap`, `overflow-wrap:anywhere`), `.save-panel`(기존 `.save-form` 규칙 재사용). 색 토큰·다크 모드·버튼·입력·`.tbl-wrap`·`table.metrics`·`.timeline`·reports 규칙은 유지. `@media (max-width:600px)` 블록에서 말풍선 규칙을 카드 패딩 규칙으로 바꾼다 |
| `frontend/reports.html` | nav 링크 텍스트만 "채팅으로 돌아가기" → "분석 화면으로". `href="index.html"`은 유지(test_d15 확인) |
| `frontend/reports.js` | 변경 없음 |

## 7. 테스트 관점

### 7-1. 기존 테스트 영향

| 테스트 | 영향 | 갱신 |
|---|---|---|
| `tests/frontend/d14.test.js` | 기존 6개(safeUrl·splitUrls·api·getSessionId·uuid·timelineRows·buildSaveBody) 대상 함수가 그대로라 **통과 유지** | 신규 순수 함수 테스트 추가(§7-2) |
| `tests/test_d14.py` | HTML/JS 정적 규칙, 스크립트 목록 `["common.js","chat.js"]`, `href="reports.html"`, `role="alert"`, label-for, 정적 서빙·CSP는 골격 유지로 **통과 유지** | 변경 없음. 신규 HTML도 속성 따옴표 규칙(`=(?!")`) 준수 필요 |
| `tests/frontend/d15*.test.js`, `tests/test_d15.py` | reports.html은 `href="index.html"`·`role="alert"` 유지로 **통과 유지** | 없음 |
| `tests/test_d13.py::test_chat_ok` | 응답 키 집합 정확 비교 → **실패** | 집합에 5키 추가 + mock state 기준 값 단언(`intent`, `conditions` 그대로, `metrics` 그대로, `disclosures={}`/값, `evidence=[]`) |
| `tests/test_d13.py::test_chat_exception` | 값 단언만 함 | 실패 경로 5키 기본값 단언 추가 |
| `tests/test_d16_e2e.py` `RESP_KEYS` | 집합 비교 → **실패** | `RESP_KEYS`에 5키 추가. 대표 요청 1의 `metrics` 키 ⊇ PASS 코드, lookup에서 `metrics` 1키 단언 1~2줄 |
| `tests/test_d17*.py` | 응답 키 집합 단언 없음(grep 확인) | filing_qa e2e가 있으면 `evidence` 길이 = hits 수 단언 1줄(선택) |

### 7-2. 신규 node 테스트 (`d14.test.js`에 추가, 내장 `node:test`)

- `fmtMetric`: % 지표와 배 지표, null → "-", 음수.
- `condChips`: industry/market null→"시장 전체"/value·ref 제약/pending/want_disclosures/전부 빈 값 → []. prefill에 숫자가 없음.
- `groupVerdicts`: 순서 보존, 미지 status 무시, 비배열 → 빈 그룹.
- `viewKind`: §3 표 1~5 각각 + asked가 intent보다 우선 + `intent` 누락 → text + limit+verdicts → table.
- `headline`: 문단 분리, 빈 문자열, 비문자열.
- `tableModel`: 열 = constraint metric 중복 제거, FAIL 셀 `miss`, UNDECIDABLE 사유 `REASON_TEXT`/미지 코드 원문, `estimated` → `*`·플래그, `want_disclosures`면 disc 링크 `href`가 `safeUrl` 결과(`javascript:`는 href null), saveable 반영, `metrics` 누락 코드 → "-".
- `avgText`: value null, n<3 "표본 부족", PER 없음 → null.
- `statusLine`: status 4종.

### 7-3. 수동 확인 (브라우저, 시연 전 1회 — D14 §11 시나리오를 카드 기준으로)

1. 대표 요청 1: 표 3그룹, 산업 평균 PER 줄, 행 저장 → 201 문구.
2. 대표 요청 2: 재계획 줄·사이드 구분선, limit 카드 + 칩 안내.
3. 칩 클릭 → 입력창 prefill → "15% 이상으로" 전송 → refine 카드, 칩 갱신.
4. lookup "삼성전자 PER 알려줘" → 지표 카드.
5. filing_qa → 근거 카드·DART 링크(새 창). 색인이 없으면 error 텍스트 카드.
6. chitchat·asked → 텍스트 카드.
7. 키 제거 → 배너.
8. 409(두 탭) → 임시 카드 제거.
9. 서버 종료 → error 카드.
10. 390px 폭: 사이드 접힘·가로 스크롤 없음(표는 `.tbl-wrap` 내부 스크롤).
11. 키보드만으로 칩→입력→분석→그룹 펼침→저장→과정 보기.
12. XSS 문자열(질문·mock evidence text·title, `javascript:` url)이 텍스트로만 표시되고 CSP 위반 0건.
13. 다크 모드.

## 8. 접근성 (D14 §12 유지 + 추가)

- 칩·저장·과정 보기 버튼에 `aria-label`로 대상을 명시한다(§3-1, §4-2).
- 처리 중: 임시 카드 `aria-busy`, `form aria-busy`, 완료는 `#status` aria-live 한 줄(§4-1).
- 그룹은 `<details>/<summary>`(건수 텍스트 포함), 표는 `<th scope="col">`/행 머리 `<th scope="row">`(종목)로 만든다.
- 상태는 색만으로 전달하지 않는다(뱃지 텍스트, `✗` 텍스트, 그룹 제목).
- 카드는 `<li class="run"><article aria-labelledby="{질문 h2 id}">`, 질문은 `<h2>`이다(시각적으로 1rem).

## 9. 구현 시 확인 항목 (FAIL 아님)

- `headline` = 첫 문단 규칙이 실제 respond 출력(LLM 요약에 개행이 섞이는 경우)과 맞는지 확인한다. D9 `_summary`·D17 요약은 공백을 정규화하지만, 어긋나면 headline이 길어질 뿐 전체 응답 details가 있어 정보 손실은 없다.
- 칩 prefill 후 이어 쓴 문장("ROE 기준을 15% 이상으로")이 analyze에서 refine으로 분류·추출되는지 실호출로 확인한다(LLM 의존). screen으로 분류되면 conditions가 초기화되므로(D0 §6-1), 필요 시 prefill 문구만 조정한다("현재 조건에서 ROE 기준을 ").
- 산업 변경 prefill("산업을 2차전지로")이 refine으로 처리되는지 확인한다. 새 screen으로 처리돼도 사용자 의도와 크게 어긋나지 않는다.
- 900px·320px 사이드 폭, 카드 최대 폭 1200px 등은 임의 상수다.
- `matchMedia` 1회 판정이라 회전·리사이즈 후 접힘 상태는 사용자 조작에 맡긴다.
- 후보 100개일 때 표 렌더 성능·응답 크기는 실측한다(예상 수십 KB).
- 이전 카드 저장 비활성 정책이 시연에서 불편하면 완화할지 판단한다(D14 §13과 같다).
- `metrics`에 원천값(revenue 등)도 포함된다. 표시는 지표 7종만 한다.
- `field-sizing: content` 미지원 브라우저에서는 textarea가 rows=1 고정이다(D14b와 같다).
- (구현 반영) 저장 버튼은 "PASS 행이면서 code ∈ saveable"일 때만 표시하고, status=limit 카드에서는 저장 열을 생략한다(이전 턴 draft 오인 방지).
- (구현 반영) `INTENT_TEXT`는 state.py Intent 리터럴 6종(filing_qa 포함) 전부를 담는다.
- (구현 반영) limit인데 verdicts가 비어도 `status-limit` 클래스로 limit 스타일을 일관 적용한다(카드 클래스는 status 기준).
- (구현 반영) 409/422 오류 문구는 `setBusy(false)` 이후에 `#status`에 쓴다(setBusy가 비우므로).

## 10. 상위 문서 수정 제안 (직접 수정 안 함, 승인 대기)

1. **DESIGN §9 `POST /api/chat` 응답**: `{answer, status, verdicts[], industry_avg, steps[], tool_calls[], replans[], disclaimer}`에 D13 기제안 `missing_api_key, saveable`과 본 단위의 **`intent, conditions, metrics, disclosures, evidence`** 를 추가한다. 근거는 §5-1(대시보드 구조화 렌더에 필요, final state 값을 그대로 노출, 저장은 여전히 서버 draft 기준)이다. 보조 문구 추가 제안: "응답의 `metrics` 등은 표시용이며 저장 요청에 포함하지 않는다(서버 draft 원칙 불변)".
2. **DESIGN §9 화면 정의**: `index.html(대화 + 단계/Tool 이력 타임라인, 재계획 구분 표시, 키 누락 안내)`을 → `index.html(검색형 대시보드: 자연어 입력·조건 칩·의도별 실행 카드(결과 표/지표/공시 근거)·사이드 실행 타임라인(재계획 구분)·처리 중·키 누락 안내)`로 바꾼다.
3. **DESIGN §1 트리 주석**: `frontend/ index.html(대화)`를 → `index.html(분석 대시보드 = 대화 화면)`로 바꾼다.

D0(AgentState·이벤트·ToolResult)는 변경하지 않는다. 불승인이면 §5의 5필드를 추가할 수 없으므로 표는 `verdicts`만으로 그리고(값 열 없이 상태·사유만), 칩·근거 카드·지표 카드는 생략한 뒤 텍스트 카드로 대체한다. 이 경우 화면 가치가 크게 떨어지므로 승인을 권장한다.
