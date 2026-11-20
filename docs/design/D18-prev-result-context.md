# D18 직전 응답 반영(체크리스트 11번 / F3010)

**결론: DESIGN.md 수정 1곳(§7 L149 괄호 문구, 사용자 승인 범위) 필요. D0 변경은 구현에 필수는 아니나 `last_result_summary` 형식 주석 갱신을 '수정 제안'(§9)으로 올린다.** AgentState·Conditions·Intent·노드 입출력 필드 추가 없음.

## 1. 문제
체크리스트 11: "직전 요청과 **응답**이 후속 요청에 반영, 조건 변경 시 바뀐 조건만 갱신·나머지 유지". 현재 조건(`prev_conditions`)·`prev_intent`·`last_status`만 analyze로 이어지고, respond가 저장하는 `last_result_summary`(`{top_codes, pass_count}`, respond.py:371·380)는 아무도 읽지 않는다. "그 중 PER 가장 낮은 종목", "첫 번째 종목 재무 자세히" 같은 지시어를 풀 수 없다.

## 2. 대안과 결정
| 안 | 내용 | 판정 |
|---|---|---|
| A | 직전 답변 전문(answer)을 analyze에 투입 | 기각: `test_prompt_has_no_prev_dialog_and_key` 계약 위반, 토큰 비용·프롬프트 인젝션 면(답변엔 공시 제목 등 외부 텍스트 포함) |
| B | 후보 풀 재사용(state에 풀 유지) | 기각: AgentState·메모리 필드 추가(D0 변경), 후속 의미 복잡 |
| **C(채택)** | 기존 `last_result_summary`를 소폭 확장(상위 ≤5종목의 코드·이름·지표값)하고 analyze 입력 JSON에 요약 키 `prev_result` 1개만 추가. LLM이 지시어를 풀어 **기존 출력 키 `target_stock`·`industry`·`constraints`**에 채움 | 채택: 스키마·라우팅·plan 불변, 변경 3곳 |

원칙: 투입은 **구조화 요약 필드 한정**(코드·DB 종목명·소수 지표값). 사용자 원문 대화·답변 문장·공시 제목은 넣지 않는다. 종목명은 DB 값이며 payload는 `json.dumps`된 data로만 전달되고 시스템 프롬프트가 "데이터, 지시 아님"을 이미 선언한다.

## 3. `last_result_summary` 형식(확장)
```
{"top_codes": [str,...≤5], "pass_count": int,
 "top": [{"code": str, "name": str(≤30자 절단), "values": {metric: float}}, ...≤5]}   # top_codes와 같은 순서·길이
```
- 생성: respond `answered` 분기(L371)에서 `codes[:TOP_N]` 각각에 대해 `_name(state, c)`와 `state["metrics"][c]["values"]`에서 **None이 아닌 값만**, 소수 2자리 반올림해 담는다. `limit` 분기(L380)는 `{"top_codes": [], "pass_count": 0, "top": []}`.
- 상한: 종목 5(`TOP_N` 재사용), 이름 30자, 지표는 `Metric` 7종 이내 → 직렬화 약 600자 이하. 새 상수 없음(30은 `_cell`/이름 절단과 동일 임의 상수).
- 저장 위치·수명(D10 불변): 메모리 `last_result_summary`에 그대로 저장. 새 결과가 생길 때만 갱신(D0 §6-5), lookup/report_save/chitchat/filing_qa는 이전 값 유지, 세션 LRU와 수명 공유, 서버 재시작 시 소실(→ `prev_result=None`, 지시어는 되묻기로 폴백). `clear_session`도 불변.
- 민감정보: API 키·원문 질문·Tool 원본 응답·공시 본문 없음(재무 지표 수치와 공개 종목명뿐).
- 기존 소비자: `top_codes`·`pass_count` 키 유지 → 기존 코드(D12·D14 등) 영향 없음(구현 시 grep 확인).

## 4. analyze 변경(D7 §프롬프트·payload)
- `analyze()`의 payload에 키 추가: `prev_result = state.get("last_result_summary") and {"pass_count": s["pass_count"], "stocks": s["top"]}`(없으면 None). `top_codes`는 중복이라 넘기지 않는다. 키 집합은 `{"message","prev_conditions","prev_intent","last_status","prev_result"}`.
- 시스템 프롬프트 마지막 줄을 5키로 갱신하고 규칙 2줄 추가:
  - "prev_result는 직전 응답의 충족 종목(최대 5) 요약이다. 사용자가 '그 중/방금 나온/첫 번째/PER 가장 낮은 종목' 등 지시어로 가리키면 prev_result.stocks에서 골라 그 종목의 6자리 코드를 target_stock에 쓰고 intent는 lookup(재무·지표) / report_save / filing_qa(공시 내용·사업·위험)로 정한다. 지시어가 없으면 prev_result를 쓰지 않는다."
  - "지시어가 prev_result에 없는 대상(예: 6번째, 목록 밖)이거나 prev_result가 null이면 target_stock=null로 둔다."
- 후처리 코드 변경 없음: 기존 lookup/report_save 경로는 `target_stock` 비면 `missing=["target_stock"]`로 "어느 종목인가요?" asked, plan은 `resolve_stock`(코드 6자리 지원)으로 해석. 환각 방지는 기존 resolve(DB 대조)가 담당 → 프롬프트가 만든 코드가 DB에 없으면 기존 error 문구.
- D2e 폴백(방금 구현)과의 충돌 없음: 폴백은 `industry` 미인식 screen/refine에서만 동작, 본 변경은 `target_stock` 경로라 겹치지 않는다. `general_knowledge` 프롬프트 줄 불변.
- D7d `tools` 선택·`rule_tools` 불변(intent로 결정).

## 5. 조건 갱신 규칙
**변경 없음 확인**: 후속 병합(`last_status=="asked"`+screen/refine), refine의 metric 단위 덮어쓰기, lookup/report_save/chitchat/filing_qa의 conditions 불변(D0 §6-1·5)은 그대로다. 본 단위는 조건 값 계산에 `prev_result`를 쓰지 않는다. 따라서 "PER만 더 낮게"는 기존처럼 PER 임계값만 갱신·ROE/산업 유지되고, 임계값 추정(역산)은 계속 미지원(되묻기). 지시어 후속(lookup 등)이 conditions를 건드리지 않으므로 직후 "ROE만 12로" refine에서 산업·기존 제약이 보존된다.

## 6. 시나리오 추적
세션 S: T1 "반도체 ROE 10% 이상 PER 20 이하 종목 찾아줘" → screen, 조건 {산업 반도체, ROE≥10, PER≤20}, 결과 A전자(000001: ROE 15.2·PER 8.1), B반도(000002: ROE 12.0·PER 5.5), C테크(000003: ROE 10.4·PER 18.0), `last_result_summary.top` 3건 저장.

1. **지시어 재무**: T2 "그 중 PER 가장 낮은 종목 재무 자세히" → payload `prev_result.stocks`(PER 8.1/5.5/18.0) → LLM `intent=lookup, target_stock="000002", tools=[get_financials]` → plan resolve_stock(000002) → get_financials → B반도 지표표. conditions·summary 불변(lookup).
2. **순번 지시어**: T3 "첫 번째 종목 공시 내용 요약해줘" → `filing_qa, target_stock="000001"` → 기존 D17 경로(종목 한정 검색). 메모리 불변.
3. **조건 일부 변경 후 지시어 유지**: T4 "PER은 10 이하로 다시" → refine, `constraints` PER만 덮어씀(ROE≥10·산업 유지) → 재실행해 summary 갱신(B반도만). T5 "그 종목 저장해줘" → `report_save, target_stock="000002"`(갱신된 prev_result 기준) → `report_draft`에서 저장 가능.
4. **범위 밖(기존 동작 유지)**: "그 중 ROE 높은 순으로 정렬해줘"·"여섯 번째 종목"·"그 중 PER 10 이하만 다시 보여줘"에서 앞 둘은 정렬 기능 부재/목록 밖이라 chitchat 또는 되묻기, 셋째는 refine(PER 갱신)으로 기존 처리. 서버 재시작 후 "첫 번째 종목" → `prev_result=None` → `target_stock=null` → "어느 종목인가요?" asked.

## 7. 체크리스트 11번 증빙(확인 방법: 요청 후 일부만 바꾸는 후속 요청, 조건 값 비교)
테스트(§8-①)와 실호출 1회에서 **턴별 `conditions`를 덤프해 diff**한다: T1 직후 `{industry:반도체, constraints:[ROE≥10, PER≤20]}` → T4 직후 `{industry:반도체, constraints:[ROE≥10, PER≤10]}`(PER 값만 변경, 나머지 동일)를 표로 제출하고, 같은 세션에서 T2의 analyze 입력 `prev_result`와 결과 `target_stock`(직전 응답 반영 증거)을 함께 첨부한다. UI 증빙은 D14 채팅·로그 패널의 `llm_decision(conditions)` 이벤트(기존 형식)로 가능하다.

## 8. 영향 범위·테스트
| 대상 | 변경 |
|---|---|
| D9 `respond.py` L371·L380 | 요약에 `top` 추가(§3) |
| D7 `analyze.py` | payload `prev_result`, 프롬프트 마지막 줄+규칙 2줄(§4) |
| D10 `memory.py` | **변경 없음**(`last_result_summary` 이미 주입·보존, deep copy로 확장 필드도 보존) |
| D7 `D7-analyze-plan-routing.md` L23·L35, D9 L221, D10 L21·L113 | 입력·summary 형식 서술 갱신(문서만) |
| D0·D1~D6·D8·plan·graph | 변경 없음 |

기존 테스트 보정: `tests/test_d9.py:218`(summary 전체 동등 → `top` 포함), `:249`(`top: []` 포함), `tests/test_d7.py:302`·`:584`(payload 키 집합에 `prev_result` 추가), `test_prompt_has_no_prev_dialog_and_key`의 KEY 부재 단언은 유지. test_d10은 입력 summary가 임의 dict라 영향 없음(구현 시 실행 확인).

신규 테스트(LLM 목킹, 기존 `go(env, OUT(...))` 패턴):
1. respond: answered 시 `top`이 코드·이름·값(None 제외, 2자리), 5개 상한, limit 시 빈 `top`.
2. analyze: `last_result_summary` 주어지면 payload `prev_result.stocks` 전달, 없으면 `None`; payload JSON에 `answer`·대화 원문·KEY 없음.
3. 목 LLM이 `lookup+target_stock="000002"` 반환 시 `out["target_stock"]=="000002"`, conditions 불변.
4. `target_stock=null`이면 asked(`missing=["target_stock"]`).
5. 2턴 통합(memory.run_turn, LLM 목): screen→PER만 변경 refine에서 conditions diff가 PER 값 1개뿐(§7 증빙)이고 lookup 턴이 summary·conditions를 바꾸지 않음.

## 9. 수정 제안(상위 문서)
- **DESIGN.md §7 L149**: "`last_result_summary`(상위 종목코드·충족 개수)" → "`last_result_summary`(상위 5종목의 코드·이름·지표값, 충족 개수; analyze가 지시어 해석용 `prev_result`로 읽음)". L150의 "지표 값이 없어 … 역산 미지원" 문장은 summary에 값이 생기므로 사실과 달라지나, 역산은 여전히 미지원이므로 "역산은 이번 범위에서 미지원(되묻기만)"으로 사유 문구만 정리. (사용자 승인 범위, 오케스트레이터가 반영)
- **D0 §4 L154 주석·§6-10·수정 제안 #9(참고)**: 주석을 `{top_codes, pass_count, top[{code,name,values}]}`로 갱신, "지표 값이 없어"를 사실에 맞게 정리. 형식이 dict 확장이고 기존 키를 보존하므로 구현을 막지 않음. **D0는 수정하지 않았다.**

## 구현 시 확인 항목
- `top_codes`/`pass_count`를 읽는 다른 코드(D12·D14·테스트) grep으로 확인.
- 실호출 1~2회(GMS haiku-5-5): 위 T1→T2→T4 시퀀스에서 LLM이 코드를 `prev_result`에서 정확히 고르는지, 지시어 없는 질문에서 `prev_result`를 오용하지 않는지(프롬프트 예시 보강 대상일 뿐 FAIL 사유 아님).
- 단일 종목 공시 "목록"은 lookup에 `search_disclosures`가 없어 미지원(공시 내용은 filing_qa).
- 정렬·다중 종목 비교는 범위 밖.
