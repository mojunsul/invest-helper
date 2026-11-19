# D8 execute + judge (`backend/agent/nodes/execute.py`, `judge.py`)

기준: DESIGN §3·§4·§5·§6·§10, D0 §1·§3·§4·§6-8/9/11, D1 §4·§5, D2 §3·§8, D3~D7 의 위임/요청, 구현 코드(`state.py`, `constraints.py`, `nodes/plan.py`, `tools/base.py`·`registry.py`, `identity/resolve.py`, `events/bus.py`, `config.py`). 의사코드이며 구현 아님. DESIGN·D0·D1은 수정하지 않고 요청만 올린다. 새 AgentState 필드는 추가하지 않는다.

## 1. 목적·범위

| 노드 | D8 소관 | 비소관(경계) |
|---|---|---|
| execute | `plan[]` 실행, 후보별 `get_financials` 전개, `tool_cache` 조회/저장, `tool_call`/`tool_result` 발행, 종목명→코드 변환(`resolve_stock`), 후보 누적 병합, `errors` 기록 | plan 작성·`search_scope`(D7), `status` 설정·재계획 분기·카운터 갱신·`replan` 이벤트(D9) |
| judge | 캐시된 재무 → `MetricRow`, 산업 평균 재계산, `evaluate`/`failure_verdict` → `verdicts`, lookup의 지표 행 생성, **PASS 종목 공시 조회(§5)** | n_pass/n_dec 기반 규칙 1~6 평가, `status`, 재계획(D9), 표기 문구·반올림·"표본 부족"(D9) |

- D8 노드는 `status`를 **설정하지 않는다**(D0: 규칙 평가·status 설정은 D9). `replan_count`·`expansion_step`·`replan_log`도 건드리지 않는다.
- 두 노드 모두 LLM 미사용. 외부 호출은 `registry.call`(Tool)과 D2 `resolve_stock`(동기 DB, `asyncio.to_thread`)뿐.
- 그래프 흐름은 DESIGN §4 그대로(plan→execute→judge). 공시는 새 노드·재진입 없이 judge 끝에서 호출한다(§5, D0 §6-11 확정).

## 2. AgentState 입출력

| 노드 | 읽는 필드 | 쓰는 필드(변경 키만 반환) |
|---|---|---|
| execute | `session_id, intent, conditions, plan, target_stock, tool_cache, candidates, errors` | `tool_cache, candidates, errors`, `target_stock`(§3-4: 해석된 6자리 코드로 덮어씀. D0가 "코드 또는 원문"을 허용) |
| judge | `session_id, intent, conditions, candidates, tool_cache, errors, target_stock` | `metrics, verdicts, industry_avg, disclosures, tool_cache, errors` |

- 인터페이스: `async def execute(state) -> dict`, `async def judge(state) -> dict`. 둘 다 `stage_start`/`stage_end`를 발행하고(재계획 때 재실행마다 재발행, D0 §3), 예외를 던지지 않는다. 어떤 상태에서 호출돼도 안전해야 한다(할 일이 없으면 no-op) — D9가 execute→judge 또는 execute→respond 어느 쪽으로 라우팅해도 된다.
- **재계획 재진입 의미**: execute/judge는 호출 때마다 현재 `candidates`(누적) 기준으로 `metrics/verdicts/industry_avg`를 **처음부터 재계산해 통째로 교체**한다(D0 규칙 "누적된 현재 candidates 전체 기준"). `tool_cache`·`candidates`·`errors`만 호출 간 누적된다.
- `errors` 의미: **(tool, stock_code)별 최신 상태만 유지**한다. execute/judge가 어떤 (tool, stock_code)를 이번에 호출·재평가하면 그 키의 기존 레코드를 먼저 제거하고 실패 시에만 새로 추가한다(재계획 중복·낡은 오류 방지). analyze의 `tool="llm"` 등 다른 레코드는 건드리지 않는다.
- 헬퍼(모듈 공개, D9가 규칙 1·2 판정에 재사용 가능):
  ```python
  def terminal_error(errors) -> ErrorCode | None
  # MISSING_API_KEY 레코드가 하나라도 있으면 그 코드(D0 규칙 1).
  # 없고 tool=="find_stock_candidates" 레코드의 code != "NO_RESULT"이면 그 코드(규칙 2). 그 외 None.
  ```

## 3. execute

### 3-1. 공통 호출 헬퍼 `_call(cache, sid, name, raw) -> ToolResult` (D1 §5 흐름 그대로)

```
key = registry.cache_key_for(name, raw)            # None이면 캐시 우회(검증 실패 등)
publish tool_call(tool=name, input=raw, cache_hit=key in cache)
if key in cache: r = ToolResult.model_validate(cache[key]); dur = 0
else: t0; r = await registry.call(name, raw); dur = ms; if key and is_cacheable(r): cache[key] = r.model_dump()
publish tool_result(tool, ok, error_code, duration_ms=dur, summary)
```
- `summary`는 요약만: candidates "후보 N건, 기준일 X"(`meta.actual_basis`), financials "FY2025, 추정=예/아니오", disclosures "공시 N건", 실패는 `error.code`(+`error.message`, 이미 마스킹됨). 원천값·제목·URL·키는 넣지 않는다(D1 §3 점검표, D5).
- 캐시 규칙은 D0 §1/D1 그대로: ok와 `NO_FINANCIALS`/`NO_RESULT`만 저장(`is_cacheable`), 그 외 `ok=False`는 미저장이라 재계획 때 재호출된다. `tool_cache` 사본(`dict(state.tool_cache)`)을 갱신해 반환한다.
- `registry.call`은 예외를 던지지 않으므로(D1) 호출부에 try/except 없음. `CancelledError`는 전파.
- 이벤트·`ErrorRecord.message`는 `ToolError.message`만 사용(원문 없음).

### 3-2. screen/refine

1. `plan`의 step을 순서대로 실행한다(screen/refine은 D7이 만든 `find_stock_candidates` 1개). **fatal(`MISSING_API_KEY`) 또는 후보 조회 실패(어떤 코드든) 시 나머지 실행을 중단**한다(후속이 후보에 의존).
2. 후보 조회 결과:
   - ok → `candidates`에 **합집합 병합**: 기존 행 유지, 새 `code`만 뒤에 추가(재계획의 확장은 후보를 넓히는 방향, D0). 행은 D3 data 그대로.
   - `NO_RESULT` → `candidates` 변경 없음, `errors`에 `ErrorRecord(tool="find_stock_candidates", stock_code=None, code="NO_RESULT", ...)`.
   - 그 외 실패 → 같은 형식으로 `errors` 기록(재계획 없이 error는 D9 규칙 2).
   - 이번 호출 전에 기존 `find_stock_candidates` 레코드를 제거(최신 상태 유지). D9는 이 레코드로 규칙 2·3을 판별한다: 레코드 code≠`NO_RESULT` → 규칙 2, `NO_RESULT`이고 `candidates==[]` → 규칙 3(`terminal_error`와 `candidates`만으로 판정 가능).
3. **후보별 `get_financials` 전개**(D7 §5-3): 후보가 있고 `terminal_error`가 없으면 **모든 후보 `code`**에 대해 `{"stock_code": code}`(+`conditions.base_date`가 있으면 `"base_date"`)를 호출한다. 키 생략 규칙은 D7과 같다(None 키 생략 → 캐시 키 동일, D4: `actual_basis`는 넘기지 않음). 입력 구성은 judge가 재사용하도록 헬퍼 `fin_input(code, conditions) -> dict` 한 곳에 둔다.
   - **동시성(단순)**: `asyncio.gather` + `asyncio.Semaphore(FIN_CONCURRENCY)`. 결과는 후보 순서대로 처리한다. 상수 값은 임의(§9).
   - **fatal**: 한 결과가 `MISSING_API_KEY`면 `errors`에 1건 기록하고, 아직 시작 안 한 호출은 시작하지 않는다(세마포어 획득 후 플래그 확인). 이미 진행 중인 호출은 끝까지 기다린다.
   - **부분 실패**: 그 외 `ok=False`는 종목 단위로 `errors`에 `ErrorRecord(tool="get_financials", stock_code=code, code, message)`만 기록하고 **나머지 종목을 계속 진행**한다(DESIGN §10-3). 재시도 없음.
   - 판정·지표 계산은 하지 않는다(judge).

### 3-3. lookup

- `plan`에 `get_financials` step이 있으면(D7: 변환 성공 시) 그것을 실행하고 `target_stock`을 그 step의 `stock_code`로 덮어쓴다. step이 없으면(plan `[]`) §3-4로 `target_stock`을 재해석해 실패 사유를 `errors`에 남긴다(D0 §6-8). 변환이 이 경로에서 성공하는 일은 없지만(plan이 이미 시도) 방어적으로 성공하면 `get_financials`를 한 번 실행한다.
- 결과가 `ok=False`면(NO_FINANCIALS 포함) `errors`에 `(get_financials, code)` 기록. lookup 종료 status(`error`/`answered`)는 D9(D0 §6-8). **lookup에서는 `search_disclosures`를 호출하지 않는다**(§6, D7 요청 3 미반영 상태: 저장된 `want_disclosures`는 이전 screen 값일 수 있음).
- lookup은 후보 행이 없으므로 `mkt_cap` 보충을 하지 않는다(D6: PER/PBR/PSR은 `NO_MARKET_CAP`).

### 3-4. 종목명→코드 변환 `_resolve_target(state)` (D0 §6-8/9, D7 §5-3)

- 대상: `intent=="report_save"`(plan `[]`이어도 항상) 및 lookup의 plan `[]` 경우. `target_stock`이 None/빈 값이면 `ErrorRecord(tool="resolve_stock", stock_code=None, code="INVALID_INPUT", message=MSG["INVALID_INPUT"])`(analyze가 asked로 막으므로 방어).
- `m = await asyncio.to_thread(resolve_stock, state["target_stock"])`:

| 결과 | 동작 |
|---|---|
| `status=="ok"` | `target_stock = m.matches[0].stock_code`로 덮어씀(D9가 `report_draft`와 코드로 비교). 오류 없음 |
| `"none"` | `ErrorRecord(tool="resolve_stock", stock_code=None, code="NO_RESULT", message="일치하는 종목이 없습니다.")` |
| `"multiple"` | `code="INVALID_INPUT"`, message = "종목명이 여러 종목과 일치합니다: a, b, c"(`matches` 이름 최대 5개 + 총수가 많으면 "외 N개"; 캐시 데이터이며 외부 원문 아님) |
| `IdentityError` | `ErrorRecord(tool="resolve_stock", stock_code=None, code=e.code, message=e.message)` (D2 §3: 요청 경로에서 `MISSING_API_KEY`는 나오지 않음) |

- 이 레코드를 D9가 D0 §6-8/9대로 `status=error`로 처리하고 문구를 만든다(none=`NO_RESULT`, multiple=`INVALID_INPUT`으로 구분 가능). `resolve_stock`은 Tool이 아니므로 `tool_call`/`tool_result` 이벤트는 발행하지 않는다.
- 표시용 종목명은 D8가 별도 저장하지 않는다. D9가 필요하면 `resolve_stock(code)`를 다시 호출한다(D9 위임).

### 3-5. report_save / chitchat

- report_save: §3-4만 수행(Tool 호출 없음). 저장 가능 여부·draft 일치 판정은 D9/D12. chitchat은 라우팅상 도달하지 않으나 도달해도 no-op.

## 4. judge

### 4-1. screen/refine

```
rows = {}; verdicts = {}
for c in candidates:                       # 누적 후보 전체
    code = c["code"]
    r = cache.get(cache_key_for("get_financials", fin_input(code, conditions)))   # ok인 ToolResult만 유효
    if r is ok:
        raw = {7키: data.get(...)}                                    # revenue,op_profit,net_income,assets,equity,debt_ratio,mkt_cap
        if raw["mkt_cap"] is None: raw["mkt_cap"] = c["mkt_cap"]      # D6 위임: 후보 행으로 보충(D3가 양의 정수 보장)
        rows[code] = compute_metric_row(raw, fiscal_period=data["fiscal_period"],
                                        actual_basis=meta.actual_basis, estimated=meta.estimated)
    else:
        e = errors에서 (get_financials, code) 레코드
        e 없음 → 판정 안 함(fatal 중단으로 미호출된 종목)
        e.code == "NO_FINANCIALS" → verdicts[code] = failure_verdict("NO_FINANCIALS")
        e.code == "MISSING_API_KEY" → 판정 안 함(fatal, D9 규칙 1이 error 처리)
        그 외(API_ERROR/RATE_LIMIT/TIMEOUT/INVALID_INPUT/기타) → failure_verdict("FETCH_FAILED")
avg = {"PER": industry_avg_per(rows)}                                   # 성공 종목 누적 rows 기준 매 호출 재계산
for code, row in rows.items(): v = evaluate(row, conditions["constraints"], avg); if v: verdicts[code] = v
return {metrics: rows, verdicts, industry_avg: avg, ...}
```
- 데이터 운반은 `tool_cache`(성공)와 `errors`(실패)뿐이다. 새 필드 없음.
- `constraints==[]`면 `evaluate`가 `None`이라 verdict를 만들지 않는다(D0: judge에 도달하지 않음, 방어).
- `industry_avg`는 ref 사용 여부와 무관하게 항상 계산해 저장한다(표 표기·"표본 부족"은 D9, `is_sample_short`).
- 재계획 확장으로 후보·rows가 늘면 평균·모든 verdict가 이번 호출에서 다시 계산된다(이전 PASS가 FAIL로 바뀔 수 있음 — D0 "합집합 재평가"의 귀결).

### 4-2. lookup

- `target_stock` 코드의 캐시된 `get_financials`가 ok면 `compute_metric_row`로 `metrics[code]`만 만든다(`mkt_cap` 보충 없음, 후보 행 없음). `verdicts`·`industry_avg` 미설정. 실패면 `metrics` 비움(D9가 `errors`로 error 처리).

### 4-3. 그 외 intent

- report_save/chitchat: no-op(스테이지 이벤트 외 변경 없음).

## 5. 공시 호출 (D0 §6-11 확정)

- **위치: judge 끝**(새 노드·execute 재진입·respond 선행 호출 없음 — 그래프 변경이 없어 DESIGN §4 다이어그램이 그대로 유지된다).
- **조건**(모두 만족): `intent in {screen, refine}` and `conditions.want_disclosures` and PASS 종목 ≥ 1 and `terminal_error(errors) is None`. PASS가 없으면 재계획/limit 경로이므로 호출하지 않아 라운드마다 공시를 부르지 않는다(공시는 사실상 최종 라운드에서만 호출됨). lookup은 §3-3대로 제외.
- **호출**: PASS 종목(후보 순서)마다 `{"stock_code": code}`(`limit`은 기본값 그대로 — 같은 키로 캐시 일관, D5). `_call` 재사용, §3-2와 같은 세마포어 동시성·fatal 중단.
- **저장·표기 규칙**(D5 요청 2 확정):
  - ok → `disclosures[code] = data`.
  - `NO_RESULT` → `disclosures`에 키를 만들지 않고 `errors`에도 기록하지 않는다. D9는 "want_disclosures ∧ PASS ∧ 키 없음 ∧ `(search_disclosures, code)` 오류 레코드 없음"을 **"공시 없음"**으로 표기한다.
  - 그 외 실패(API_ERROR/RATE_LIMIT/TIMEOUT/INVALID_INPUT) → `ErrorRecord(tool="search_disclosures", stock_code=code, ...)`. D9는 해당 종목만 **"공시 조회 실패"**로 표기하며 **이 오류로 `status`를 올리지 않는다**(판정에 영향 없음).
  - `MISSING_API_KEY`(DART 키) → fatal: 기록 1건 후 중단. D0 규칙 1에 따라 D9가 `status=error`(D0 "어느 Tool이든").
- 공시 `title`은 외부 텍스트다. D8은 저장만 하며 `summary`·로그에 넣지 않는다(LLM 프롬프트 처리는 D9, D5 요청 1).

## 6. 이벤트·로그 연계 (D0 §3, `bus.publish(session_id, type, payload_model)`)

| 시점 | 이벤트 |
|---|---|
| execute/judge 시작·종료 | `StageStart/StageEnd(stage="execute"/"judge")`(재계획마다 재발행). `StageEnd`의 `intent/reason`은 비움 |
| Tool 호출마다(후보 조회·재무·공시) | `ToolCall(tool, input, cache_hit)` → `ToolResult(tool, ok, error_code, duration_ms, summary)`. 캐시 적중은 `duration_ms=0`. 후보 100개면 이벤트 약 200건(허용) |
| 종목 변환(`resolve_stock`) | 이벤트 없음(Tool 아님, 결과는 `errors`) |

- `replan`/`final`은 D9. `llm_decision`은 사용하지 않는다.
- 로깅: `logging`만 사용(`print` 금지). 건수·코드·종목코드만 남기고 `mask()` 통과 텍스트만 사용. 데이터 원문·제목·URL 금지.
- 동시 호출 중 이벤트 `seq`는 버스가 락으로 부여(D0)하므로 순서 보장은 "버스 도착 순"이다(후보 간 순서는 비결정적, §9).

## 7. 에러·실패 처리 요약

| 상황 | D8 동작 | 이후(D0 규칙) |
|---|---|---|
| 어느 Tool이든 `MISSING_API_KEY` | 기록 1건, 미시작 호출 중단 | D9: 규칙 1 `status=error` |
| 후보 조회 `NO_RESULT` | 레코드 기록, `candidates` 불변, 재무 전개 없음 | D9: 규칙 3 |
| 후보 조회 그 외 실패 | 레코드 기록, 전개 없음 | D9: 규칙 2 `status=error` |
| 종목 `NO_FINANCIALS`(캐시됨) | `errors` + judge가 `failure_verdict("NO_FINANCIALS")` | 나머지 진행 |
| 종목 `API_ERROR/RATE_LIMIT/TIMEOUT/INVALID_INPUT`(미캐시) | `errors` + `failure_verdict("FETCH_FAILED")`, 재계획 시 재호출 | 재계획 트리거 아님 |
| 공시 `NO_RESULT` / 그 외 실패 | §5 | "공시 없음" / "공시 조회 실패"(status 불변) |
| 종목명 변환 none/multiple/IdentityError | §3-4 `errors` | D9: `status=error`(D0 §6-8/9) |

## 8. D9로 넘기는 입력 요약 (재계획 필요 여부 판단용)

| D9가 필요한 값 | 출처 |
|---|---|
| n_pass / n_dec (규칙 4~6) | `verdicts`의 PASS 수 / PASS+FAIL 수 (누적 후보 전체 기준) |
| 규칙 1·2 | `terminal_error(errors)` |
| 규칙 3 | `candidates == []` ∧ `find_stock_candidates` 레코드(`NO_RESULT`) |
| 규칙 4 후 사유 요약 | `verdicts[*].reasons`, `errors` |
| 재계획 단계 효과 판정(§12) | `search_scope`(D7), `similar_industries`(D2), `MAX_LIMIT`(D3) — D8 무관 |
| 표 표기 | `metrics`, `industry_avg`(`is_sample_short`는 D9), `disclosures`+`errors`(§5) |

- 재계획 시 D9가 `replan_log`를 추가하고 plan→execute→judge를 재진입시키면, D8은 캐시(`tool_cache`)로 기존 재무를 재호출하지 않고 새 후보분만 호출한다(DESIGN §10-2).

## 9. 구현 시 확인 항목

- 임의 상수: `FIN_CONCURRENCY`(예: 5; 금융위 API 한도·`TOOL_TIMEOUT_S` 합산 소요를 실호출로 확인해 조정), 공시 호출 수 상한 없음(PASS 100건이면 공시 100회 — 실호출 한도 확인, 필요하면 PASS 상위 N건 제한을 D9와 상의).
- 동시성 엣지: 동시 호출 중 `tool_call/tool_result` 이벤트 순서·`tool_cache` 갱신(단일 이벤트 루프라 dict 갱신은 안전), fatal 이후 진행 중 호출의 결과 처리, 한 라운드 전체 소요 시간(후보 수 × get_financials 호출 수; 노드 전체 타임아웃은 두지 않음).
- `ToolResult.model_validate(cache[key])` 왕복 시 `meta`/`error` 복원 확인.
- judge가 `fin_input`으로 캐시 키를 재구성하므로 `registry.cache_key_for`가 None인 경우(검증 실패)는 "캐시 없음 + 오류 레코드"로 처리됨을 테스트.
- 후보 행 `mkt_cap`(D3 basDt 시점)과 재무 `mkt_cap` 기준일 불일치(D4 §8)는 의도된 단순화.
- 재계획 확장 시 PASS가 바뀌는 현상(§4-1), 한 종목이 `errors`에서 갱신되는 순서, `multiple` 종목명 메시지 길이.
- `FETCH_FAILED` 변환 대상 외 코드(`NO_INDEX` 등 방어 분기)는 일괄 `FETCH_FAILED`.
- 공시 의도를 lookup에서 지원하려면 D7 요청 3(요청 단위 필드)이 선행되어야 한다(현재 미지원).

## 10. 테스트 관점 (외부 호출 mock: `registry.call`/`registry.cache_key_for`/`resolve_stock` monkeypatch, `bus.subscribe`로 이벤트 수집)

- execute: 대표 요청 1·2·3(§11), 캐시 적중 시 재호출 없음·`cache_hit=True`/`duration_ms=0` 이벤트, 비캐시 실패 재호출·`NO_FINANCIALS` 캐시, 후보 합집합 병합(순서·중복), 후보 `NO_RESULT`/API_ERROR 시 재무 호출 0회·레코드 내용, fatal 시 미시작 호출 중단·레코드 1건, 부분 실패 후 나머지 진행, `errors` 최신 상태 유지(재호출 성공 시 제거), base_date 키 생략 동일 캐시 키, lookup 단일 호출·plan `[]` 재해석 오류, report_save 변환 ok/none/multiple/IdentityError·`target_stock` 덮어쓰기, 이벤트 payload에 원천값·키 없음, 동시성에서도 `candidates`·`errors` 순서가 결정적.
- judge: 7키 `raw` 추출·`mkt_cap` 보충(None일 때만), `failure_verdict` 매핑 전 코드, 후보 누적 후 `industry_avg` 재계산·verdict 재평가, 미호출(fatal) 종목 verdict 없음, `constraints==[]`, lookup `metrics`만 생성, 공시: 조건 4가지 각각 off일 때 호출 0회, PASS만 호출, ok/`NO_RESULT`(키·오류 모두 없음)/API_ERROR(레코드, 키 없음)/fatal 처리, lookup에서 `want_disclosures=True`여도 호출 없음.

## 11. 대표 요청 추적 (DESIGN §10 중 execute/judge 구간)

| # | 요청 | execute | judge |
|---|---|---|---|
| 1 | 반도체 ROE≥10, PER≤산업평균 | 후보 조회 → 후보별 `get_financials`(동시 N) | 7키→MetricRow, `industry_avg` 중앙값, evaluate→PASS K; `want_disclosures`면 PASS에 공시(+링크) |
| 2 | 은행 ROE≥25, 부채비율≤30 | 라운드1 후보+재무 → (judge 충족 0) → D9 재계획 → plan이 확장된 scope로 재생성 → execute: 새 후보 병합, **기존 재무는 `tool_cache` 적중**, 신규분만 호출 | 누적 rows로 `industry_avg`·verdict 전체 재계산 → 3회 후 limit(D9) |
| 3 | 한 종목 `NO_FINANCIALS` | 해당 종목 레코드 기록, 나머지 계속 | 그 종목 UNDECIDABLE(`NO_FINANCIALS`), 나머지 정상 |
| 4 | "안녕" | 미진입(D7 라우팅) | 미진입 |
| 5 | "ROE 높은 종목" | 미진입(analyze asked, Tool 0회) | 미진입 |

## 12. 이전 단위 위임 사항의 처리 결과

| 출처 | 위임/요청 | D8 처리 |
|---|---|---|
| D0 §1 | 캐시 키·`is_cacheable`·NO_FINANCIALS/NO_RESULT만 캐시 | §3-1 |
| D0 §3 | tool_call/tool_result 규칙(cache_hit, duration 0), 재실행 시 start/end 재발행 | §3-1, §6 |
| D0 §4 규칙 1·2·3 입력 | fatal·후보 실패 구분 가능한 기록 | §2 `terminal_error`, §3-2, §8 (평가 자체는 D9) |
| D0 §4 `PlanStep.input` | plan은 스키마 만족 step만, 후보별 전개는 D8 | **준수**: §3-2 전개 step도 FinInput/DisclosureInput 스키마 만족, D0 수정 없음 |
| D0 §6-8 lookup | 변환·실행 D8, 실패 status/문구 D9 | §3-3·3-4 (`errors` 규약) |
| D0 §6-9 report_save | 종목명→코드 변환 D8(plan `[]`여도) | §3-4 |
| D0 §6-11 / D5 요청 2 | 공시 호출 노드·시점·NO_RESULT 외 실패 표기 | §5: judge 끝, PASS만, 상태 불변 표기 규칙 확정 |
| D1 §5 | 캐시 조회·저장·이벤트·duration·summary 문구 | §3-1, §6 |
| D1 §6 위임 | API_ERROR/RATE_LIMIT/TIMEOUT/INVALID_INPUT→`FETCH_FAILED` | §4-1 |
| D2 §3·§8 | `IdentityError` catch→`ErrorRecord`, `to_thread` | §3-4 |
| D3 요청 3·5 | 일부 미구축 표기·산업 표시명 | D9 소관(D8 비관여) |
| D3 §8 | ①~③ 재계획 인자는 plan이 scope로 반영, D8은 plan 그대로 실행 | §3-2 |
| D4 §6 | 후보마다 `base_date=conditions.base_date`, `actual_basis` 미전달, 같은 입력 캐시 | §3-2 |
| D4/D6 | 시총 None은 후보 행으로 보충(screen), lookup은 미보충 | §4-1, §4-2 |
| D4 §5 | lookup `NO_FINANCIALS`→error, screen은 종목 단위 UNDECIDABLE | §3-3, §7 |
| D5 §7 | `limit` 고정값, `NO_RESULT`면 `disclosures` 키 없음, summary에 제목 금지 | §5 |
| D6 §1·§7 | `raw` 7키, `failure_verdict` 매핑, 누적 rows 기준 `industry_avg` 재계산, `MetricRow` 생성 | §4 |
| D7 §5-3 | 후보별 financials 항상, disclosures는 `want_disclosures`일 때만(전개는 D8), report_save/lookup 변환 | §3-2·3-3·3-4, §5 |
| D7 요청 3 | lookup 공시 의도 저장 필드 | 미반영 상태이므로 lookup 공시 **미지원**(§3-3) |
| D7 요청 7 | D8 위임 명시 | 위 항목들로 처리 |

## 13. 상위 계약에 대한 요청 (제안만)

1. **DESIGN §4 노드 표·본문 갱신**: 공시 호출은 judge 끝에서 수행(흐름 변경 없음). judge 행 설명에 "통과 종목 공시 조회(want_disclosures)" 추가하고 "호출 노드·시점은 D8/D9에서 확정" 문구를 확정 내용으로 교체. 대안(judge 순수 유지 + judge→execute 재진입)은 그래프 분기와 단계 표식 필드가 필요해 채택하지 않음. *(사용자 판단 필요: 위 방식 승인 여부, 낮은 위험)*
2. **D0 §6-11 확정 기록**: §5 규칙(PASS만, judge 끝, NO_RESULT=키 없음·오류 없음, 그 외 실패는 종목별 오류 레코드·status 불변, MISSING_API_KEY는 규칙 1).
3. **D0 §4 `errors`·`target_stock` 주석**: (a) `errors`는 (tool, stock_code)별 최신 상태, 후보 실패는 `tool="find_stock_candidates"` 레코드로 규칙 2·3 판별. (b) `resolve_stock` 실패 레코드 규약(none=`NO_RESULT`, multiple=`INVALID_INPUT`, IdentityError는 그 코드). (c) execute가 해석한 6자리 코드를 `target_stock`에 덮어쓴다.
4. **D9 협약**: D8는 `status`·재계획 카운터를 건드리지 않으며 D9가 §8 표의 입력으로 규칙 1~6을 평가한다. 표시용 종목명은 D9가 `resolve_stock(code)`로 조회.

## 변경 이력

- 1회차: 최초 설계.
