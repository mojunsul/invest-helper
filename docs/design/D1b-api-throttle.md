# D1b 런타임 외부 API 호출 간격(호스트별 최소 간격)

기준: D1 §1·§3(`Tool.run` 래퍼·`timeout_s`), D8 §3-2(`call_batch`, `FIN_CONCURRENCY`), D2b(구축 CLI 간격). 의사코드이며 구현 아님. **D0·DESIGN.md는 수정하지 않는다.** 새 `ErrorCode` 없음, Tool 입출력·래퍼 동작 불변. 변경은 base.py 내부 헬퍼 1개 추가 + Tool 3개의 `_fetch_*` 함수 첫 줄 1줄씩.

## 1. 배경

D2b에서 구축 CLI가 OpenDART를 ~600건/분으로 두드려 IP가 연결 수준에서 차단됐다. 런타임 Tool도 간격 없이 호출한다.

| 호출 지점 | 호스트 | 요청 수(1회 Tool 호출당) | 동시성 |
|---|---|---|---|
| `disclosures._fetch_list` | opendart.fss.or.kr | 1 | judge의 `call_batch`(PASS 종목마다, 세마포어 5) |
| `financials._fetch_fin` | apis.data.go.kr | 1~2 | execute/judge의 `call_batch`(후보마다, 세마포어 5) |
| `financials._fetch_price` | apis.data.go.kr | 0~7(휴장일 소급) | 위와 동일 |
| `stock_candidates._fetch_page` | apis.data.go.kr | 1~7 + 추가 페이지 | 순차 |

후보 기본 20(최대 100), 재계획(`REPLAN_MAX` ≤3)마다 반복, 세션이 여럿이면 합산된다. 세마포어 5만으로는 응답이 빠를 때 초당 수십 건이 나갈 수 있다.

목표: 프로세스 전역에서 **호스트별 요청 시작 간 최소 간격**을 강제해, `call_batch` 동시성·재계획·다중 세션이 겹쳐도 버스트가 나지 않게 한다.

## 2. 헬퍼 (base.py)

```python
import time
from urllib.parse import urlparse

HOST_INTERVAL_S = {                 # 임의 상수(구현 시 확인 항목). 표에 없는 호스트는 간격 없음
    "opendart.fss.or.kr": 0.2,      # 최대 ~300건/분 (D2b COMPANY_INTERVAL_S와 동일 수준)
    "apis.data.go.kr": 0.1,         # 최대 ~600건/분
}
_next_start: dict[str, float] = {}  # 호스트 -> 다음 요청이 시작해도 되는 monotonic 시각
_sleep = asyncio.sleep              # 테스트에서 이 별칭만 patch
_now = time.monotonic               # 테스트에서 patch 가능

async def throttle(url: str) -> None:
    """요청 직전 호출. 호스트별로 '슬롯'을 예약하고 그 시각까지 잠든다."""
    host = urlparse(url).hostname or ""
    gap = HOST_INTERVAL_S.get(host, 0)
    if gap <= 0:
        return
    now = _now()
    start = max(now, _next_start.get(host, 0.0))
    _next_start[host] = start + gap          # await 이전에 읽기·쓰기 완료 → 락 불필요
    if start > now:
        await _sleep(start - now)
```

- **락 없음**: 단일 이벤트 루프에서 `await` 없이 읽고-쓰므로 예약이 원자적이다. 대기 순서 = 예약 순서. `asyncio.Lock`을 쓰지 않으므로 루프 바인딩 문제(테스트의 `asyncio.run` 여러 번)도 없다. Tool의 HTTP 호출은 모두 이벤트 루프에서 일어난다(`to_thread`는 동기 identity 조회에만 사용).
- **취소**: 대기 중 `wait_for` 타임아웃으로 취소되면 예약 슬롯은 비게 된다(뒤 요청이 약간 더 늦을 뿐, 버스트는 생기지 않음). 되돌리지 않는다.
- 간격은 **요청 시작** 기준(응답 시간과 무관). 동시 요청이 있어도 시작 시각은 gap 이상 벌어진다.
- 전역 상태라 세션 간에도 공유된다 — IP 단위 차단을 막는 것이 목적이므로 의도된 동작.

## 3. 적용 지점 (Tool 3개)

각 `_fetch_*`의 첫 줄에 1줄 추가(인자는 그 함수가 쓰는 URL 상수):

```python
async def _fetch_list(corp_code, bgn_de, count):     # disclosures.py
    await throttle(URL)
    async with httpx.AsyncClient(timeout=10) as c: ...   # 이하 기존 그대로

async def _fetch_fin(crno, biz_year):                # financials.py
    await throttle(URL)
async def _fetch_price(basDt, stock_code):           # financials.py
    await throttle(sc.URL)
async def _fetch_page(basDt, page, rows):            # stock_candidates.py
    await throttle(URL)
```

- import: `from backend.tools.base import ..., throttle` (각 모듈 기존 import 줄에 추가).
- `call_batch`·`FIN_CONCURRENCY`·래퍼·캐시는 변경 없음. 캐시 적중은 `_fetch_*`를 거치지 않으므로 간격 대상이 아니다(정상).
- 기존 테스트는 `_fetch_*` 자체를 monkeypatch하므로 throttle을 거치지 않는다 → 기존 테스트 속도·단언 불변.

## 4. 타임아웃 분석

대기는 `Tool.run`의 `asyncio.wait_for(self.fn(inp), t)` 안에서 일어나므로 **Tool 타임아웃에 포함된다**. 단, 세마포어 5 때문에 한 세션의 배치에서 동시에 예약 경쟁하는 Tool 호출은 최대 5개이고, 각 Tool은 요청을 순차로 보내므로 호스트별로 동시에 걸린 예약은 최대 5개다. 따라서 **요청 1건의 대기 상한 ≈ 5 × gap**(후보 수 20~100과 무관 — 대기열은 세마포어 앞에 쌓이고, 세마포어 대기는 `wait_for` 밖이다).

| Tool | timeout | 요청 수 최악 | 대기 상한(1세션) | 판정 |
|---|---|---|---|---|
| search_disclosures | 10s(`TOOL_TIMEOUT_S`) | 1 | 5×0.2 = 1.0s | 여유 |
| get_financials | 40s | 2 + 7 = 9 | 9×5×0.1 = 4.5s | 여유 |
| find_stock_candidates | 30s | ~10(소급 7 + 페이지 ~3) | 순차, 다른 배치와 겹쳐도 10×5×0.1 = 5s | 여유 |

- 배치 전체 소요는 늘어난다: PASS 100건 공시 ≥ 100×0.2 = 20s, 후보 100건 재무(평균 2~3요청) ≥ 20~30s. 노드 전체 타임아웃은 없으므로(D8 §9) 실패가 아니라 지연이다. 기본 20건이면 공시 ≥4s, 재무 ≥4~6s.
- 트레이드오프: 동시 세션 N개면 대기 상한이 ×N. 공시(1요청, 10s)는 N≈10 이상에서 TIMEOUT 가능 — 과제 규모(단일 사용자)에선 무시, 구현 시 확인 항목에 남긴다.

## 5. 제외·재사용 결정

- **LLM(`backend/agent/llm.py`, GMS) 제외**: 요청당 수 회(analyze·plan·respond·재계획 next_step)로 순차 호출이고 동시 배치가 없으며 차단 이력도 없다. 호스트가 달라 표에 넣지 않으면 간격 0이므로 별도 처리 불필요. 필요해지면 `HOST_INTERVAL_S`에 호스트 1줄 추가 + `complete`에 `await throttle(url)` 1줄.
- **D2b(build CLI) 재사용 안 함**: build는 순차 1-루프에 `_sleep(min_interval_s)` 고정 대기로 이미 단순하고 테스트(`min_interval_s=0`, `_sleep` patch)도 갖춰져 있다. 바꾸면 이득 없이 테스트만 손대야 하므로 그대로 둔다. (build와 런타임 서버는 별도 프로세스라 전역 상태 공유도 안 됨.)

## 6. 테스트 (tests/test_d1.py 추가, 실호출·실대기 없음)

공통: `monkeypatch.setattr(base, "_next_start", {})`, `_sleep`을 인자 기록용 async 함수로, `_now`를 고정값(예: `lambda: 100.0`)으로 patch.

1. **간격 누적**: `HOST_INTERVAL_S`를 `{"h.test": 1.0}`로 patch → `asyncio.gather(throttle("https://h.test/a") ×3)` → 기록된 sleep 인자 `[1.0, 2.0]`(첫 호출은 대기 없음).
2. **시간 경과 시 대기 없음**: 1회 호출 후 `_now`를 `102.0`으로 바꿔 다시 호출 → 새 sleep 기록 없음.
3. **호스트 분리**: `{"a.test": 1.0, "b.test": 1.0}` → a, b 각 1회 → sleep 기록 0건. 표에 없는 호스트 → 기록 0건, `_next_start` 미변경.
4. **적용 지점**(테스트 파일 test_d3/test_d4/test_d5에 각 1개): 해당 모듈의 `throttle`을 `raise _Stop(url)`하는 가짜로 patch → `_fetch_list`/`_fetch_fin`/`_fetch_price`/`_fetch_page` 호출 시 `_Stop`이 나고 인자가 각 모듈의 URL(`d.URL`, `fin.URL`, `sc.URL`)이다 → HTTP 전에 throttle을 거침을 확인(네트워크 없음).
5. 기존 전체 테스트 통과(무수정).

## 7. 구현 시 확인 항목

- `HOST_INTERVAL_S` 값(0.2 / 0.1)은 임의. DART·data.go.kr 분당 한도 미확인. 대표 요청(후보 20, 공시 포함)을 실호출로 돌려 차단·`020`·`22` 없이 끝나는지, 전체 소요가 체감상 허용 범위인지 확인 후 조정.
- 실제 호스트명이 `urlparse(URL).hostname`과 일치하는지(`opendart.fss.or.kr`, `apis.data.go.kr`) — URL 상수에서 바로 나오므로 일치해야 정상.
- 다중 세션 동시 실행 시 공시 TIMEOUT 발생 여부(§4 트레이드오프). 문제되면 `search_disclosures`에 `timeout_s=30` 지정(D1 `@register` 인자, 계약 변경 아님).
- 차단이 다시 나면 DART 간격 상향 또는 judge의 공시 대상 PASS 상위 N건 제한(D8 §9 기존 항목)을 검토.

## 8. 상위 계약 영향

- **D0 변경 없음**, **DESIGN.md 변경 없음**. Tool 입출력·`ToolResult`·오류 매핑·`Tool.run` 동작 불변.
- **D1**: 계약 변경 없음. D1은 base.py에 내부 헬퍼 추가를 금지하지 않는다. 문서 보완 제안만: §1 끝에 "외부 HTTP 직전 `throttle(url)`로 호스트별 최소 간격(D1b)" 한 줄.
- D3/D4/D5: `_fetch_*` 내부 1줄 추가(동작 의미 불변, 지연만 추가). D8: 변경 없음(`FIN_CONCURRENCY` 그대로).
