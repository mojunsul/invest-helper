# 투자 도우미 AI 코어 설계 (v0)

근거: `invest-helper.md` §5~9. 이 문서는 구현 전 설계이며, ⚠ 표시는 **작업 순서 7번(실제 API 호출)** 에서 확인 후 확정할 가정이다.

## 1. 구조

```
invest-helper/
├─ backend/
│  ├─ main.py                 # FastAPI 앱, 라우터 등록
│  ├─ config.py               # .env 로딩 (키 `GMS_*`, 모델, REPLAN_MAX=3, TOOL_TIMEOUT_S, DB_PATH)
│  ├─ schemas.py              # Pydantic 요청/응답
│  ├─ api/  chat.py reports.py logs.py
│  ├─ agent/
│  │  ├─ graph.py             # LangGraph 조립
│  │  ├─ state.py             # AgentState
│  │  ├─ nodes/ analyze.py plan.py execute.py judge.py respond.py
│  │  ├─ constraints.py       # 지표 계산·산업 평균·충족 판단 (순수 함수, LLM 없음)
│  │  └─ memory.py            # 세션별 조건 요약
│  ├─ tools/
│  │  ├─ base.py              # ToolResult, 실패 코드, @register
│  │  ├─ registry.py          # 이름→Tool, Agent에는 이름·설명·스키마만 노출
│  │  └─ stock_candidates.py  financials.py  disclosures.py  filing_text.py(심화 F3022, 로컬)
│  ├─ events/
│  │  ├─ bus.py               # publish(event) / subscribe(fn), 구독자 없어도 무해
│  │  └─ logger_agent.py      # 구독 → SQLite 추가 저장 (LLM 미사용)
│  └─ db/ sqlite.py           # reports, run_logs
├─ frontend/  index.html(대화)  reports.html(관리)  *.js
├─ scripts/   build_filing_index.py  # 심화 F3022: 공시 원문 오프라인 수집·색인(chromadb)
├─ .env.example
└─ README.md                  # 실행법, 외부 API 출처·이용조건, 구현 범위
```

NF3006(분리)과 NF3001(키는 `.env`만)은 구조로 충족한다.

## 2. 도메인·요청 정의

- 기준 조건: 산업, 기준일(기본=최근 영업일) / 선호: 시장(KOSPI·KOSDAQ) / 제약: 지표 임계값(ROE≥, ROA≥, 영업이익률≥, PER≤, PBR≤, PSR≤, 부채비율≤, `PER<=산업평균` 형태 허용)
- 의도 6종: `screen`(탐색+판단) · `lookup`(단일 종목 지표) · `report_save`(저장) · `refine`(후속 조건 변경) · `chitchat` · `filing_qa`(공시 본문 서술 질문, 심화 F3022 — `docs/design/D17-filing-rag.md`)
- **되묻기**: 임계값 없는 "ROE 높은 종목"은 Tool 호출 전에 되묻는다. 산업이 없어도 되묻는다.
- 면책: 모든 최종 응답 끝에 "투자 권유가 아니며 전문가 조언을 대신하지 않습니다" 고정 문구를 **코드가 붙인다**(LLM 생성에 맡기지 않음).

## 3. Tool 정의 (F3001~F3004 외부 API 툴 + 심화 F3022 로컬 툴)

모든 Tool은 `kind`로 `external`(외부 API 툴)/`local`(로컬 서버 API 툴)을 명시해 등록한다(D1c).

공통 응답:
```python
ToolResult = {"ok": bool, "data": ..., "error": {"code": str, "message": str} | None,
              "meta": {"requested_basis": ..., "actual_basis": ..., "estimated": bool}}
```
`meta`는 항상 객체(`requested_basis/actual_basis`: `str|None`, `estimated`: bool). `ok=True`면 `data` 필수·`error=None`, `ok=False`면 `error` 필수. 빈 결과는 `NO_RESULT`. `NO_RESULT/NO_FINANCIALS/NO_INDEX`는 `ErrorCode`에 포함하며 치명은 `MISSING_API_KEY`뿐이다. 종목 단위 `API_ERROR/RATE_LIMIT/TIMEOUT`은 해당 종목 UNDECIDABLE(`FETCH_FAILED`). 상세는 `docs/design/D0-contract.md`.
모든 Tool은 예외를 던지지 않고 `ok=False`로 바꿔 반환한다. `message`는 사용자 노출용 요약이며 원문 오류는 로그에만 남긴다(NF3002).

공통 실패 코드: `MISSING_API_KEY`(치명) · `INVALID_INPUT` · `API_ERROR` · `RATE_LIMIT` · `TIMEOUT`

| Tool | 설명(= 언제 쓰나, Agent가 보는 문장) | 입력 | 출력 data | 전용 실패 코드 |
|---|---|---|---|---|
| `find_stock_candidates` | 사용자가 산업·시장으로 종목을 찾아야 할 때, 비교 대상 후보 목록을 얻는다 | `industry:str`, `market:str?`(None=전체), `base_date:str?`, `extra_industries:list[str]?`, `limit:int?` | `[{code, name, market, industry, mkt_cap, price}]` | `NO_RESULT` |
| `get_financials` | 후보 종목의 PER·PBR·ROE·부채비율이 필요할 때 종목코드 하나로 조회한다 (내부에서 종목코드→법인등록번호 변환) | `stock_code:str`, `base_date:str?` (→ `bizYear` 결정, 기본=가장 최근 확정 사업연도) | `{code, fiscal_period, revenue, op_profit, net_income, assets, equity, debt_ratio, mkt_cap}` (원천값) | `NO_FINANCIALS` |
| `search_disclosures` | 종목의 최근 공시 제목·일자·링크를 보여줄 때 쓴다. 수치는 주지 않는다 | `stock_code:str`, `limit:int=3` | `[{title, date, url}]` | `NO_RESULT` |
| `search_filing_text` (local, 심화 F3022) | 외부 API로 확인할 수 없는 공시 본문 서술(사업 내용·실적 전망·가이던스·위험 요인·경영진 분석 등)을 물을 때, 미리 색인한 공시 원문에서 근거 문단을 찾는다. 수치 지표 조회에는 쓰지 않는다 | `query:str`, `stock_code:str?`, `top_k:int=3` | `[{text, score, stock_code, corp_name, title, rcept_no, date, section, url}]` | `NO_INDEX`, `NO_RESULT` |

- 기본값: `base_date` 미지정 → 최근 영업일. 재무는 확정 분기 기준이므로 `meta.actual_basis != requested_basis` 이면 `estimated=true`로 밝힌다(F3002).
- 데이터 흐름: `find_stock_candidates.code` → `get_financials.stock_code` → (통과 종목만) `search_disclosures.stock_code`.
- Tool로 두지 않는 것: 지표 계산, 산업 평균, 충족 판단, 리포트 저장 (실행 흐름이 직접 수행).
- 심화 F3022 `search_filing_text`: 사전 지정 종목의 사업보고서 원문을 `scripts/build_filing_index.py`가 오프라인으로 수집해 chromadb에 색인하고, Tool은 로컬 색인만 읽는다(요청 중 DART 원문 호출 없음). 색인이 없으면 `NO_INDEX` → 고정 형식 "아직 모으지 못했습니다" 응답. 상세는 `docs/design/D17-filing-rag.md`.

**실호출 검증 결과 (2026-11-18, 삼성전자)**
- 재무정보 API(`getSummFinaStat_V2`)는 `crno`(법인등록번호)+`bizYear`로 조회. **연간** 데이터만 제공(분기 없음). 연결(`fnclDcd=110`)·별도(120) 두 행이 오며 **연결 우선**, 없으면 별도. 2025년분까지 조회됨.
- 필드 매핑: `enpSaleAmt`=매출, `enpBzopPft`=영업이익, `enpCrtmNpf`=당기순이익, `enpTastAmt`=자산, `enpTdbtAmt`=부채, `enpTcptAmt`=자본총계, `fnclDebtRto`=부채비율. → 영업이익률·ROA·ROE·부채비율은 이 API만으로 계산 가능. PER·PBR·PSR은 시가총액이 필요하므로 주가 API 필요.
- ID 매핑 체인: `stock_code` → DART `corpCode.xml`로 `corp_code` → DART `company.json`으로 `jurir_no`(=crno)·`induty_code`(산업, KSIC). 금융위 기업기본정보 API의 이름 검색은 "삼성전자잠실판매" 같은 다른 법인을 반환해 **사용하지 않는다**.
- 산업 분류 출처는 DART `induty_code`로 확정. 단 "산업 → 후보 목록"을 바로 주는 API는 없으므로, 상장사 `corpCode.xml`에서 종목별 `induty_code`를 한 번 모아 로컬 캐시(SQLite)한 뒤 산업으로 필터한다(DART 호출 한도 확인 필요).
- 주가 API는 **`https://apis.data.go.kr/1160100/GetStockSecuritiesInfoService_V2/getStockPriceInfo_V2`** (`/service` 없음, `_V2`; 구버전 경로는 403). 파라미터 `basDt`, `likeItmsNm`. 응답: `basDt, srtnCd(종목코드), itmsNm, mrktCtg(KOSPI/KOSDAQ), clpr(종가), mrktTotAmt(시가총액), lstgStCnt(상장주식수)` → PER·PBR·PSR 계산용 시가총액 확보. 기준일 휴장일 동작·`likeItmsNm`의 부분일치 문제(이름 검색 시 유사 종목 포함)는 Tool 구현 시 `srtnCd`로 정확히 매칭.

## 4. Agent 실행 흐름 (LangGraph)

```
analyze ─┬─(chitchat)────────────────────────────► respond
         ├─(조건 부족: 되묻기)────────────────────► respond
         ├─(filing_qa)─► plan ─► execute ─────────► respond   (judge·재계획 없음, D17)
         └─► plan ─► execute ─► judge ─┬─(충족≥1 | 치명실패 | 상한)─► respond
                       ▲                │
                       └── replan ◄─────┘ (충족 0 & replan_count<3 & 재시도 가치 있음)
```

`replan`은 노드가 아니라 분기 결과이며, 후보 0개(`NO_RESULT`)는 execute 직후에도 재계획 분기로 간다. 공시(`search_disclosures`)는 통과 종목에만 호출하며 **judge 끝에서 호출한다**(D8 확정, D0 §6-11; 새 노드·분기 없음 → 위 그래프 불변). 상세는 `docs/design/D0-contract.md`.

노드별 책임:

| 노드 | LLM | 하는 일 |
|---|---|---|
| analyze | O | 의도 분류 + 조건 추출(구조화 출력). 못 뽑은 조건은 `None`. 이전 조건과 병합(§7). 의도·이유를 이벤트로 발행 |
| plan | 규칙 우선 | 의도·조건으로 Tool 순서 결정. screen=[candidates, financials(후보별)], 공시 요청 시 +disclosures, filing_qa=[search_filing_text]. 불필요 Tool 제외 |
| execute | X | 계획대로 호출. 이전 결과를 `tool_cache`(키: tool+입력)에서 먼저 조회 → 재사용. 개별 실패는 사유 기록 후 계속 |
| judge | X | `constraints.py`로 계산·판단. 충족/불충족/판단불가 분류. 끝에서 `want_disclosures`이고 통과 종목이 있으면 통과 종목에 `search_disclosures` 호출(공시 실패는 종목별 표기, 판정·status 불변) |
| respond | O(문장화) | 결과 표·근거는 코드가 만들고 LLM은 요약 문장만. 면책 문구 코드 부착. 리포트 초안 생성. filing_qa는 근거 문단·출처(공시명·접수번호·일자·링크·발췌) 블록을 코드가 붙임 |

`AgentState` 필드: `session_id, user_msg, intent, intent_reason, conditions{industry,market,base_date,constraints[],want_disclosures,pending_metrics}, plan[], tool_cache{}, candidates[], metrics{code:{...}}, verdicts{code:{status,reasons,failed}}, industry_avg{value,n}, search_scope, expansion_step, replan_count, replan_log[], errors[], status, clarify_question, missing, target_stock, disclosures, filing_hits, prev_intent, answer, report_draft`
- verdict `PASS/FAIL/UNDECIDABLE`. 집계는 FAIL 우선이되 `reasons`에 UNDECIDABLE 사유도 병기(F3011). `Constraint`는 value/ref 택일. `want_disclosures` 기본 False(사용자가 공시를 요청할 때만 공시 포함).
- 상세는 `docs/design/D0-contract.md`.

### 재계획 규칙 (F3009, NF3004)
- 재계획 조건: **충족 종목 0개**이고 치명 실패가 아닐 때만. 단 `NO_RESULT` 또는 판정 가능 종목 ≥1일 때만 허용하며, 판정 가능 0개(전부 UNDECIDABLE)·후보 조회가 `NO_RESULT` 외로 실패한 경우는 재계획 없이 `status=error`.
- 비재계획: `MISSING_API_KEY`, `INVALID_INPUT`, 후보 조회 `NO_RESULT` 외 실패, 후보 0개이고 확장 여지 없음 → 즉시 안내.
- 확장 순서: ① 시장 확대(KOSPI→KOSPI+KOSDAQ) ② 유사 업종 추가 ③ 후보 상한 증가. 효과 없는 단계(시장 미지정 ①, 유사 업종 없음 ② 등)는 건너뛰고 **효과 있는 단계만 세며** 최대 `REPLAN_MAX`회. 유효 단계가 없으면 재계획 없이 limit(후보 0개면 error).
- 사용자가 시장을 명시했어도 ①로 확대할 수 있다(`conditions.market` 보존, `search_scope.market`만 변경). **확대했을 때 응답에 그 사실을 명시한다.**
- 상한 `REPLAN_MAX=3`(.env, 1~3으로 클램프). 도달 시 사유 + "임계값을 완화할지 되묻기" 응답. 임계값 자동 완화는 하지 않는다(사용자 조건 침해).
- 이미 조회한 `get_financials`는 `tool_cache` 적중으로 재호출하지 않는다.

## 5. 제약 판단 계산식 (F3008) — 전부 코드

| 지표 | 식 | 비고 |
|---|---|---|
| PER (Trailing) | 시가총액 ÷ 당기순이익 | 확정 실적 기준, 응답에 기준 분기 명시. 순이익 ≤ 0 → 판단불가(`NEGATIVE_EARNINGS`), 값을 만들지 않음 |
| PBR | 시가총액 ÷ 자본총계 | 자본 ≤ 0 → 판단불가 |
| ROE | 당기순이익 ÷ 자본총계 × 100 | 분기 순이익이면 연환산하지 않고 기간을 응답에 명시 |
| 부채비율 | 부채총계 ÷ 자본총계 × 100 (API가 주면 그 값) | |
| 영업이익률 | 영업이익 ÷ 매출액 × 100 | 매출 ≤ 0 → 판단불가. 영업적자는 음수 그대로 비교 |
| PSR | 시가총액 ÷ 매출액 | 매출 ≤ 0 → 판단불가. 순이익 적자로 PER이 판단불가인 종목의 대체 밸류에이션 지표 |
| ROA | 당기순이익 ÷ 자산총계 × 100 | 자산 ≤ 0 → 판단불가. ROE와 같은 기간 기준 |
| 산업 평균 PER | 후보 집합 중 PER 산출 가능 종목의 **중앙값** | 평균은 이상치에 취약해 중앙값 채택. 표본 수 `n`을 함께 표시, n<3이면 "표본 부족" 표기 |

- 비교: `metric <op> threshold`(op: ≥ ≤). `PER<=산업평균`은 `industry_avg.PER`로 치환 후 비교.
- 값이 없으면 `verdict=UNDECIDABLE(사유)`. 충족/불충족과 섞지 않고, 응답에 제외 사유로 노출(F3011).
- 산업 평균은 "후보 집합 기준"임을 응답에 명시(공식 산업 평균이 아님 → 역산 기준 제시 원칙).

## 6. 이벤트·로그 (F3012~F3014)

버스: 함수 호출형 `publish(event)`; 구독자 리스트가 비어도 정상. 구독자 예외는 삼켜서 메인 흐름을 막지 않는다.

공통 필드: `session_id, seq, ts, type, payload`

| type | payload 핵심 |
|---|---|
| `stage_start` / `stage_end` | `stage`, (analyze 종료 시) `intent`, `reason` |
| `tool_call` | `tool`, `input`, `cache_hit` |
| `tool_result` | `tool`, `ok`, `error_code`, `duration_ms`, `summary` (원본 전체 X, 건수·핵심값) |
| `llm_decision` | `kind`(intent/…), `decision`, `reason` |
| `replan` | `count`, `trigger`, `change` (예: 시장 확대) |
| `final` | `status`(answered/asked/limit/error) |

- `seq/ts`는 버스가 세션 단위로 부여: `publish(session_id, type, payload)`. run 경계는 stage_start(analyze)~final. 의도는 stage_end·llm_decision 양쪽 발행, 건너뛴 stage는 미발행, 재계획 시 재실행 노드의 start/end 재발행. `llm_decision.kind`/`replan.trigger`/`replan.change` 값 집합과 상세는 `docs/design/D0-contract.md`.

저장: `run_logs(id, session_id, seq, ts, type, payload_json)` 추가 전용(정렬은 `id` 기준 보완). API는 목록·상세·삭제만(수정 없음). `logger_agent`는 LLM import 자체를 하지 않는다.

## 7. 대화 맥락 (F3010)

- 세션 메모리 = `{conditions, last_intent, last_status, last_result_summary(상위 종목코드·충족 개수), report_draft}`. Tool 원본 응답은 보관하지 않는다. AgentState는 요청마다 새로 만들어 메모리로 채우며 `tool_cache`는 요청 간 유지하지 않는다(재계획 내에서만 재사용).
- `refine`: 새로 추출된 필드만 덮어쓴다("PER만 더 낮게" → 산업·ROE 유지, PER 임계값만 갱신; constraints는 metric 단위 덮어쓰기). 단 **추정한 새 임계값**은 만들지 않고 "현재 PER 상한 X → 얼마로 낮출까요?"로 되묻는다. 직전 결과 PER 분포 역산은 `last_result_summary`에 지표 값이 없어 이번 범위에서 미지원(되묻기만).
- 후속 병합: 직전 status가 asked(+`prev_intent`∈screen/refine)이면 후속 병합, 새 screen은 conditions 전체 초기화, chitchat·filing_qa는 메모리 불변. `lookup/report_save`는 `target_stock`을 쓰고 조건 불변. 상세는 `docs/design/D0-contract.md`.
- 저장소: 프로세스 메모리 dict(키=session_id). 서버 재시작 시 소실 — 이번 범위에서는 감수.

## 8. 저장소 (SQLite)

```
reports(id, name, stock_code, stock_name, base_date, metrics_json, industry_avg_json,
        disclosures_json, disclaimer, memo, created_at, updated_at)
run_logs(id, session_id, seq, ts, type, payload_json)
```
- 중복: `(stock_code, base_date)` 동일 레코드 존재 시 **저장은 진행**하고 응답에 `duplicate_of:[id…]` 포함(F3015).
- 수정 가능 필드: `name`, `memo`만(F3016). 지표 값은 불변 — 근거가 바뀌지 않게.
- 저장은 Tool이 아니라 API/흐름이 직접 수행(사용자가 이미 정한 값이므로).

## 9. API 명세 (FastAPI, Pydantic)

| 메서드 경로 | 요청 | 응답 |
|---|---|---|
| `POST /api/chat` | `{session_id, message}` | `{answer, status, verdicts[], industry_avg, steps[], tool_calls[], replans[], disclaimer}` |
| `GET /api/reports` | – | `[ReportSummary]` |
| `GET /api/reports/{id}` | – | `Report` |
| `POST /api/reports` | `{session_id, stock_code, name?, memo?}` (채팅 결과 draft를 서버가 조회) | `{id, duplicate_of:[]}` |
| `PATCH /api/reports/{id}` | `{name?, memo?}` | `Report` |
| `DELETE /api/reports/{id}` | – | `204` |
| `GET /api/logs` | – | `[{session_id, started_at, event_count}]` |
| `GET /api/logs/{session_id}` | – | `[Event]` (seq 순) |
| `DELETE /api/logs/{session_id}` | – | `204` |

- 로그 수정 엔드포인트 없음. 오류 응답은 `{code, message}`만, 스택·키 비노출(NF3002).
- 리포트 저장 시 클라이언트가 지표를 보내지 않고 서버가 세션의 draft를 읽는다 → 모델·클라이언트가 값을 조작/지어내지 못함.
- 화면: `index.html`(대화 + 단계/Tool 이력 타임라인, 재계획 구분 표시, 키 누락 안내), `reports.html`(목록·수정·삭제).

## 10. 대표 요청 추적 (설계 시험용)

1. "반도체 중 ROE 10% 넘고 PER 산업 평균 이하" → analyze(industry=반도체, ROE≥10, PER≤avg) → candidates → financials×N → judge(중앙값 계산) → 충족 K개 → respond(+공시 링크).
2. "ROE 25% 넘고 부채비율 30% 이하 은행" → 충족 0 → replan①시장 확대(cache로 기존 재무 재사용) → … → 3회 후 되묻기.
3. 한 종목 `NO_FINANCIALS` → 해당 종목 UNDECIDABLE, 나머지 진행.
4. "안녕" → analyze → respond (plan 건너뜀).
5. "ROE 높은 종목" → 임계값 없음 → 되묻기(Tool 0회).
6. (심화 F3022) "삼성전자 최근 실적 가이던스가 어땠어?" → analyze(filing_qa, target_stock=삼성전자) → plan(search_filing_text) → execute → respond(요약 + 근거 공시 블록). 색인이 없으면 `NO_INDEX` 고정 형식.

## 11. 구현 순서 (명세 §7 매핑)

1. **API 3종 실호출 검증 + LLM 키 확인** (⚠ 1일차 16시 기한. 실패 시 영화 도메인 전환) — 응답 필드가 §3·§5 입력을 채우는지, 빈 값 응답 형태, 호출 한도 기록
2. Tool 3개 + 공통 `ToolResult` (+ 개별 호출 테스트)
3. `constraints.py` 단위 테스트 (계산식은 이게 핵심 — 순수 함수라 테스트 쉬움)
4. LangGraph 흐름 + 이벤트 버스 + logger_agent
5. FastAPI + SQLite → 6. 프론트 2화면 → 7. (선택) F3022 RAG (D17)

## 12. 열린 항목 / 범위 밖

- ⚠ 산업 분류 출처, PER/ROE 직접 제공 여부, 재무 기준 분기와 기준일 매핑 규칙 — 1번 단계에서 확정
- 제외: 점수화 추천, 산업 전망, 관심 종목 비교, 스케줄러 수집, 세션 영속화, F3020/3021/3023
- 제외: Forward PER 등 예상 실적 기반 지표 — 컨센서스 데이터가 사용한 API에 없고, 추정은 F3008(확인 불가 값을 지어내지 않음) 위반. PER은 Trailing(확정 실적 기준)이며 응답에 기준 분기를 명시한다. 사용자가 요청하면 "제공 데이터로 계산 불가"로 안내한다.
