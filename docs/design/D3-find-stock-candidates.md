# D3 find_stock_candidates (`backend/tools/stock_candidates.py`)

기준: D0 §1·2·3·4(§12 재계획 효과 판정)·6-12, D1 §1·3, D2 §3·5·8, DESIGN §3 '실호출 검증 결과'(주가 API 줄). 의사코드이며 구현 아님. D0/D1/D2 계약은 변경하지 않는다.

## 1. 목적·범위

산업(+선택 시장)으로 **비교 대상 후보 목록**을 만든다. 재무·지표 계산·충족 판단은 하지 않는다(D4/D6/D8). 후보 = D2 산업 캐시(`select_by_industry`)로 산업에 속한 상장사를 고른 뒤, **주가 API 1일치**에서 시장·시가총액·종가를 붙인 행. 외부 호출은 주가 API뿐(DART는 D2 캐시로 대체, 요청 경로에서 DART 호출 없음).

## 2. 확인된 사실 vs 가정

| 구분 | 내용 |
|---|---|
| 실호출 확인(DESIGN §3) | 주가 API `https://apis.data.go.kr/1160100/GetStockSecuritiesInfoService_V2/getStockPriceInfo_V2`, 파라미터 `basDt`, `likeItmsNm`. 응답 필드 `basDt, srtnCd, itmsNm, mrktCtg(KOSPI/KOSDAQ), clpr, mrktTotAmt, lstgStCnt` |
| **가정** | G1 `serviceKey`(DATA_GO_KR_KEY), `resultType=json`, `numOfRows`, `pageNo`, `basDt` 파라미터로 **특정 일자 전체 종목을 페이지 조회** 가능(종목명·코드 필터 없이). G2 `numOfRows` 상한(1000으로 알려졌으나 미확인)과 하루 전체 건수(수천 추정) → 몇 페이지. G3 응답 구조 `response.header.resultCode("00"=정상)`, `response.body.{totalCount, items.item[]}`; 단건이면 `item`이 list가 아닌 dict, 0건이면 `items`가 `""`일 수 있음. G4 한도초과/키오류는 HTTP 200 본문 `resultCode`로 오며 코드값은 미확인(§6에서 가정값 표기). G5 휴장일·당일(미반영)은 `totalCount=0`. G6 `clpr`·`mrktTotAmt`는 숫자 문자열(원 단위 정수). G7 `srtnCd`는 D2 `stock_code`와 동일한 6자리(우선주도 별도 srtnCd) |

## 3. 입출력

```python
MAX_LIMIT = 100          # 임의 상수(§9). 모듈 상수로 공개 — D9가 limit_up 단계의 candidate_limit에 사용
DEFAULT_LIMIT = 20       # 임의 상수(§9)

class CandidateInput(BaseModel):           # extra="forbid" 금지(D1)
    industry: str = Field(min_length=1)    # 사용자 표현 원문("반도체"). D2 resolve_industry로 해석
    market: Literal["KOSPI","KOSDAQ"] | None = None      # None=전체
    base_date: str | None = Field(default=None, pattern=r"^\d{4}-\d{2}-\d{2}$")   # None=최근 영업일
    extra_industries: list[str] | None = None            # D2 similar_industries 산출(KSIC 접두 숫자 문자열). None/[]=없음
    limit: int | None = Field(default=None, ge=1, le=MAX_LIMIT)   # None=DEFAULT_LIMIT. 범위 밖은 클램프 않고 INVALID_INPUT(D5와 동일)
```
- `@register(name="find_stock_candidates", requires_keys=("DATA_GO_KR_KEY",), timeout_s=<§9 상향>)`. 설명문(Agent 노출)은 DESIGN §3 표 그대로: "사용자가 산업·시장으로 종목을 찾아야 할 때, 비교 대상 후보 목록을 얻는다".
- **출력**: `ok(data=[{code:str, name:str, market:str, industry:str, mkt_cap:int, price:int}, ...], requested=base_date, actual=basDt("YYYY-MM-DD"))`. 길이 1..limit. `code`=stock_code, `industry`=D2 `induty_code`(KSIC 코드 문자열; 산업명 라벨은 D2에 없음). `mkt_cap`/`price`는 정수(원). 키·요청 URL은 data에 넣지 않는다(D1 §3 점검표).
- 빈 결과는 `fail("NO_RESULT", "조건에 맞는 후보가 없습니다.")`(`data=[]` 금지).
- `meta.estimated=False` 고정. 영업일 보정(요청일 ≠ 실제일)은 D0의 "연도 기준 estimated"가 아니므로 `actual_basis`로만 드러낸다(요청 2).

## 4. 처리 순서

1. 입력 검증은 D1 래퍼(INVALID_INPUT).
2. **산업 해석**: `r = resolve_industry(industry)`(동기·DB, `asyncio.to_thread`). `ok` → `prefixes=r.prefixes`. `unknown` → `INVALID_INPUT`("산업을 해석할 수 없습니다."). 단, `theme.cached_codes`가 있으면 그 종목 풀 사용(D2e). `ambiguous` → `INVALID_INPUT`(message에 `options[].term` 나열, 추측 안 함). D2 §5-4 권고 채택(NO_RESULT는 재계획만 낭비). 결과: D0 규칙 2에 따라 error — 되묻기 개선은 요청 1.
3. **extra_industries**: 각 원소를 `resolve_industry`로 재해석(숫자 2~5자리는 D2 §5-2로 그대로 `ok`). `ok`가 아닌 원소가 있으면 `INVALID_INPUT`. `prefixes = unique(primary + extras)`.
4. **산업 종목 조회**: `select_by_industry(prefixes)` → `[{stock_code,name,induty_code}]`. `IdentityError`는 `ToolFailure(e.code, e.message)`로 1:1 변환(D2 §3). 결과 `[]`이면 `NO_RESULT`.
5. **주가 일치 조회**: `basDt` 결정·전체 페이지 수집(§5). `srtnCd ∈ 산업 종목 집합`인 행만 남기고 `srtnCd` 기준 dedupe.
6. **시장 필터**: `market`이 있으면 `mrktCtg == market`인 행만. 0건이면 `NO_RESULT`.
7. **정렬·절단**: `mkt_cap` 내림차순, 동률은 `code` 오름차순(결정적 → 캐시 키·재계획 재현성). 상위 `limit`건. `mkt_cap`/`price`가 파싱 불가(None·0 이하)인 행은 제외(후보 행에 None을 두지 않아 D6의 후보 mkt_cap 보충이 항상 유효).
8. 변환 결과가 0건이면 `NO_RESULT`. 그 외 `ok(data, requested, actual)`.

## 5. 기준일(basDt) 결정과 페이지 수집

- 시작일 = `base_date` 또는 오늘. 시작일부터 **과거로 최대 `LOOKBACK_DAYS`(임의, §9)일** 1일씩 `getStockPriceInfo_V2(basDt=YYYYMMDD, numOfRows=PAGE, pageNo=1)`를 시도해 `totalCount>0`인 첫 일자를 `basDt`로 채택(주말·휴장·당일 미반영 대응, G5). 발견 시 `totalCount`만큼 나머지 페이지를 이어 조회. 끝까지 없으면 `API_ERROR`("주가 데이터를 찾지 못했습니다."): 후보 부재가 아니므로 NO_RESULT 아님(재계획 헛돎 방지).
- `actual_basis`=채택 일자(YYYY-MM-DD). 응답을 신뢰하지 않고 행의 `basDt`가 아니라 요청한 `basDt`를 쓴다.
- 이 흐름은 **호출 전체 타임아웃(D1 §3-3)** 안에서 순차 실행한다(병렬화·모듈 캐시는 단순성 위해 두지 않음, §9).

## 6. 실패 매핑

| 입력 | 코드 |
|---|---|
| 키 빈 문자열 | MISSING_API_KEY(D1 래퍼 선처리) |
| 본문 `resultCode`가 키 오류류(가정: 30·31·32 등 미확인) | MISSING_API_KEY(`ToolFailure`) |
| 본문 `resultCode`가 한도초과류(가정: 22 미확인), HTTP 429 | RATE_LIMIT |
| httpx Timeout / 래퍼 타임아웃 | TIMEOUT |
| `resultCode`가 정상(`"00"`) 아님·알 수 없음, HTTP/연결/JSON/응답 구조 오류 | API_ERROR |
| 산업 해석 불가·모호, 잘못된 `extra_industries` 원소 | INVALID_INPUT |
| 산업 종목 0건, 시장 필터 후 0건, 파싱 후 0건 | NO_RESULT |
| `IdentityError`(CACHE_NOT_READY/INDUSTRY_NOT_READY 등) | e.code 그대로(API_ERROR 등; 메시지에 build CLI 안내) |
| 기준일 탐색 전부 실패(§5) | API_ERROR |
- 호출 예외는 `raise ToolFailure(...) from None`, 로그는 타입명·status·건수만(키가 쿼리스트링에 실림, D1 NF3002). `print` 금지. URL·원문 본문은 로그·메시지에 넣지 않는다.
- 재계획 연동(D0 §4): NO_RESULT만 재계획 대상이고 나머지 `ok=False`는 error. 따라서 INVALID_INPUT/API_ERROR를 NO_RESULT로 둔갑시키지 않는다.

## 7. 캐시 상태 처리 요약 (D2 §3 표 적용)

| D2 상태 | D3 동작 |
|---|---|
| 빈 캐시 | `resolve_industry`는 통과(`n_listed=0`) → `select_by_industry`가 `CACHE_NOT_READY`(API_ERROR) → error |
| 미완료(complete=False) | `select_by_industry`가 `INDUSTRY_NOT_READY`(API_ERROR) → error |
| 완료(미완료 ≤5% 허용) | 정상. 일부 종목이 조용히 빠질 수 있음(D2 §8). Tool 출력에 경고 채널이 없어 **logging 건수 경고만** 남기고(`cache_status()` 호출) 응답 표기는 요청 3 |
- 후보 조회에서 API_ERROR는 D0 규칙 2로 `status=error`이며, 메시지는 D2 고정 문구(`python -m backend.identity.build` 안내)가 `errors[]`에 실린다.

## 8. 이벤트/로그 연계

- D3는 bus를 직접 쓰지 않는다. `tool_call`/`tool_result` 발행·duration·cache_hit은 D8(D1 §5). D8의 `summary`용으로 `data` 길이·`actual_basis`만 있으면 충분(예: "후보 N건, 기준일 YYYY-MM-DD").
- 로깅: `logger.info`로 건수(산업 종목 수, 주가 매칭 수, 최종 수, 채택 basDt)만. 종목명 목록·URL·키 금지.
- 재계획 인자 연동: ① 시장 확대 = D8/D9가 `market=None`으로 재호출, ② 유사 업종 = `extra_industries=similar_industries(conditions.industry)`, ③ limit 증가 = `limit=MAX_LIMIT`(D0 §12-③의 "D3 최대 상한" = `MAX_LIMIT`). 입력이 달라 캐시 키가 달라지므로 재호출된다(주가 페이지는 재조회, §9).

## 9. 구현 시 확인 항목

- **실호출 확인**: G1(basDt만으로 전체 일자 페이지 조회 가능한지), G2(`numOfRows` 상한, 일자별 건수·페이지 수·호출 소요 시간), G3(응답 구조 변형: 단건 dict, 0건 `""`), G4(한도초과·키 오류 `resultCode`/HTTP 상태), G5(휴장일·당일 응답), G6(숫자 문자열 형식), G7(우선주·ETF·ETN·스팩 등이 같은 응답에 섞이는지; 산업 종목 집합 교집합이라 무해하나 확인), `mrktCtg` 값 집합(`KOSDAQ GLOBAL`, `KONEX` 등 — `market=None`일 때 그대로 통과시킬지, `market` 필터를 동등 비교로 둘지).
- 만약 G1이 불가하면 대안은 후보별 `likeSrtnCd`/`srtnCd` 조회뿐이며 호출 수가 후보 수만큼 늘어 설계 재검토 필요.
- **임의 상수(근거 없음, 구현 시 측정 후 조정)**: `DEFAULT_LIMIT=20`, `MAX_LIMIT=100`, `PAGE`(numOfRows), `LOOKBACK_DAYS`(예: 7), `register(timeout_s=…)`(기본 10초로 N페이지×최대 LOOKBACK 시도가 모자라면 상향, D1 가정 4와 같은 방식).
- 엣지: 산업 종목 수가 `limit`보다 훨씬 많을 때 시가총액 상위만 남는 편향(대형주 우선; 의도된 단순화), 요청한 `base_date`가 미래, 시가총액 0·누락 행 제외로 인한 후보 감소, 산업 종목이 수백이어도 메모리 필터는 O(N) 수준, 재계획마다 주가 페이지 재조회(모듈 단위 `basDt` 캐시는 필요 시 후속).
- `asyncio.to_thread`로 D2 동기 함수 호출(D2 §8), 같은 요청 내 `resolve_industry`가 D2 `lru_cache` 용어 파일 로딩 실패(`ValueError`)하면 래퍼가 API_ERROR로 변환(설정 오류).

## 10. 테스트 관점(실호출 없음)

- 분리: 모듈 수준 `async def _fetch_page(basDt, page, rows) -> dict`와 `_today() -> date`를 monkeypatch/`respx`로 대체, `resolve_industry`/`select_by_industry`/`similar` 계열은 모듈 전역 참조로 monkeypatch(D1 §6 패턴).
- 점검 항목: 정상(필터·정렬·절단·필드 타입·`meta.actual_basis`), `market` 필터, `extra_industries` 합산, 동률 정렬 결정성, 영업일 후진(휴장→직전일)과 전부 실패 → API_ERROR, 다중 페이지 수집, 단건 dict·0건 `""` 응답 변형, 산업 unknown/ambiguous → INVALID_INPUT, 잘못된 extra 원소 → INVALID_INPUT, 산업 종목 0·시장 필터 후 0 → NO_RESULT, `IdentityError` 1:1 변환(빈 캐시/미완료), `resultCode`·HTTP 429·Timeout 매핑, limit 0·101·base_date 형식 오류 → INVALID_INPUT, 실패 메시지·로그에 키 미포함.

## 상위 계약에 대한 요청 (제안만)

1. **모호/미해석 산업 처리**(D2 요청 2와 동일 쟁점): D3는 `INVALID_INPUT`을 돌려주며 D0 규칙 2로 `status=error`가 된다. 사용자에게 되묻게 하려면 D0 §6-6에 "analyze/plan이 Tool 전에 `resolve_industry`를 호출해 `unknown/ambiguous`면 asked" 추가가 필요(사용자 판단).
2. **D0 §1 `estimated`**는 "정규화된 연도 기준"으로 정의되어 일 단위 기준일 보정에 맞지 않는다. D3는 `estimated=False` 고정 + `actual_basis`만 채운다. 이 해석을 D0에 한 줄 명시하거나, 응답 문구에서 기준일 차이를 알릴지 D9에 위임할지 확정 요청.
3. **일부 미구축 표기**: D2 complete가 미완료 5%까지 허용해 후보가 일부 누락될 수 있으나 ToolResult에 경고 채널이 없다. 필요하면 D9가 `cache_status()`를 직접 읽어 "산업 데이터 일부 미구축"을 표기하도록 D9에 위임(D0 변경 없음).
4. **D0 §12-③의 "D3 최대 상한"**을 `stock_candidates.MAX_LIMIT`(모듈 상수)로 정의했다. D9가 이를 import해 `candidate_limit` 상한 판정에 쓰도록 D0/D9에 명시 요청.
5. DESIGN §3 `find_stock_candidates` 출력의 `industry` 값은 KSIC 코드 문자열이다(라벨 없음). 표시용 산업명이 필요하면 D9가 `conditions.industry`를 사용.
