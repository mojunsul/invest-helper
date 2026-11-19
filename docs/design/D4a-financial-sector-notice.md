# D4a 금융업 재무 미지원 전용 안내 (A안)

기준: D0 §1·§4·§12, D1 §3, D4 §3-5·§8(H7), D6, D8(judge), D9(respond), DESIGN §10 시나리오 2. 의사코드이며 구현 아님. **D0·D1·DESIGN.md·기존 설계 문서는 수정하지 않는다.** 새 ErrorCode·ReasonCode 없음.

## 1. 배경(실호출 확정 사실)

`getSummFinaStat_V2`는 은행(induty_code 접두 6412, 17사)에 HTTP 200·`resultCode 00`·`totalCount 0`을 준다(우리은행·카카오뱅크·기업은행 × 2025/2024 모두 0건, 같은 경로 삼성전자는 정상). 따라서 D4는 `NO_FINANCIALS`를 내고(D4 §4-3), 화면에는 "후보 4개 모두 판단불가: 재무정보 없음 4"만 나와 원인을 알 수 없다. 증권(6612)·보험(651)·저축은행(6413) 등은 미확인(§7).

목표: 원인을 알려 주는 안내 한 줄. **판정 로직·status·재계획 동작은 바꾸지 않는다.**

## 2. 금융업 식별 (D4 내부, 신호 2개 AND)

신호 ① 재무 API가 Y·Y-1 모두 0건(기존 `NO_FINANCIALS` 경로, D4 §4-3·5 그대로) AND ② 해당 종목 `company.induty_code`가 `64`·`65`·`66`으로 시작.

- ②는 신규 모듈 함수 `_is_financial(stock_code) -> bool`(financials.py). 구현: `store.read("SELECT induty_code FROM company WHERE stock_code=? AND listed=1", (code,))`(D2 `store.read` 재사용, 읽기 전용; DB 오류·행 없음·NULL → False). `asyncio.to_thread`로 호출(`get_crno`와 동일 패턴). 테스트는 이 함수를 monkeypatch(D1 §6 패턴). D2 공개 API 추가는 하지 않는다(범위 최소; 필요 시 후속).
- **실DB 근거(읽기 전용, 상장 4,019사)**: 접두 64·65·66 합계 505사(12.6%). 은행 6412 = 17사(실제 코드 `64121`), 증권 66121=27, 보험 651x·652x=17, 금융지주·기타 64992=93, 여신전문 6491=14, 집합투자·신탁 642xx=약 37, 스팩·기타금융 66199=149. `detail_status != ok` 0, `induty_code IS NULL`(ok 행) 0 → 현재 캐시는 ②에 누락 없음.
- **오탐**: 접두 64·65·66에는 지주회사(64992, 93사; SK·LG·GS 등)와 스팩(66199)처럼 **실제로는 재무 API가 응답할 수 있는 종목**이 섞여 있다. ①(0건)과 AND이므로, 이들이 정상 응답하면 안내는 붙지 않는다(영향 없음). 이들이 0건이면 원래도 NO_FINANCIALS였고 안내 문구가 "금융업 분류 종목은 지원하지 않는다"로 약간 부정확할 뿐 → 수용(문구는 단정 대신 "금융업 업종 분류 종목"으로 §3에서 완충).
- **미탐**: 금융업이지만 0건이 아닌 응답(일부 금융업은 행이 있을 수 있음, H7) → 기존대로 정상/None 처리, 안내 없음. 접두 밖 금융 연관 업종(예: 67·68)은 대상 아님.
- **0건의 다른 원인(상장 직후·미공시)**: 금융업 접두 종목이면 구분 불가 → 문구를 "금융업은 지원하지 않거나 재무 미공시"가 아니라 사용자 안내 요구 문구로 쓰되 단정 어미를 피한다(§3). 비금융업 0건은 기존 문구 유지.
- crno 없음(`get_crno` NO_RESULT, D4 §4-2) 경로는 건드리지 않는다(원인이 D2 캐시 문제일 수 있음).

## 3. 표현: 새 코드 없이 `ToolError.message`로 구분

D0/D1 확인: `ToolError`는 `code`·`message`만 있고(`detail` 필드 없음) `ErrorCode`는 고정 8개. `message`는 "사용자 노출용 한국어 요약"이며 D8 `record_batch`가 `errors[].message`에 그대로 담고 `tool_cache`에도 저장된다. → **코드는 `NO_FINANCIALS` 유지, `message`만 상수로 구분.**

```python
# backend/tools/financials.py
_NO_FIN = "재무 정보를 찾을 수 없습니다."                       # 기존
NO_FIN_FINANCIAL = "금융업(은행·보험·증권 등)은 이 데이터 소스에서 지표 산출을 지원하지 않습니다."  # 신규, respond가 import
# get_financials 본문, 기존 `fail("NO_FINANCIALS", _NO_FIN)` (D4 §4-3·5 경로) 대체:
#   return fail("NO_FINANCIALS", NO_FIN_FINANCIAL if await asyncio.to_thread(_is_financial, code) else _NO_FIN)
```
- `fail()`의 마스킹·D1 `is_cacheable`(NO_FINANCIALS 캐시)·D0 §1 캐시 규칙과 충돌 없음(코드 동일).
- 새 `ReasonCode` 불필요: 판정(`verdicts[].reasons`)은 그대로 `["NO_FINANCIALS"]`. REASON_KO/REASON_TEXT 9키 고정 유지. 금융업 여부는 verdict가 아니라 `errors[]`(message 일치)에서 읽는다.
- 문구 일치 판정은 `e["message"] == NO_FIN_FINANCIAL`(상수 import, 문자열 복제 금지).

## 4. 영향 단위별 변경점 (구현 범위 = 아래 2개 파일 + 테스트)

| 단위 | 변경 |
|---|---|
| D4 `financials.py` | §3 상수·`_is_financial`·반환 1곳. 입출력·캐시·실패 매핑표 변화 없음(NO_FINANCIALS 행의 message만 둘로 갈림). |
| D9 `respond.py` | 아래 두 곳(§4-1). |
| D8 execute/judge | **변경 없음.** `record_batch`가 message를 이미 `errors[]`에 싣고 judge는 `failure_verdict("NO_FINANCIALS")`만 쓴다. |
| D6 / D0 / D1 | 변경 없음. |
| D3 | **후보 단계에서 거르지 않는다.** 걸러 내면 후보 0 → D0 §12 규칙 3 재계획(시장 확대 등)이 무의미하게 돌고 사용자는 이유를 못 본다. 경고 부착도 하지 않음(미리 알려면 get_financials 응답이 필요, 중복 호출). |
| D14/D15 | **변경 없음.** 안내는 `answer` 텍스트에 들어가 chat.js가 그대로 렌더. `REASON_TEXT`(리포트·로그 사유) 키 불변. |

### 4-1. respond.py (최소)
1. 헬퍼 `_fin_note(state) -> str | None`: `errors` 중 `tool=="get_financials"` AND `code=="NO_FINANCIALS"` AND `message==NO_FIN_FINANCIAL`이 하나라도 있으면 `"※ " + NO_FIN_FINANCIAL`, 없으면 None.
2. `_screen`: 마지막 `_not_built` 부착 직전(status 무관 공통 지점)에 `_fin_note`가 있으면 빈 줄 + 안내 한 줄 추가. status가 answered/limit/error 어느 쪽이든 동일하게 붙는다(제외 종목 목록의 "재무정보 없음" 옆에 설명이 생김). `_excluded` 줄 자체는 불변.
3. `error_text`의 lookup 분기(`e["code"]=="NO_FINANCIALS"`): `e["message"]==NO_FIN_FINANCIAL`이면 `f"‘{t}’의 재무정보를 제공하지 못합니다. {NO_FIN_FINANCIAL}"`, 아니면 기존 문구. (screen error 경로의 "후보 N개 모두 판단불가: 재무정보 없음 N"은 불변, 1번 안내가 아래에 붙는다.)

## 5. status/재계획 정합 (D0 §12)

- **전부 금융업 후보(예: 대표 요청 2 실호출)**: 전 후보 UNDECIDABLE(NO_FINANCIALS) → `n_dec==0` → 규칙 4 `status=error`, **재계획 없음**(변화 없음). 화면: "후보 4개 모두 판단불가입니다: 재무정보 없음 4" + 빈 줄 + "※ 금융업(은행·보험·증권 등)은 이 데이터 소스에서 지표 산출을 지원하지 않습니다". `answered`로 바꾸지 않는다(D0 규칙 4 유지, 상위 계약 변경 회피).
- 금융업+비금융업 혼합: 비금융업 판정에 따라 기존 규칙 5·6대로 진행(재계획 시 6413 등 유사업종 확장은 기존 로직). 금융업 종목은 UNDECIDABLE로 남고 안내만 추가. 산업 평균 PER은 금융업 행이 `rows`에 없으므로 비금융업 중앙값(D8 기존 동작).
- 따라서 §12 규칙·`expansion_step` 불변, 불필요한 재계획이 새로 생기지도 줄지도 않는다.
- **한계(명시)**: 대표 요청 2의 의도 흐름(재계획·limit 응답)은 실호출로는 여전히 재현되지 않는다(은행 데이터 부재). A안은 "원인이 보이는 error"까지만 보장. 해당 흐름 검증은 mock E2E(`tests/test_d16_e2e.py`, 정상 재무 가정, 변경 없음)가 계속 담당.

## 6. 테스트 관점 (실호출 없음, 기존 364 passed 유지)

- `tests/test_financials`(D4 기존 파일에 추가, `_fetch_fin` 0건 mock + `_is_financial` monkeypatch): (a) 금융업 True+0건 → `NO_FINANCIALS`, `message==NO_FIN_FINANCIAL`; (b) False+0건 → message `_NO_FIN`(기존 단언 그대로); (c) 금융업 True라도 정상 행이면 ok(안내 무관); (d) `_is_financial` 자체: 임시 DB(또는 `store.read` monkeypatch)로 64121→True, 26110→False, NULL/행 없음→False; (e) 이 message가 `is_cacheable` True·키 미포함.
- respond 단위 테스트: errors에 금융업 message 포함 → screen error 본문에 안내 줄 포함 / 비금융업 NO_FINANCIALS만 → 안내 없음·기존 문자열 동일 / lookup 금융업 message → 새 문구, 비금융업 → 기존 "재무정보를 찾을 수 없습니다." / answered·limit 혼합에도 안내 1회.
- E2E 보강 1건(mock): 은행 후보 전부 `get_financials`가 금융업 메시지의 NO_FINANCIALS를 반환 → `status=error`, `replan_count==0`, answer에 "금융업" 안내 포함. 기존 은행 시나리오(정상 재무 mock)는 수정 없이 통과해야 함. 기존 단언 중 `재무정보 없음 N` 문자열 일치가 있으면 부분 일치 유지 확인.

## 7. 구현 시 확인 항목

- **다른 금융업종 실호출**: 증권(66121), 보험(651·652), 저축은행(6413x), 여신전문(6491), 금융지주(64992 중 금융지주 vs 일반 지주)의 `getSummFinaStat_V2` 응답이 0건인지, 행이 있는지, 핵심 5값이 채워지는지. 행이 있으면 안내 대상이 아니며(기존 처리), 전부 0건이면 접두 범위 유지, 일부만 0건이면 접두 좁힘 여부를 사용자 판단으로.
- 지주회사(64992, 93사)·스팩(66199, 149사)의 실응답. 정상이면 오탐 우려 해소, 0건이면 문구 완화 검토.
- 은행 외 접두의 실제 `induty_code` 자릿수·접두 일치(`LIKE '64%'` vs `substr`) 구현 시 단일 방식으로.
- 상장 직후·미공시 금융업 종목은 구분 불가(수용, §2).

## 8. 상위 계약 요청 (제안만)

- 없음(필수). 선택 제안: D4 §3 실패 표 `NO_FINANCIALS` 행에 "message가 금융업 안내 상수일 수 있음" 한 줄, D9 §표현 규칙에 `_fin_note` 한 줄 반영(계약 변경 아님, 기록). 향후 금융업 지표 산출을 실제로 지원하려면 별도 데이터 소스(DART 재무제표 등)가 필요하며 이는 본 단위 범위 밖.
