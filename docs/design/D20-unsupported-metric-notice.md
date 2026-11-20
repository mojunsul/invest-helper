# D20 미지원 지표 요청 안내 (체크리스트 8번 ② 충족)

상위: DESIGN §12(제외 항목: "사용자가 요청하면 '제공 데이터로 계산 불가'로 안내한다"), §6, D6, D7(analyze), D9(respond), D11. **DESIGN.md·D0는 수정하지 않는다**(문서 말미 '수정 제안'만).

## 1. 문제 (실측·코드 확인)

- `analyze.py` SYSTEM_PROMPT는 metric을 7종(ROE/ROA/OPM/PER/PBR/PSR/DEBT_RATIO)으로 한정하지만, 7종 밖 요청에 대한 규칙이 없다. LLM은 "Forward PER 10 이하"를 `PER<=10`으로 치환하고, 이유는 llm_decision 로그에만 남는다.
- `respond._cond_line`은 `조건: ... PER ≤ 10배`만 표시 → 사용자는 Forward PER이 계산된 줄 안다. 원본 8번 "계산 값·충족 여부가 응답의 판단 기준과 일치"는 숫자상 맞지만 **사용자가 요청한 기준과 응답의 기준이 다름**을 알리지 않는다. DESIGN §12 약속 미구현.

## 2. 결정

### 2-1. '확인할 수 없는 값'(미지원 지표) 목록
지원 = `Metric` 7종(PER은 Trailing). 아래는 **미지원**(제공 데이터 = 금융위 재무요약·시총·공시에서 계산 불가 또는 추정 필요):
- 예상 실적 기반: Forward/선행/예상 PER, 예상 EPS, 컨센서스, 목표주가, 투자의견
- 주당·배당: EPS, BPS, 배당수익률·배당성향(D14e도 배당 미지원)
- 성장·추세·시계열: 매출·이익·EPS 성장률, 주가 상승률·수익률·모멘텀(재무 다기간 비교 없음)
- 기타 비율: PEG, EV/EBITDA, FCF·현금흐름, 유동비율, 이자보상배율 등 7종에 없는 모든 지표

위 목록은 프롬프트의 예시일 뿐 닫힌 집합이 아니다. 기준은 "7종 외 지표 = 미지원"이며 LLM이 판단한다(코드는 §2-3의 좁은 백스톱만).
(시가총액·산업·시장 같은 후보 필터는 지표가 아니므로 본 단위 대상 아님.)

### 2-2. 방식: 스키마 신호 + 코드 고정 문구 (프롬프트만 보강은 기각)
프롬프트만으로는 치환 여부를 코드가 모르고 응답 고지를 보장할 수 없다. 따라서:
- `AnalyzeOut.unsupported_metrics: list[str] = []` — 사용자가 말한 미지원 지표 이름 원문(예 "Forward PER", "배당수익률"). 기존 AnalyzeOut과 같이 누락 시 `[]`(구 응답·mock 호환).
- SYSTEM_PROMPT에 규칙 추가(JSON 키 목록에 한 항목 + 규칙 두 줄):
  - `unsupported_metrics: 위 7개 지표로 계산할 수 없는 지표를 요청하면 그 이름 원문 목록(예: Forward PER, EPS 성장률, 배당수익률, 목표주가). 이런 지표를 가장 비슷한 지원 지표(예: Forward PER→PER)로 바꿔 constraints에 넣지 말 것. constraints에는 7개 지표를 직접 말한 조건만 넣는다.`
  - `예: "Forward PER 10 이하 반도체" -> constraints [], unsupported_metrics ["Forward PER"]; "ROE 10% 이상, 배당수익률 3% 이상" -> constraints [ROE>=10], unsupported_metrics ["배당수익률"]`

### 2-3. 코드 후처리 (analyze, `_normalize` 직후)
1. **정제**: `unsupported_metrics` 각 항목 `str`·strip·공백 압축, 길이 ≤ 20(초과 항목은 절단하지 않고 버림), 제어문자·`|`·개행·마크다운/장식 문자(백틱·`[`·`]`·`‘’“”`·`※`·`*`)는 공백으로 치환, 중복 제거, 최대 3개. 지원 지표 라벨/코드와 정확히 같은 항목("PER","ROE","영업이익률"…)은 제거(LLM 오분류 방지).
2. **치환 백스톱(PER 변형 한정)**: 사용자 메시지가 `_VARIANT_RE`(대소문자 무시: `(forward|fwd|선행|예상|추정|컨센서스)\s*(PER|EPS|주가수익)` 및 `EPS`, `목표\s*주가`)에 맞고, 매치 부분을 지운 나머지 메시지에 `PER|주가수익`이 없으면 → LLM이 치환했다고 보고 `PER` constraint와 `PER` pending을 제거하고, 매치된 원문을 `unsupported_metrics`에 추가(LLM이 이미 목록에 넣었으면 중복 제거). 나머지 메시지에 일반 `PER`가 따로 있으면("PER 10 이하, Forward PER 12 이하") 제거하지 않는다(진짜 PER 조건 보존). 이 외 지표(배당·성장률 등)는 코드 백스톱 없이 LLM 신호에만 의존 — 임의 지표를 정규식으로 전수 포착하는 것은 범위 밖(구현 시 확인 항목).
3. **백스톱 위치·intent**: `_normalize` 직후, `extracted` 계산 전. 미지원 목록이 비어 있지 않은데 LLM이 `chitchat`으로 분류했으면 `screen`으로 보정(산업·기준 되묻기 + 고지). lookup/screen/refine 경로에서만 `out["unsupported_metrics"]`를 세팅(lookup은 `end()` 전).
4. 결과를 `out["unsupported_metrics"]`로 반환(비어 있으면 키 생략 가능, 있어도 무방).

### 2-4. 치환 대신 진행 방식: **미지원 조건만 제외하고 나머지로 진행 + 고지** (되묻기·중단 기각)
- 근거: (a) 거절/중단은 지원 조건까지 버려 사용자 이득이 없다. (b) 되묻기는 '기준 대체'를 사용자가 결정하게 하나, 어차피 대체 지표가 7종 안에 정확히 대응하지 않는다. (c) 조건 제외는 기존 흐름을 그대로 쓴다: 지원 조건이 있으면 screen 진행, **지원 조건이 0개면 기존 `threshold` missing 경로가 이미 되묻기**("어떤 지표와 기준값으로 찾을까요? 예: ROE 10% 이상")를 만든다 → 새 분기 없음.
- 원본 요구와의 모순 해소: 제외된 조건은 판단 기준(`_cond_line`의 조건 목록)에도 계산에도 없고, 고지 문구가 "제외"를 명시하므로 응답의 판단 기준 = 실제 계산 기준 = 사용자가 안 것과 일치한다. 요청한 값은 지어내지 않는다(F3008).
- refine/followup에서는 직전 conditions는 유지, 이번 턴의 미지원 지표만 제외·고지. `unsupported_metrics`는 **요청 단위(AgentState)이며 conditions·세션 메모리에 저장하지 않는다**(다음 턴에 재고지 없음).

### 2-5. 고지 문구·위치 (D9 respond)
고정 템플릿(LLM 미사용, 코드 상수) — 항목은 `‘{a}’, ‘{b}’`로 나열:
> ※ 요청하신 ‘Forward PER’은(는) 제공 데이터(확정 실적 기준 재무)로 계산할 수 없어 조건에서 제외했습니다. 지원 지표: ROE·ROA·영업이익률·PER(Trailing, 확정 실적 기준)·PBR·PSR·부채비율.

- 함수 `_unsupported_notice(state) -> str | None` (respond.py), 지원 지표 라벨은 `METRIC_LABEL`에서 생성.
- 위치: ① `_screen` answered/limit는 `_cond_line` **바로 다음 줄**, error는 `error_text` 다음 줄(error 분기는 `_cond_line`을 쓰지 않음). ② `status == "asked"`(지원 조건 0개·산업 누락 등)와 lookup(answered·error 모두)은 `_build`에서 본문 **앞**에 한 줄 + 빈 줄로 붙임(lookup이 error여도 고지). chitchat·filing_qa·report_save는 고지하지 않음. asked를 위해 `clarify_question`(analyze 생성물)은 건드리지 않고 respond가 붙인다(analyze는 문구 책임 없음).
- 예(모두 말미에 기존 DISCLAIMER):
  - 시나리오 A "Forward PER 10 이하 반도체 종목": asked 응답 =
    `※ 요청하신 ‘Forward PER’은(는) ... 제외했습니다. 지원 지표: ...` + 빈 줄 + `어떤 지표와 기준값으로 찾을까요? 예: ROE 10% 이상`
  - 시나리오 B 혼합: 정상 screen 응답의 `조건: 산업 반도체 · 시장 전체 · ROE ≥ 10%` 다음 줄에 `※ 요청하신 ‘배당수익률’은(는) ...`.

### 2-6. 로그 (D11)
새 이벤트 타입 없음. `unsupported_metrics`가 비어 있지 않으면 `end()` 안에서(lookup/screen/refine 경로) `LlmDecision(kind="conditions", decision="unsupported=Forward PER; dropped=PER(backstop) 또는 none", reason="unsupported_metric")`을 1회 발행(`[:REASON_MAX]`). 비어 있으면 발행하지 않아 기존 이벤트 수·순서 불변.

## 3. 영향 범위

| 대상 | 변경 |
|---|---|
| `analyze.py` | `AnalyzeOut.unsupported_metrics` 추가, SYSTEM_PROMPT(키 1개+규칙·예시 2줄), `_sanitize_unsupported`·`_VARIANT_RE` 백스톱, `out["unsupported_metrics"]`, 로그 1건. `fail`/chitchat/filing_qa/report_save 경로는 값을 반환하지 않음 |
| `state.py` AgentState | `unsupported_metrics: list[str]` 추가 (**D0 수정 제안 #1**, 가산적·선택 키, LangGraph가 키 없으면 필드를 버리므로 필요) |
| `respond.py` | `_unsupported_notice` 추가, `_screen`·`_build` 2곳 부착 |
| D6 constraints | **변경 없음**(미지원 조건은 constraints에 들어가지 않으므로 `evaluate`·계산식 불변) |
| memory/graph/plan/execute/judge | 변경 없음 |

기존 테스트 보정: 확인 결과 `AnalyzeOut` 새 필드는 기본 `[]`라 `tests/test_d7.py`(OUT 헬퍼 JSON)·`test_d18.py`·`test_d2e.py` 모두 **수정 불필요**, `test_d9.py`의 `S()` 상태는 `.get`으로 읽으므로 불필요. 이벤트 개수를 단정하는 테스트가 있어도 빈 목록이면 발행하지 않으므로 불변. 구현 시 전체 테스트 실행으로 회귀 확인(미확인 항목 → §6).

## 4. 시나리오 추적

1. **Forward PER 단독** "KOSPI 반도체 Forward PER 10 이하": LLM이 올바르면 constraints[], unsupported ["Forward PER"]; LLM이 `PER<=10`으로 치환해도 백스톱(`Forward PER` 제거 후 `PER` 없음)이 PER constraint 제거·"Forward PER" 추가. 지원 조건 0 → missing=["threshold"] → asked. 응답 = 고지 + "어떤 지표와 기준값으로 찾을까요?...". 로그: conditions/unsupported_metric. 실행·재계획 없음.
2. **혼합** "반도체 ROE 10% 이상, 배당수익률 3% 이상": constraints [ROE>=10], unsupported ["배당수익률"] → screen 정상 진행(plan→execute→judge). 응답: 요약, `조건: ... ROE ≥ 10%`, 고지 줄(배당수익률 제외). 표·판정은 ROE만 기준 — 판단 기준 = 계산 기준.
3. **혼합-PER 보존** "반도체 PER 10 이하 Forward PER 8 이하": 백스톱은 나머지 메시지에 `PER`가 있어 PER 보존. LLM 신호 ["Forward PER"], constraints PER<=10 → 고지 "Forward PER 제외", 조건에 `PER ≤ 10배`(Trailing 안내가 지원 지표 줄에 포함).
4. **미지원만, 지원 조건 없음, 목표주가** "삼성전자 목표주가 알려줘"(lookup): lookup 진행, 본문 앞에 `‘목표주가’ ... 제외` 고지 + 기존 재무 조회 응답.
5. **회귀** "반도체 ROE 10% 이상": unsupported [] → 고지·추가 로그 없음, 응답 바이트 동일.

## 5. 체크리스트 8번 ② 증빙 계획
- 실제 서버(GMS Claude)로 시나리오 1·2를 실행해 응답 스크린샷/JSON 저장: `docs/verification/D20-unsupported-metric.md` + `docs/verification/logs/d20-*.json`(응답 `answer`, `/api/logs` llm_decision 발췌). 체크포인트: 응답에 고지문 존재, `조건:` 줄에 Forward PER/PER 치환 없음(시나리오 1), 계산 값=판단 기준 대조(시나리오 2 ROE).
- 체크리스트 xlsx 8번 ② 확인 방법란에 위 문서 경로 기록(오케스트레이터).

## 6. 테스트 (LLM 목킹, test_d7.py 패턴 `OUT(...)` + `queue`)

신규 `tests/test_d20.py` (test_d7의 env fixture 재사용 또는 동일 monkeypatch):
- analyze: Forward PER 단독 LLM이 올바른 신호 → constraints 비어 asked, `out["unsupported_metrics"]==["Forward PER"]`, llm_decision(conditions, reason=unsupported_metric) 1건.
- analyze: LLM이 `PER<=10`으로 치환(unsupported 누락) + 메시지 "Forward PER 10 이하" → 백스톱이 PER 제거·목록 추가.
- analyze: 메시지 "PER 10 이하 Forward PER 8 이하" → PER 보존.
- analyze: 정제(길이>20 절단 제외·`|`개행 제거·중복·"PER"/"ROE" 항목 제거·3개 초과 절단).
- analyze: unsupported 빈 목록 → 추가 llm_decision 없음, 기존 결과 동일.
- respond: `_unsupported_notice` 문구·지원 지표 라벨 목록; `_screen` answered에서 `조건:` 줄 바로 다음 줄; asked·lookup에서 본문 앞; chitchat·filing_qa 무고지; 키 없는 state 무고지.
- 회귀: 전체 `pytest` 통과.

## 7. 구현 시 확인 항목
- 실제 모델(GMS Claude)이 "배당수익률·EPS 성장률·목표주가·PEG" 등을 `unsupported_metrics`로 잘 분류하는지 실호출 확인. 누락되는 지표는 `_VARIANT_RE`에 추가할지 판단(전수 목록 아님).
- `_VARIANT_RE` 오탐(예: "예상보다 PER 낮은") 여부 — 오탐 시 사용자 PER 조건이 제외되는 비용이 있으므로 패턴이 `예상\s*PER`처럼 붙은 경우만 맞는지 실측.
- 시나리오 1에서 industry가 있고 constraints가 없을 때의 asked 문구가 자연스러운지(고지+기존 되묻기).
- "PER만 더 낮게"류 refine에 Forward가 섞인 경우(pending 처리) 실호출.
- 시총 등 후보 필터성 조건 요청의 처리는 본 단위 범위 밖.

## 8. 수정 제안 (DESIGN.md·D0 — 승인 전 미반영)
1. **D0 §4 AgentState**: `unsupported_metrics: list[str]` 필드 추가(요청 단위, 기본 없음/[]; 세션 메모리 비저장). 한 줄 가산 변경이며 이외 계약 불변.
2. (선택) DESIGN §12 해당 문장 끝에 "(미지원 지표는 조건에서 제외하고 응답 조건 줄 아래에 고정 문구로 안내 — D20)" 한 구절. 승인 없이도 동작에는 영향 없음.
