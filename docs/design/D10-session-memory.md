# D10 세션 메모리 (`backend/agent/memory.py`)

기준: DESIGN §7·§9, D0 §4(턴 간 유지 표)·§6-1/2/5/10, D7 §2(analyze 입력), D9 §2·§5-8·§9, 구현 코드(`state.py`, `graph.py:run_agent`, `nodes/analyze.py`·`plan.py`·`respond.py`). 의사코드이며 구현 아님. DESIGN·D0는 수정하지 않고 요청만 올린다. **새 AgentState 필드는 추가하지 않는다.**

## 1. 목적·범위

| 항목 | D10 소관 | 비소관 |
|---|---|---|
| 저장소 | 프로세스 메모리 dict(키=session_id), 세션별 `MemoryEntry` | DB·파일 영속화(없음, 재시작 시 소실 감수 — DESIGN §7) |
| 요청 시작 | 새 `AgentState`를 만들어 메모리로 채움(**`conditions`는 항상 주입**) | 요청 필드(`intent`, `plan` …)는 비워 둠(analyze 이후 노드가 채움) |
| 요청 종료 | 최종 state로 메모리 갱신(§4 표) | 갱신 값 계산(D7 conditions·intent, D9 status·summary·draft는 이미 state에 있음) |
| 하위 단위용 조회 | D12 draft 조회, D13 턴 실행·초기화 함수(§5) | HTTP·DB 저장(D12/D13) |

## 2. 저장 구조

```python
class MemoryEntry(TypedDict):
    conditions: Conditions                 # 기본 default_conditions()
    last_intent: Intent | None             # state.prev_intent로 주입
    last_status: Status | None             # state.last_status로 주입
    last_result_summary: dict | None       # {top_codes, pass_count} (D9 §5-8)
    report_draft: dict | None              # 종목코드 → draft (스키마 D12)

_store: OrderedDict[str, MemoryEntry]      # 모듈 전역. 접근은 threading.Lock 보호(FastAPI sync 라우트 대비)
MAX_SESSIONS = 1000                        # 임의 상수 — §8
```

- Tool 원본(`tool_cache`, `candidates`, `metrics` 등)은 저장하지 않는다. `report_draft`는 D9가 코드로 만든 값 복사본이라 DESIGN §7 "원본 비보관"과 충돌하지 않는다(D0 §4).
- 세션 ID 수명: 클라이언트(D14)가 정한 문자열을 그대로 키로 쓴다. 최초 사용 시 암묵 생성(회원가입 같은 별도 생성 API 없음), 서버 재시작·`clear_session`·LRU 퇴출로 소멸. TTL은 두지 않는다(단순화). 메모리 상한은 세션 수 `MAX_SESSIONS` 초과 시 가장 오래 쓰이지 않은 세션 퇴출(LRU; 읽기·쓰기 모두 사용으로 간주). 세션당 크기는 `report_draft`(충족 종목 ≤ 후보 상한 100)가 지배하며 별도 제한 없음.
- 입출력 모두 `copy.deepcopy`로 격리한다(노드가 state의 `conditions`를 변경해도 메모리가 오염되지 않게, 반대도 마찬가지).
- 빈 session_id 검증은 D13(입력 경계) 소관. D10은 `str`이면 받는다.

## 3. 요청 시작: `build_state(session_id, user_msg) -> AgentState`

```
mem = _get_or_default(session_id)            # 없으면 기본 MemoryEntry (저장은 하지 않음 — 갱신 시점에만 생성)
return {
  session_id, user_msg,
  conditions:          deepcopy(mem.conditions),      # 항상 존재 (첫 턴, lookup/report_save 첫 턴 포함)
  prev_intent:         mem.last_intent,
  last_status:         mem.last_status,
  last_result_summary: deepcopy(mem.last_result_summary),
  report_draft:        deepcopy(mem.report_draft),
}
```

- **`conditions` 주입은 필수 불변식**: `plan.py`가 `state["conditions"]`를 직접 인덱싱하므로(D9 구현 검증 전제), 메모리가 비어 있는 첫 턴·lookup/report_save 첫 턴·chitchat 첫 턴에서도 `default_conditions()`(현재 `nodes/analyze.py` 정의 재사용; 순환 import 방지가 필요하면 `state.py`로 이동은 구현 판단)가 들어간다. `respond.py`도 `state["conditions"]`를 직접 읽는다.
- 그 외 필드(`intent`, `tool_cache`, `errors`, `plan` …)는 **키를 만들지 않는다**. D0 §4 "읽는 쪽은 `state.get(k, 기본)`" 규칙이며, 현재 구현에서 직접 인덱싱하는 키는 `conditions`와 (해당 intent 경로에서 analyze가 채우는) `target_stock`·`metrics[code]`뿐이다(§9 확인 항목).
- `last_result_summary`·`report_draft`는 D9 respond가 "새 결과가 있을 때만" 반환하고, 반환이 없으면 주입된 이전 값이 최종 state에 그대로 남는다. 이 성질 때문에 §4의 갱신은 "최종 state 값을 그대로 저장"으로 충분하다(D9 `lookup` 병합도 주입된 이전 draft를 읽는다 — D9 §5-8).

## 4. 요청 종료: `update_memory(session_id, final_state) -> None`

D0 §6-5 그대로. `final_state`는 `run_agent` 반환 dict.

| 최종 `intent` | conditions | last_intent | last_status | last_result_summary / report_draft |
|---|---|---|---|---|
| `screen`, `refine` (asked·error·limit·answered 모두) | `final.conditions` 저장(asked라도 병합된 값, `pending_metrics` 포함) | `intent` | `final.status` | `final` 값 저장(D9가 안 바꿨으면 주입값 그대로) |
| `lookup`, `report_save` | **변경 없음**(analyze가 conditions를 반환하지 않아 최종 state의 `conditions`는 주입값과 같으므로 저장해도 무변화. 구현은 명시적으로 건드리지 않는다) | `intent` | `final.status` | `final` 값 저장(lookup answered는 D9가 병합한 draft) |
| `chitchat` | 불변 | **불변** | **불변** | 불변 — 함수는 아무것도 하지 않고 반환 |
| `None` (analyze 실패: LLM·산업 해석 예외로 intent 미설정) | 불변 | 불변 | 불변 | 불변 — 일시 오류로 "직전이 asked" 맥락을 잃지 않게(재시도 시 이어 입력 가능) |

- `final.status`가 None인 경우(방어): 갱신하지 않는다(그래프 정상 종료 시 respond가 항상 설정).
- 새 screen 시작만으로 `last_result_summary`/`report_draft`를 지우지 않는다(D0 §6-5). 새 `answered`면 D9가 draft를 통째 교체한다.
- 갱신은 쓰기 1회로 `MemoryEntry` 전체를 교체(부분 반영 중간 상태 없음). 이후 LRU 갱신·초과 시 퇴출.

**시나리오 보존 확인(D0 §6-5)**: screen answered → lookup → refine: lookup 단계에서 conditions 불변, `last_intent=lookup`·`last_status=answered`, refine은 `prev_intent=lookup`이지만 D7 규칙상 `intent=="refine"`이면 `prev_c` 병합(D7 `analyze.py:207`)이라 이전 conditions 유지. screen asked → lookup → refine: lookup이 `last_status`를 갱신하지만 conditions(`pending_metrics`)는 보존. chitchat 사이에 끼어도 모두 불변.

## 5. 하위 단위 인터페이스 (시그니처 확정)

모두 `backend/agent/memory.py`. 모두 동기 함수(락만 사용, await 없음), 예외를 던지지 않는다(미존재 세션은 기본값/None).

```python
def build_state(session_id: str, user_msg: str) -> AgentState: ...                       # §3
def update_memory(session_id: str, final_state: dict) -> None: ...                       # §4
async def run_turn(session_id: str, user_msg: str) -> dict:                              # D13 진입점
    # state = build_state(...); final = await run_agent(state); update_memory(...); return final
    # run_agent의 예외는 잡지 않고 전파한다(D13이 final(error) 처리, D9 §11). 예외 시 메모리는 갱신하지 않는다.
def get_report_draft(session_id: str, stock_code: str) -> dict | None: ...               # D12 진입점
def clear_session(session_id: str) -> None: ...                                          # 선택 사용(D13), 미존재 무시
def clear_all() -> None: ...                                                             # 테스트용
```

- **D13**: `POST /api/chat`은 `final = await memory.run_turn(session_id, message)`만 호출하고 `final`에서 `answer/status/verdicts/industry_avg/replan_log/errors/tool_cache`로 응답을 조립한다(D9 §9-4). `run_turn`이 `build_state`·`update_memory`를 소유하므로 D13이 메모리를 직접 만지지 않는다. `DELETE /api/logs/{sid}`가 메모리까지 지울지는 D13 결정(로그 삭제와 대화 맥락은 별개로 두는 것을 권장; 지우려면 `clear_session`).
- **D12**: `POST /api/reports`는 `memory.get_report_draft(session_id, stock_code)`로 draft 1건(깊은 복사본)을 받는다. 없으면 `None`(세션 없음·draft 없음·종목 불일치 모두 동일) → D12가 오류 응답(D9 report_save의 "먼저 해당 종목을 조회하세요"와 같은 의미). draft 스키마·검증은 D12 소관이며 D10은 `report_draft` 맵(`code → dict`)의 키 조회만 한다. 코드 정규화(6자리 문자열)는 호출 측.
- **D9 협약 충족 확인**: `last_result_summary`(screen/refine answered·limit에서만 respond가 반환 → 위 표에서 그대로 저장), `report_draft`(screen answered=통째 교체, lookup answered=병합 → 둘 다 주입된 이전 값 위에서 계산된 최종 값을 저장), error/asked/report_save/chitchat은 D9가 반환하지 않아 이전 값 유지.

## 6. 이벤트·로그

D10은 이벤트를 발행하지 않는다(요청 경계 이벤트는 analyze/respond 소관). 로깅은 `logging`만(`print` 금지): 세션 퇴출 시 `logger.info("session evicted")`(session_id·내용 미기록)·갱신 스킵 사유 정도. `user_msg`·conditions 값·draft 내용은 기록하지 않는다.

## 7. 테스트 관점 (LLM·Tool 불필요, `clear_all()`로 격리; `run_turn`은 `run_agent` monkeypatch)

- `build_state`: 빈 메모리(미지 세션)에서 `conditions == default_conditions()`·`prev_intent/last_status/last_result_summary/report_draft` None, 필수 키는 session_id·user_msg·conditions 5개뿐; 호출 후 반환 conditions를 변경해도 메모리 불변(deepcopy); 빈 세션 조회가 저장소에 항목을 만들지 않음.
- `update_memory` 표 전 행: screen answered/asked(병합된 conditions·`pending_metrics` 저장)/error/limit, refine, lookup(conditions 불변 + last_* 갱신), report_save, chitchat(전부 불변), `intent=None`(불변), `status=None`(스킵), summary/draft가 주입값 그대로일 때 유지·새 값일 때 교체, 시나리오 3종(screen answered→lookup→refine, screen asked→lookup→refine, 중간 chitchat 삽입)에서 `pending_metrics` 보존.
- 통합 성질: 첫 턴 lookup·report_save·chitchat 상태가 `plan.py`/`respond.py`의 `state["conditions"]`에서 KeyError 없이 진행(그래프 mock 포함 최소 1개 테스트: 초기 state만으로 `plan`을 호출).
- `run_turn`: `run_agent` 정상 시 갱신·반환값 동일, 예외 시 전파 + 메모리 미갱신, 연속 2턴에서 두 번째 `build_state`가 첫 턴 결과를 반영.
- `get_report_draft`: 존재/없음(세션·종목) 및 반환 복사본 변경이 메모리에 영향 없음.
- 용량: `MAX_SESSIONS` 초과 시 가장 오래 사용되지 않은 세션 퇴출, 읽기만 해도 최근 사용으로 갱신.

## 8. 구현 시 확인 항목

- 임의 상수 `MAX_SESSIONS=1000`(운영 규모 가정 없음).
- `default_conditions()`의 위치: 현재 `nodes/analyze.py`. `memory.py`가 이를 import하면 `analyze`가 끌어오는 `llm`/`resolve_industry`가 같이 import된다. 구현 시 순환·무거운 import가 문제면 `state.py`로 이동하고 analyze가 재export(D7 영향 최소화). 판단은 구현 에이전트.
- 같은 세션의 요청이 동시에 겹칠 때(마지막 쓰기가 이김, 이전 턴 결과를 못 읽을 수 있음)는 프론트가 한 번에 한 요청만 보낸다는 가정으로 허용. 필요하면 `run_turn`에 세션별 `asyncio.Lock`을 추가하는 정도로 해결(미결정, 구현 중 실호출로 확인).
- 직접 인덱싱 키 점검: 현재 `plan.py`·`respond.py`의 `state["conditions"]`는 §3으로 보장. `judge.py`/`plan.py`의 `state["target_stock"]`, `respond.py`의 `state["metrics"][code]`는 lookup/report_save 경로에서 analyze·judge가 먼저 채운다는 D7/D8 전제 — 구현 시 해당 경로 테스트로 확인.
- `report_draft` 크기(PASS 최대 100종목 × metrics·공시)와 deepcopy 비용은 실측 후 문제가 있으면 얕은 복사로 완화(draft는 읽기 후 교체만 하므로 불변 취급 가능).
- 최종 `intent`가 `analyze`에서 `fail()`로 끝난 경로(예: resolve_industry 예외)는 `intent` 키가 없어 `None`으로 취급되는지 실제 반환으로 확인.

## 9. 상위 계약 요청 (제안만)

1. **DESIGN §7 / D0 §4 표에 한 줄 보강(낮은 위험)**: "analyze 실패(`intent` 미설정)로 끝난 요청은 메모리를 갱신하지 않는다"를 D0 §6-5 메모리 갱신 규칙에 추가. 현행 문구는 intent별만 규정해 이 경우가 비어 있다. 이 문서 §4는 이 보강을 전제로 동작한다(보강이 없어도 D10 동작만 영향이므로 구현 차단 아님).
2. **DESIGN §7 저장소 문장 보강(문구)**: "세션 수 상한(LRU 퇴출)·TTL 없음, `memory.run_turn`이 요청 단위 state 생성·갱신을 소유"를 명시(현행은 "프로세스 메모리 dict"만). D0 §4 "초기 state에 `conditions`는 항상 존재" 문장 추가도 함께 제안(plan/respond 직접 인덱싱 전제를 계약으로 고정).
3. 그 외 D0·DESIGN 수정 요청 없음. D0 §6-10(PER 분포 역산 미지원)은 승인된 상태 그대로 유지하며 `last_result_summary` 확장은 하지 않는다.

## 변경 이력

- 1회차: 최초 설계.
