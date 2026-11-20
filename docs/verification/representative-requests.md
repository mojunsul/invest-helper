# 대표 요청 5개 실제 실행 기록 (invest-helper.md §10.7)

실행일 2026-11-20. 실제 서버 앱(`backend.main:app`, FastAPI `TestClient`+lifespan)에 `POST /api/chat`, 실행 로그는 `GET /api/logs/{session_id}`. 실제 data.go.kr·GMS(claude-haiku-5-5) 호출. 요청마다 새 session_id.
시세 기준일 2026-11-18, 재무 FY2025. 산업 캐시: 4,019개 상장사.

## 기록표

| # | 요청 | 목적 | 최종 응답(요지) | status | 실행 로그(단계·Tool·재계획) | 시간 | session_id |
|---|---|---|---|---|---|---|---|
| 1 | 반도체 중 ROE 10% 넘고 PER 산업 평균 이하인 종목 알려 줘 | 한 번에 충족 | 후보 20개 중 3개 충족(SK하이닉스 ROE 35.59%/PER 18.83, DB하이텍 11.26%/12.80, 미코 10.88%/9.14). PER 산업 평균 22.53배(후보 14개 중앙값, 공식 평균 아님 명시). 적자 종목 4곳은 "판단불가: 순이익 적자(PER 산출 불가)" 사유 표시. 면책 부착 | answered | analyze→plan→execute→judge→respond(각 1회), find_stock_candidates 1 + get_financials 20 전부 ok, 재계획 없음, llm_decision 4건(intent=screen, tools, conditions, summary=llm) | 12.39s | session_id 48b3cd8c-0f7f-498f-8569-d737631ed771 |
| 2 | KOSPI 2차전지 중 ROE 15% 넘는 종목 (시장 지정, 충족 0 후 시장 확대) | 재계획 후 충족 | KOSPI 6곳 충족 0 → 재계획 1회(시장 확대 KOSPI→전체) 후 후보 15개 중 1개 충족(비츠로셀 ROE 17.22%), 12개 탈락, 2개 판단불가(이엠티 등 NO_FINANCIALS). 응답에 "시장 KOSPI → 전체(확대)"·재계획 이력 표시. 면책 부착 | answered | analyze,plan,execute,judge,**plan,execute,judge**(round 1),respond. replan: `market_expand`(trigger `no_pass`) 1건. find_stock_candidates 2 + get_financials 21(ok 19 + NO_FINANCIALS 2) | 11.75s | session_id a58afc10-ccdd-48ad-995f-79423b56b78a |
| 3 | KOSPI 반도체 중 ROE 200% 넘고 부채비율 1% 이하인 종목 | 재계획 상한 도달 → 되묻기 | 충족 0(103개 모두 불합격). 재계획 3회(`market_expand`→`similar_industry`→`limit_up`, 모두 trigger `no_pass`) 후 "재계획 한도 도달" 사유와 함께 "조건을 완화할까요?" 되묻기. 조건 줄에 "시장 KOSPI → 전체(확대)", 이력 3건, 최고 ROE 35.84%·최저 부채비율 4.39% 안내 | limit | round 0~3 각각 plan/execute/judge(총 plan·execute·judge 4회씩)+respond. find_stock_candidates 4 + get_financials 175(ok 174, TIMEOUT 1). REPLAN_MAX=3 도달 | 56.11s | session_id fdabd68c-ef8a-4108-a685-ff860c5bb0b5 |
| 4 | KOSPI 2차전지 중 ROE 5% 넘는 종목 (LG에너지솔루션 재무 응답을 빈 값으로 주입) | Tool 하나 실패해도 나머지 진행 | 6개 중 2곳 충족(DN오토모티브 13.27%, 세방전지 8.51%), 3곳 미충족, LG에너지솔루션 "재무정보 없음"으로 제외 목록에 사유 표시 | answered | get_financials 6건: ok 5 + NO_FINANCIALS 1(주입), 나머지 계속 진행, 재계획 없음 | 5.52s | session_id 미기록(로그 파일 없음) |
| 5a | 안녕 | Tool 불필요 | 안내 템플릿+면책 | answered | analyze→respond만(plan·execute 없음), tool 호출 0 | 1.04s | session_id 미기록 |
| 5b | 이 서비스 어떻게 써? | Tool 불필요 | 같은 안내 템플릿 | answered | analyze→respond, tool 0 | 1.06s | session_id 미기록 |

참고(요청 2·3): 이엠티(232530) 등은 **주입 없이 실제로** `NO_FINANCIALS`(요청 2), `TIMEOUT` 1건(요청 3)이 나와 "Tool 하나 실패, 나머지 진행"이 자연 발생으로도 재현됐다. 요청 4의 주입은 `financials._fetch_fin`을 프로세스 내 monkeypatch로 한 종목(첫 번째 crno)만 빈 응답으로 교체한 것(키 오류가 아닌 빈 값, §10.7 허용 방식).
요청 3 참고: 후보 수 103개는 `limit_up`(후보 상한 확대) 후 값이다. 판단불가 0개로 집계됐으나 TIMEOUT 1건이 있었다(해당 종목이 후속 라운드에서 재조회되어 판정된 것으로 보이며, 개별 확인은 하지 않음 — 미확인).

## 기대와 실제 차이

| # | 기대 | 실제 | 차이·원인·수정 필요 여부 |
|---|---|---|---|
| 1 | 한 번에 충족, 재계획 없음 | 일치 | 없음 |
| 2 | 재계획 후 충족 또는 limit(비금융) | 재계획 1회(시장 확대) 후 answered | 일치. 이전 실행(시장 미지정 2차전지 ROE 40%)은 `limit_up`이 실효 없이 소모됐으나 D9a로 해결(아래 표) |
| 3 | 재계획 상한 3회 도달 | 3회 도달(`market_expand`,`similar_industry`,`limit_up`), "재계획 한도 도달" | 일치. 이전 실행은 2차전지 후보가 15개(<20)라 3회에 못 닿았다(승인된 해석: 효과 있는 단계만 센다). 후보 > 20 + 시장 지정 + 유사 업종이 있는 반도체로 재선정해 실데이터로 상한 도달 확인 |
| 2/3 원 설계(은행) | DESIGN §10 "ROE 25%·부채비율 30% 이하 은행" → 재계획 → 상한 | **은행은 사용 불가**: 후보 4곳 전부 NO_FINANCIALS(금융업 미지원 안내), status=error, 재계획 없음 | 데이터 소스(금융위 재무요약)가 금융업을 지원하지 않는 설계된 동작(D4a). 문서 예시를 비금융 산업으로 교체함(사용자 승인, 아래 절) |
| 4 | NO_FINANCIALS 종목 UNDECIDABLE·제외 사유, 나머지 진행 | 일치 | 없음 |
| 5 | plan 건너뜀 | 일치 | 없음 |

## 은행 예시 교체와 수정 사항

- **은행 예시 교체(사용자 승인)**: DESIGN §10 요청 2("ROE 25% 넘고 부채비율 30% 이하 은행")·invest-helper.md §10.7 예시 표는 은행이 금융업이라 재무 소스(금융위 재무요약)가 미지원(D4a) → 후보 전부 `NO_FINANCIALS`, 재계획 전에 status=error로 끝나 재현 불가. 비금융 예시로 교체: 요청 2 = "KOSPI 2차전지 중 ROE 15% 넘는 종목"(재계획 후 충족), 요청 3 = "KOSPI 반도체 중 ROE 200% 넘고 부채비율 1% 이하인 종목"(상한 도달). 체크리스트 xlsx E31·E32의 은행 문구도 같이 교체. 원본 요구 문장(§10.7 상단의 일반 요구)은 불변.
- **D2d(현대차 별칭)**: 약칭 "현대차"가 현대차증권(001500)으로 해석되던 문제를 D2d 별칭 정규화(현대차→현대자동차 등)로 수정(커밋 a85cdfe). D17 비교 ③ 재실행 결과는 `D17-rag-comparison.md` 참조.

## 이전 실행 결함의 원인과 수정 (D9a)

| 이전 관찰 | 원인 | 수정·재확인 |
|---|---|---|
| `limit_up`이 효과 없는 재호출(요청 2에서 round 1 후보가 round 0과 같음; 기존 기록의 "원인 미조사") | `limit_up`이 "후보 수가 상한(20) 미만이면 유효"로 판정됐다. 즉 후보 수(15) < 상한(20)이라 상한을 올려도 후보가 늘 수 없는데도 유효 단계로 처리됨 | D9a: `limit_up`은 후보가 현재 상한에 **찼을 때만** 유효. 재실행에서 요청 2(후보 6→15)는 `market_expand` 1회 후 충족해 종료, `limit_up` 낭비 호출 없음. 요청 3은 103개(상한에 찬 업종)에서만 `limit_up`이 사용됨 |
| 시장 확대 후 응답이 "시장 KOSPI"로 표기됨 | 응답 조건 줄이 최초 조건만 출력, 확대된 시장 미반영 | D9a: 조건 줄에 "시장 KOSPI → 전체(확대)" 표기. 재실행 요청 2·3 모두 해당 표기 확인(해결) |
| 재계획 2/3회인데 응답이 "한도 도달"로 표기됨 | limit 사유가 실제 재계획 횟수·종료 원인과 무관하게 고정 문구 | D9a: limit 사유 코드 분리(더 넓힐 검색 범위 없음 vs 재계획 한도 도달). 재실행 요청 3은 3회에 실제 도달하여 "재계획 한도 도달" 표기가 사실과 일치(해결) |

새 실행에서 발견한 결함(수정하지 않음, 보고만): 요청 2의 응답 요약 첫 단락에 LLM이 "제공된 데이터에는 종료 사유(limit_reason) 값이 없습니다"라는 내부 필드명 문장을 넣었다. answered 응답에는 limit_reason이 없는 것이 정상이므로 불필요한 문구이며, D9a §6이 예고한 "SUMMARY_SYSTEM 문구는 LLM이 어길 수 있음"의 사례다(숫자 검증은 통과).

## NF3005 처리 시간 판정

요청별 시간: 요청 1 12.39s(후보 20), 요청 2 11.75s(재계획 1회), 요청 3 56.11s(재계획 3회·후보 103개·get_financials 175건), 요청 4 5.52s, 요청 5 약 1s. 외부 호출 간격: `apis.data.go.kr` 호스트 최소 0.1s throttle(`backend/tools/base.py` `HOST_INTERVAL_S`), OpenDART 0.2s — 순간 폭주를 막는 간격이며 호출 수에 비례해 시간이 늘어난다.
판정: 일반 요청(후보 20개 이하·재계획 0~1회)은 약 1~12초로 **사용에 지장 없음**. 재계획 3회+후보 100개 이상인 최악 사례는 56초로 길지만, 이는 충족 불가 조건의 상한 도달 경로에 한정되며 처리 중 표시(index.html)가 있어 **허용 범위로 판단하되 지장 소지 있음(경계)**. 

**처리 시간 기준(명세 NF3005에 수치가 없어 프로젝트에서 정함, 2026-11-20 사용자 승인)**: 대표 요청 1건당 60초 이내이고, 처리 중 화면에 "분석 중입니다…" 표시가 나오면 사용에 지장이 없는 것으로 본다. **이 기준은 위 5건을 측정한 뒤에 정했다**(측정 전 사전 약속이 아니며 명세 기준도 아니다). 대조: 요청 1 12.39s · 2 11.75s · 3 56.11s · 4 5.52s · 5 1.04/1.06s로 전부 60초 이내이고, 요청 3은 여유가 3.89s로 작다(외부 API 상태에 따라 재측정 시 60초를 넘을 수 있으며 이번에 재측정하지 않았다). 처리 중 표시 근거: `frontend/chat.js` `setBusy(true)`가 "분석 중입니다… (최대 수십 초 걸릴 수 있습니다)"를 표시(실호출 캡처 `screenshots/23-chat-live-processing.png`). 외부 호출 간격 throttle(`HOST_INTERVAL_S`)은 커밋 1b8cd86(2026-11-19 23:08 KST)에서 도입돼 이 기록(2026-11-20 09:48~10:05 KST) 이전에 적용돼 있었다. 판정: 기준 60초 이내이고 처리 중 표시가 있어 **통과**(요청 3은 여유가 작음).

**기록표 보완(읽기 전용 DB 조회, 원 기록이 아님)**
- 요청 3 `fdabd68c-…`: Tool 호출 179건(`find_stock_candidates` 4 + `get_financials` 175) 중 캐시 적중 71건을 뺀 108건이 실제 외부 호출. HTTP 요청 수는 DB에서 알 수 없어 계속 미확인.
- 요청 4: 원 기록은 session_id 미기록. 실DB 대조로 `57e875c8-8e88-4da2-9d89-37adf09b95e8`가 **추정 일치**(`llm_decision` 조건 `industry=2차전지; ROE>=5`, find 1 + get_financials 6, `NO_FINANCIALS` 1건, 길이 5.50s vs 기록 5.52s, 같은 모양의 세션이 유일). 원 기록의 session_id가 아니다.
- 요청 5a/5b: 원 기록은 미기록. 길이(±0.1s)·intent=chitchat·tool 0·`llm_decision` reason이 같고 유일한 세션 `d1907a37-ccf7-46a8-88ff-fa01b5f1d4b5`(1.03s, "단순 인사…")가 5a, `f9300b3e-9564-4cee-8b80-a4ba2a217552`(1.02s, "서비스 사용법을 묻는 일반 질문…")가 5b로 **추정 일치**. 질문 원문은 `run_logs`에 저장되지 않아 확정할 수 없다.

## 실행 로그 샘플 (제출물용)

- 대표 요청 1건 전체 로그(응답+이벤트 전부): `docs/verification/logs/rep1-one-shot.json`
- 재계획 발생 사례: 요청 3 재계획 3회(상한 도달) `docs/verification/logs/rep3-replan.json`, 요청 2 재계획 1회 후 충족 `docs/verification/logs/rep2-replan.json`
- 세 파일에서 API 키 값(DART·DATA_GO_KR·GMS)과 `serviceKey`·`Traceback`·`Bearer`·`x-api-key` 문자열이 없음을 확인했다.

## 외부 호출 횟수(이 작업 전체, httpx 계측)

| 대상 | 횟수 |
|---|---|
| 공공데이터포털(apis.data.go.kr) | 이전 551(비교 267 + 대표 요청 1차 122 + 2차 162) + 재실행: 요청 2 = 66(계측), 요청 3 = 계측 누락(아래 추정) |
| GMS | 이전 38 + 재실행: 요청 2 = 2, 요청 3 = 약 2(계측 누락, analyze+summary 각 1회로 추정), D17 ③ = 2 |
| DART | 색인 스크립트 16(코드상 값, 계측 아님) |

재실행 요청 3의 호출 계수기 출력이 로그 필터링 중 유실되어 정확한 횟수는 **미확인**이다. 요청 2가 get_financials 21건·find 2건에 HTTP 66회(건당 약 3회)였던 비율로 추정하면 요청 3(get_financials 175건·find 4건)은 대략 300~500회로, 승인 한도(재실행 합계 200회)를 **초과했을 가능성이 높다**. 상한 도달 요청의 본질적 비용이다(후보 103개 전수 조회).

## 실호출 브라우저 E2E(D24)

실행일 2026-11-20(KST 16:05~16:08). 목적: 체크리스트 27번의 실호출 브라우저 증빙 확보와 8.7 보완. **키 발급 당일 호출 기록은 없으며(미확인) 이 실호출은 그것을 대체하지 않는다.** 23번 판정에는 쓰지 않았다(위 기록 5건으로 판정).

| 요청 | 서버 `run_logs` ts(UTC) | Tool 호출 수(DB) | 공공데이터 HTTP | OpenDART HTTP | GMS HTTP | status |
|---|---|---|---|---|---|---|
| C `KOSPI 2차전지 중 ROE 5% 넘는 종목, 공시도 같이 보여줘` | 07:05:43.7~07:05:50.8(7.04s; 브라우저 POST 응답까지 8.4s) | 9(find 1 + get_financials 6 + search_disclosures 2) | 22/40 | 2/6 | 2/4 | answered(충족 2·불충족 4·판단불가 0, 공시 줄·DART 링크 표시) |
| A `삼성전자 ROE랑 PER 알려줘` | 07:08:20.5~07:08:23.1(2.58s) | 1(get_financials) | 3/12 | 0/0 | 2/4 | answered(ROE 10.36%·PER 25.46배 등, FY2025) |
| 합계/상한 | C 종료 후 A 시작까지 약 150초 | 10 | 25/52 | 2/6 | 4/8 | 외부 HTTP 31건(상한 66) |

- HTTP 건수는 기동 래퍼의 `httpx.AsyncClient.send` 계수치만 썼다(상태코드는 전부 200, 연결 오류·429·403·5xx 0건, 상한 초과 차단 0건, 중단 표식 없음, STOP 이후 호출 없음). 래퍼는 저장소에 없고(스크래치패드 전용) 호스트명·건수·오류 클래스명만 기록한다.
- C의 `want_disclosures`: `llm_decision` tools에 `search_disclosures`가 포함되어 공시가 실제로 호출됐다(`logs/live-c-screen-disclosure.json` seq 3, 25~28).
- 방법: 요청마다 **새 서버 프로세스**(`httpx` 계수 래퍼 + `uvicorn.run(backend.main:app)`, 127.0.0.1:8765, cwd=`invest_helper.db` 사본이 든 임시 폴더). 실DB는 `sqlite3 -readonly .backup`으로 복사만 했고 실행 전후 해시·`run_logs` 1,909행이 같다. 사용자 서버(8000번)는 접근하지 않았다. 서버 출력은 파일로 리다이렉트(키 grep 0건). 브라우저는 헤드리스 Chrome(CDP)이며 같은 탭에서 `index.html` 질문 입력→결과→`reports.html`→(A) `/api/logs/{sid}` 순으로 이동했다.
- 증빙: `screenshots/27-chat-live-screen-disclosure.png`(C), `27-chat-live-lookup.png`(A), `23-chat-live-processing.png`(C 처리 중), `network/21-live-network-requests.json`(method·path·status·ms), `logs/live-c-screen-disclosure.json`·`live-a-lookup.json`(run_log).
- 한계: ① 프런트엔드는 `/api/logs`를 호출하지 않는다. 네트워크 기록의 `/api/logs/{sid}`(A 실행)는 사람이 주소를 직접 연 것과 같은 방식의 요청이다. ② 네트워크 기록은 CDP 이벤트이며 DevTools 네트워크 탭 화면 캡처가 아니다. ③ 헤드리스 캡처라 주소창이 보이지 않는다(서버 주소는 이 문서·네트워크 기록에 적음). ④ `/api/chat` 응답 본문을 파일로 저장하지 못해 실행 로그 JSON의 `response`는 null이다(화면 렌더가 증빙). ⑤ 두 요청 모두 같은 날 단발이며 대표 요청 3 같은 대량 호출은 하지 않았다. ⑥ 요청 시간(7.04s·2.58s)은 23번 판정에 쓰지 않았다.
