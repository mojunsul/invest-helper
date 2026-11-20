# D9a 재계획 ③ limit_up 유효 판정 · limit 응답 문구 보정

상위: D0 §4·§6-12, DESIGN §4 재계획 규칙, D9. **D0 §6-12 변경은 사용자 승인됨**(아래 "상위 문서 수정"을 구현 단계에서 반영).

## 1. 문제

1. `next_step` ③은 `trigger=="no_pass"`이고 `candidate_limit(기본 20) < MAX_LIMIT(100)`이면 무조건 유효. 후보가 현재 상한에 잘렸는지 보지 않아, 업종 전체가 상한 미만이면 같은 후보로 재조회만 한다(rep3/rep2 실데이터: 2차전지 15개 → 20→100 재호출, 후보 동일).
2. rep3 응답: 시장 확대 후에도 조건 줄이 `시장 KOSPI`(conditions.market 원값 표시). DESIGN §4 "확대했을 때 응답에 그 사실을 명시" 위반.
3. rep3 LLM 요약: 재계획 2회(REPLAN_MAX=3)인데 "한도에 도달해". `facts`에 종료 사유가 없어 LLM이 추측함.

## 2. 변경

### 2-1. `next_step` ③ (`backend/agent/nodes/replan.py`)

```python
if step == 3 and trigger == "no_pass":
    lim = scope.get("candidate_limit") or DEFAULT_LIMIT
    if lim < MAX_LIMIT and len(state.get("candidates") or []) >= lim:
        return 3
```

- `candidates`는 라운드 합집합(D0 §4)이지만 검색 범위는 단조 확대이므로, 마지막 조회가 상한에 안 잘렸으면 합집합 = 마지막 조회 결과(< lim). 잘렸으면 마지막 조회만으로 lim개 → 합집합 ≥ lim. 따라서 `len(candidates) >= lim` = "현재 상한에 잘렸을 수 있음". 정확히 lim개(잘리지 않았지만 경계)는 유효로 취급(최대 1회 낭비, 허용).
- ①②, `_replan_or`, `decide_outcome`, plan(`search_scope` 재계산) 불변.

### 2-2. 조건 줄 시장 표시 (`respond.py` `_cond_line`)

`_cond_line(cond)` → `_cond_line(state)`(호출부 1곳, `_screen`). 시장 문구만 바꾼다:

```python
m = cond.get("market")
expanded = m and any(r["change"] == "market_expand" for r in state.get("replan_log") or [])
mk = f"{m} → 전체(확대)" if expanded else (m or "전체(KOSPI+KOSDAQ)")
```

- `conditions.market`은 보존(D0 원칙 그대로). 표시만 `replan_log`로 판단(plan이 `search_scope`를 `replan_log`로 재계산하는 것과 같은 원천).

### 2-3. limit 사유 구분 (`respond.py` `_screen`)

`status=="limit"`일 때 사유는 코드로 결정(D9 `_replan_or`의 fallback 조건과 동일):

```python
reason = "재계획 한도 도달" if r >= settings.REPLAN_MAX else "더 넓힐 검색 범위 없음"
```

- `facts`에 `"limit_reason": reason`, `"replan_max": settings.REPLAN_MAX` 추가(answered에는 넣지 않음). 숫자 검증 허용 집합이 facts JSON에서 오므로 "3회" 같은 표현도 통과.
- 템플릿: `‘{industry}’ 후보 {n}개 중 조건을 충족한 종목이 없습니다(재계획 {r}회, {reason}).`
- `SUMMARY_SYSTEM`에 한 구 추가: "종료 사유는 limit_reason 값 그대로 쓰라." (프롬프트 문구 외 LLM 경로 불변)
- `from backend.config import settings` import 추가.

생략: 응답 본문에 별도 "사유" 줄 추가 안 함(템플릿 문장에 포함되면 충분).

## 3. 상위 문서 수정 (사용자 승인됨, 구현 단계에서 반영)

- **D0 §6-12 ③** 현행: "③ `candidate_limit`가 D3 최대 상한 미만이면 유효(trigger=`no_candidates`일 때는 건너뛸 수 있음)."
  → 변경: "③ trigger=`no_pass`이고 현재 상한(`candidate_limit` 또는 D3 기본 상한) < D3 최대 상한이며 현재 `candidates` 수 ≥ 현재 상한(상한에 잘렸을 수 있음)일 때만 유효(trigger=`no_candidates`면 건너뜀)."
- **DESIGN §4 재계획 규칙 '확장 순서'** 괄호 예시: "(시장 미지정 ①, 유사 업종 없음 ② 등)" → "(시장 미지정 ①, 유사 업종 없음 ②, 후보가 현재 상한 미만 ③ 등)".
- **D9 §11 구현 시 확인 항목**: "③ `limit_up`은 D0 §12 문구대로 … 1회 소모될 수 있다(…)." 항목 → "③ `limit_up`은 후보 수가 현재 상한 이상일 때만 유효(D9a). 후보 수 == 상한(잘리지 않은 경계)이면 1회 낭비 가능(`MAX_LIMIT=100`·기본 20은 임의 상수)."로 교체.

## 4. 영향 (기존 테스트 갱신 필요)

limit_up 유효를 가정한 곳(grep `limit_up`·`next_step`·`재계획 2회`):

| 위치 | 현재 가정 | 갱신 |
|---|---|---|
| `tests/test_d9.py:112,114` | 후보 1개로 ③=3 | `S(candidates=20개, ...)`로 바꿔 3 유지 + 후보 1개면 None 단언 추가 |
| `tests/test_d9.py:126-136` (`test_replan_max[mx=3]`) | 기본 S 후보 1개라 ③ 무효 | S에 후보 20개(lim 이상) 부여 |
| `tests/test_d9.py:236-241` | replan_log 3건 표시 | 표시 테스트라 영향 없음(확인만) |
| `tests/test_d9.py:339` | `…(재계획 2회).` 정확 문자열 | `…(재계획 2회, 더 넓힐 검색 범위 없음).` |
| `tests/test_d9.py:444,456` | 후보 2개로 `[market_expand, similar_industry, limit_up]` / `[similar_industry, limit_up]` | limit_up 제거(replan_count 2 / 1), 호출 수 단언 조정 |
| `tests/test_d16_e2e.py:281-300` (req2/2b) | 은행 소수 후보로 limit_up 포함 | limit_up 제거, round 집합·`find_stock_candidates` 호출 수·캐시 단언 조정 |
| `tests/test_d13.py:225` | 이벤트 픽스처 문자열 | 영향 없음(직렬화 테스트) |

## 5. 테스트 관점 (신규)

- `next_step`: 후보 < 상한 → ③ 건너뜀(None), 후보 == 상한(20) → 3, `candidate_limit=MAX_LIMIT` → None, trigger=`no_candidates` → ③ 없음.
- `_cond_line`: `market="KOSPI"` + replan_log에 market_expand → `시장 KOSPI → 전체(확대)`; 미확대 → `시장 KOSPI`; 시장 미지정 → `전체(KOSPI+KOSDAQ)`.
- limit 사유: `replan_count == REPLAN_MAX` → 템플릿에 "재계획 한도 도달", 미만 → "더 넓힐 검색 범위 없음"; LLM mock이 받은 facts에 `limit_reason` 포함; answered facts에는 없음.
- e2e(mock): 후보 2개·시장 지정 → replan `[market_expand, similar_industry]` 후 limit, 응답에 "KOSPI → 전체(확대)"·"더 넓힐 검색 범위 없음".

## 6. 구현 시 확인 항목

- 이 변경 후 "상한 도달(REPLAN_MAX=3)" 실데이터 대표 시나리오는 **업종 후보 > 20개이면서 시장 지정·유사 업종이 있는 산업**(예: KOSPI 반도체 + 충족 불가 조건)이어야 한다. `docs/verification/representative-requests.md` 요청 3 재선정 필요(2차전지는 15개라 ③ 미적용 → 재계획 1회 후 "더 넓힐 검색 범위 없음").
- rep2(시장 미지정 2차전지)는 재계획 0회로 즉시 limit이 되는지 실호출로 확인.
- `SUMMARY_SYSTEM` 문구 추가 후 LLM이 limit_reason을 그대로 쓰는지 실호출 1회 확인(어겨도 숫자 검증만 통과하면 채택되므로 문구 정확성은 미보장 — 템플릿 대체 강제는 범위 밖). (필드명 노출은 D9b가 검증으로 차단)
