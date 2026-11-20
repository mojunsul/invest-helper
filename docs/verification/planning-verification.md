# 기획 검증 결과 (명세 8.7)

이 문서는 `invest-helper.md` §8.7 "기획 검증"의 항목별 결과다. 8.7은 원래 구현 전에 하는 검증이며, 이 프로젝트에는 **구현 당시의 호출 기록이 따로 남아 있지 않다.** 그래서 아래는 구현 후(2026-11-20) 저장소에 남은 실호출 기록(`representative-requests.md`, `D17-rag-comparison.md`, `logs/*.json`)과 설계·테스트 문서를 **열어 대조한 결과**이며, 이번 정비에서 외부 API를 다시 호출하지 않았다. 기록이 없는 항목은 `미확인`으로 적는다.

## 1. 도메인 조건 확인

### 1-1. 외부 API 3종 호출

| 출처 | 확인 근거 | 상태 |
|---|---|---|
| 공공데이터포털 금융위 주식시세·기업 재무정보 | 대표 요청 1~3 실행 로그에서 `find_stock_candidates`(시세)·`get_financials`(재무)가 `ok`로 응답(`logs/rep1-one-shot.json`, `rep2-replan.json`, `rep3-replan.json`; 시세 기준일 2026-11-18, 재무 FY2025) | 구현 후 실호출 성립 확인(2026-11-20) |
| OpenDART | 공시 원문 색인(`D17-rag-comparison.md` §1: 8종목 8/8 색인) | 구현 후 실호출 성립 확인(2026-11-20) |
| SSAFY GMS(claude-haiku-5-5) | 대표 요청 1의 `llm_decision` 이벤트 4건(intent·tools·conditions·summary), `rep1-one-shot.json` | 구현 후 실호출 성립 확인(2026-11-20) |

- **키 발급 당일(1일차 16시까지) 호출을 확인한 기록은 저장소에 없다 → 미확인.** 그 시점의 확인은 소급해서 쓰지 않는다. 이후 구현이 끝난 뒤 위 실호출로 3종이 모두 성립함을 확인했다.
- 출처·이용 조건·호출 한도는 README §7(확인일 2026-11-20)에 기록했다. GMS의 한도·약관과 OpenDART 일일 한도의 공식 수치는 그쪽에도 `미확인`으로 적혀 있다.

### 1-2. 'LLM만으로 답하면 무엇이 틀리는가' (대표 요청별 한 줄)

수치는 `representative-requests.md`에 기록된 실제 실행 결과다.

| 대표 요청 | LLM만으로 답하면 |
|---|---|
| 1. 반도체 중 ROE 10% 넘고 PER 산업 평균 이하 | 최신 재무값과 산업 평균을 알 수 없어 숫자를 지어내게 된다(실제: SK하이닉스 ROE 35.59%·PER 18.83, 산업 평균 PER 22.53배는 후보 14개 중앙값이고 적자 4곳은 PER 산출 불가) |
| 2. KOSPI 2차전지 중 ROE 15% 넘는 종목 | KOSPI 6곳에서 충족이 0이라 시장을 넓혀 다시 찾아야 한다는 사실과 확대 후 후보 15개 중 비츠로셀(ROE 17.22%) 1곳만 충족한다는 결과를 실조회 없이는 알 수 없다 |
| 3. KOSPI 반도체 중 ROE 200% 넘고 부채비율 1% 이하 | 후보 103개가 모두 불합격이라는 사실과 최고 ROE 35.84%·최저 부채비율 4.39%를 알 수 없어 "없다/있다"를 근거 없이 단정하게 된다 |
| 4. KOSPI 2차전지 중 ROE 5% 넘는 종목(한 종목 재무 없음) | 종목별 재무 데이터의 유무(LG에너지솔루션 NO_FINANCIALS)를 몰라 값을 채워 넣거나 빠뜨린 사실을 말하지 못한다(실제: 6개 중 2곳 충족, 1곳은 재무정보 없음으로 제외 사유 표시) |
| 5. 안녕 · 이 서비스 어떻게 써? | 해당 없음. **Tool 불필요(LLM만으로 충분)** |
| (D17) 삼성전자 사업보고서에서 가이던스는? | 공시 원문 서술은 LLM이 모르고 지어낼 수 있다. 원문 색인에서 근거 문단을 찾아야 한다(`D17-rag-comparison.md` §3 C 상태: 근거 있을 때만 답하고 없으면 "확인되지 않습니다") |

## 2. Tool 설계 확인

- **실제 응답 → 출력 구조 매핑**: 시세·재무 응답이 `find_stock_candidates`/`get_financials` 출력(후보 목록, 재무 원천값, `meta.requested_basis`·`actual_basis`·`estimated`)을 채움을 대표 요청 로그의 `tool_result`로 확인. 구조 정의는 `docs/DESIGN.md` §3, 구현 검증은 `tests/test_d3.py`·`tests/test_d4.py`·`tests/test_d5.py`.
- **빈 값·결과 없음의 실패 코드 반영**: 재무 값이 비면 `NO_FINANCIALS`(요청 2의 이엠티, 요청 4의 주입 사례; `tests/test_d4.py`, `tests/test_d16_e2e.py::test_req3_no_financials_undecidable`), 공시 없음은 `NO_RESULT`(`tests/test_d5.py`), 금융업은 재무 소스 미지원이라 후보 전부 `NO_FINANCIALS`와 안내(D4a, `tests/test_d16_e2e.py::test_d4a_all_financial_candidates_error_with_note`; `representative-requests.md`의 은행 예시 교체 절).
- **호출 한도·이용 조건**: README §7 표와 "우리가 지키는 방식"(호스트별 최소 호출 간격, 연속 연결 실패 시 중단, 429 처리). 공식 수치가 없는 항목은 거기에 `미확인`으로 표기.

## 3. 도구 연결 확인

- **LLM 호출 성공(API 키·접속 주소·환경변수 로딩)**: 대표 요청 1·2·3 실행에서 GMS(claude-haiku-5-5) 호출이 성공했고 응답이 `llm_decision` 이벤트로 남았다(`logs/rep1-one-shot.json` seq 2~4·55, `representative-requests.md`).
- **Tool 명세를 넘겼을 때 LLM의 Tool 선택**: 요청 1의 `llm_decision`(kind=tools, decision=`find_stock_candidates,get_financials`, 검증 결과 `unknown=0; match=yes`), 공시 원문 질문에서 `search_filing_text`가 선택된 라우팅은 `D17-rag-comparison.md` §3(질문 ①~③ 모두 intent=filing_qa, `search_filing_text` 호출)와 `docs/design/D17-filing-rag.md` §7.
- 이번 정비에서 LLM을 다시 호출해 재확인하지 않았다. 위는 저장된 실행 로그·문서 내용을 대조한 결과다.

## 4. 설계 시험

- **대표 요청 하나를 단계·Tool 순서대로 따라가기**: 요청 1(`logs/rep1-one-shot.json`)에서 `find_stock_candidates`(입력 `industry=반도체`는 analyze 결과)가 후보 20건을 돌려주고, 그 종목코드(`000660`, `000990`, `080220`, `440110`, `067310` …)가 그대로 `get_financials` 입력 `stock_code` 20건으로 전달됨을 확인(seq 9, 11~). 단계는 analyze → plan → execute → judge → respond.
- **재계획 조건과 상한**: 요청 2(`logs/rep2-replan.json`)는 충족 0 후 `market_expand` 1회(`trigger=no_pass`)로 충족, 요청 3(`logs/rep3-replan.json`)은 `market_expand → similar_industry → limit_up` 3회로 REPLAN_MAX=3에 도달해 `status=limit`로 되묻기. 조건·상한 정의는 `docs/DESIGN.md` §4, `tests/test_d9.py`.
- 대표 요청 4·5의 session_id는 기록에 없다(`representative-requests.md`에도 "미기록"). 그대로 `미기록`으로 둔다.
