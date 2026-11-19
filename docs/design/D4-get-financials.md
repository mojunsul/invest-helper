# D4 get_financials (`backend/tools/financials.py`)

기준: D0 §1·2·4·6-8, D1 §1·3, D2 §3·8, D3(형식·패턴), D6(소비자), DESIGN §3 '실호출 검증 결과'·§5, `scripts/check_apis.py`·`scripts/out/fin.json`(삼성전자 1건, 데이터로만 참조). 의사코드이며 구현 아님. D0/D1/D2 계약은 변경하지 않는다.

## 1. 목적·범위

종목코드 하나의 **연간 재무 원천값**(매출·영업이익·순이익·자산·자본·부채비율)과 **시가총액**을 돌려준다. 지표 계산(ROE·PER 등)·충족 판단은 하지 않는다(D6/D8). 외부 호출은 금융위 재무정보 API(필수) + 주가 API(시가총액, 부가). DART 호출 없음(crno는 D2 캐시, 요청 경로는 DART 키 무관).

## 2. 확인된 사실 vs 가정

| 구분 | 내용 |
|---|---|
| 실호출 확인(삼성전자, 2026-11-18) | `GET https://apis.data.go.kr/1160100/service/GetFinaStatInfoService_V2/getSummFinaStat_V2` 파라미터 `serviceKey, resultType=json, crno, bizYear, numOfRows, pageNo`. 응답 `response.header.resultCode="00"`, `body.items.item[]`에 연결(`fnclDcd="110"`)·별도(`"120"`) 두 행. 필드 `basDt, crno, bizYear, fnclDcd, enpSaleAmt(매출), enpBzopPft(영업이익), iclsPalClcAmt, enpCrtmNpf(순이익), enpTastAmt(자산), enpTdbtAmt(부채), enpTcptAmt(자본총계), enpCptlAmt, fnclDebtRto(부채비율, 소수 문자열), curCd`. 값은 숫자 문자열(원 단위). 연간만 제공, 2025년분까지 조회됨 |
| 주가 API(DESIGN §3) | `.../GetStockSecuritiesInfoService_V2/getStockPriceInfo_V2`, 필드 `srtnCd, mrktTotAmt` 등. `likeItmsNm` 검색은 부분일치 → `srtnCd`로 정확 매칭 |
| **가정** | **H1** `bizYear` 결정 규칙: `Y = (base_date 또는 오늘).year - 1`(사업보고서는 익년 확정). 1~3월 직후 등 Y 데이터가 아직 없을 수 있어 H2로 보완. **H2** Y가 비면 `Y-1` 한 번만 더 시도(그 이상 소급 안 함). **H3** 주가 API가 `basDt` + `likeSrtnCd`(종목코드 필터)를 지원(D3 §9가 대안으로 언급한 파라미터; 미실호출). 불가하면 `likeItmsNm`+D2 이름(`resolve_stock(code)` 결과 name)으로 대체하고 `srtnCd` 정확 매칭. **H4** 재무 API 응답 변형: 0건이면 `items`가 `""`/누락, 단건이면 `item`이 dict(D3 G3와 동일 취급). **H5** 한도초과/키오류는 HTTP 200 `resultCode`로 오며 코드값은 D3와 같은 가정값(키 30·31·32, 한도 22; 미확인). **H6** 값 필드가 빈 문자열·누락일 수 있다(→ None). **H7** 금융업은 재무 필드 구성이 다를 수 있다(→ None 처리, §3-6) |

## 3. 입출력

```python
class FinInput(BaseModel):                 # extra="forbid" 금지(D1)
    stock_code: str = Field(pattern=r"^[0-9A-Z]{6}$")                       # D2 A7과 동일
    base_date: str | None = Field(default=None, pattern=r"^\d{4}-\d{2}-\d{2}$")   # None=오늘 기준. 형식만 검증(실제 달력 오류는 INVALID_INPUT)
```
- `@register(name="get_financials", requires_keys=("DATA_GO_KR_KEY",), timeout_s=<임의 상향, §8>)`. DART 키는 선언하지 않는다(D2 운영 모델). 설명문(Agent 노출)은 DESIGN §3 표 그대로: "후보 종목의 PER·PBR·ROE·부채비율이 필요할 때 종목코드 하나로 조회한다". 설명에 내부 변환(crno)은 넣지 않는다.
- **출력** `ok(data, requested=f"bizYear={Y}", actual=f"FY{Y_actual}", estimated=...)`:
```python
data = {"code": str, "fiscal_period": "FY2025",          # = meta.actual_basis (연간 단일)
        "revenue": int|None, "op_profit": int|None, "net_income": int|None,
        "assets": int|None, "equity": int|None,           # 원 단위. 정수 문자열→int, 소수→float
        "debt_ratio": float|None,                          # API fnclDebtRto 그대로. 없으면 None(계산 대체 안 함)
        "mkt_cap": int|None}                               # 주가 API mrktTotAmt(원). 조회 실패·없음이면 None
```
  키 7+2개는 DESIGN §3과 동일. D6 `raw`가 그대로 읽는다. `debt_ratio`가 None일 수 있음을 D6 요청 3에 대한 답으로 확정한다(D6은 `NO_FINANCIALS` 사유로 처리).
- **meta 규칙(D0 §1)**: `requested_basis=f"bizYear={Y}"`(H1), `actual_basis=f"FY{채택한 bizYear}"`. **정규화**: 각 문자열에서 첫 4자리 숫자(연도)를 추출해 비교, 다르면 `estimated=True`(= H2 소급으로 Y-1을 쓴 경우). 시가총액의 기준일은 estimated와 무관(연도 기준만 본다). 실패 시 `ToolMeta()` 기본값(D1).
- 시가총액 기준일은 data·meta에 노출하지 않는다(최근 영업일 종가 기준 시총, §4-5).

## 4. 처리 순서

1. 입력 검증은 D1 래퍼(INVALID_INPUT). `base_date` 달력 오류(`strptime` 실패)도 `ToolFailure("INVALID_INPUT", "입력값이 올바르지 않습니다.")`.
2. **crno 조회**: `crno = await asyncio.to_thread(get_crno, stock_code)`. `IdentityError`: `NO_RESULT` → `fail("NO_FINANCIALS", "재무 정보를 찾을 수 없습니다.")`(D2 §8 위임 그대로), 그 외(CACHE_NOT_READY=API_ERROR, INVALID_INPUT 등) → `ToolFailure(e.code, e.message)`(D2 §3 1:1).
3. **재무 조회**: `Y` 산출(H1). `getSummFinaStat_V2(crno, bizYear=Y, numOfRows=10, pageNo=1)`. 행이 없으면 `Y-1` 한 번 더(H2). 둘 다 없으면 `NO_FINANCIALS`("재무 정보를 찾을 수 없습니다.").
4. **행 선택**: `fnclDcd=="110"` 행 우선, 없으면 `"120"`, 둘 다 없으면 NO_FINANCIALS(DESIGN §3). 응답의 `bizYear`가 아니라 **요청한** 연도를 `actual`로 쓴다(D3 §5와 동일 방침). 다른 `crno`·`bizYear` 행은 무시.
5. **값 변환**: 각 필드를 숫자 파싱, 실패·빈 값은 None(H6). 매출·영업이익·순이익·자산·자본 5개가 **모두** None이면 NO_FINANCIALS(내용 없는 행). 일부만 None이면 ok로 돌려주고 D6가 지표별 사유 처리(D6 공통 우선순위 ①). 음수는 그대로(적자·자본잠식은 D6 판단).
6. **시가총액(부가)**: 주가 API를 `basDt=base_date 또는 오늘`부터 **과거로 최대 `LOOKBACK_DAYS`일** 1일씩 `likeSrtnCd=stock_code`(H3)로 조회, `srtnCd==stock_code` 행이 처음 나온 날의 `mrktTotAmt`를 정수로 채택(>0만, 아니면 None). 이 단계의 모든 실패(`ToolFailure`·타임아웃 제외 예외)는 **삼키고 `mkt_cap=None`**(warning 로그: 타입명·code만). 재무값이 이미 확보됐으므로 부분 성공으로 처리하며, 시총 None은 screen에서 D8이 후보 행 `mkt_cap`으로 채우고(D6 §raw), lookup에서는 D6가 `NO_MARKET_CAP`으로 안내한다.
   - 래퍼 전체 타임아웃·취소(D1 §3-3, §3-5)는 삼키지 않는다(삼키는 예외 범위는 `ToolFailure`·`httpx.HTTPError`로 한정, §8).
7. `ok(data, requested, actual, estimated)`. 키·요청 URL은 data에 넣지 않는다(D1 §3 점검표).

호출 수: 재무 1~2회 + 주가 1~7회(보통 1회). 전부 순차(병렬화 안 함, 단순성).

## 5. 실패 매핑

| 입력 | 코드 | 재계획·집계(D0) |
|---|---|---|
| 키 빈 문자열(래퍼) / 본문 `resultCode`가 키 오류류(H5) | MISSING_API_KEY | fatal → `status=error` |
| 입력 형식·`base_date` 달력 오류 | INVALID_INPUT | 종목 UNDECIDABLE(`FETCH_FAILED`), 재계획 트리거 아님 |
| 본문 한도초과류(H5)·HTTP 429 | RATE_LIMIT | 〃 |
| httpx Timeout·래퍼 타임아웃 | TIMEOUT | 〃 |
| `resultCode` 비정상·알 수 없음, HTTP/연결/JSON/구조 오류(재무 API) | API_ERROR | 〃 |
| `IdentityError` 빈 캐시/미완료(CACHE_NOT_READY 등) | API_ERROR(D2 고정 문구) | 〃 |
| crno 없음(`get_crno` NO_RESULT), Y·Y-1 모두 행 없음, 연결·별도 모두 없음, 핵심 5값 전부 None | **NO_FINANCIALS** | 종목 UNDECIDABLE(`NO_FINANCIALS`), 재계획 트리거 아님, **`tool_cache`에 캐시**(D1 `is_cacheable`) |
| 주가 API 실패 | (실패 아님) `mkt_cap=None` | — |
- `ok=False`는 예외를 던지지 않는 규약(D1). 본문은 `fail()`/`ToolFailure`만 사용, 호출 예외는 `raise ToolFailure(...) from None`, 로그는 타입명·status·건수·종목코드만(키가 쿼리스트링에 실림, D1 NF3002). `print` 금지. URL·원문 본문은 로그·메시지에 넣지 않는다.
- lookup intent에서 `ok=False`(NO_FINANCIALS 포함)는 `status=error`(D0 §6-8). screen에서는 종목 단위 UNDECIDABLE이며 나머지 종목은 계속(DESIGN §10 시나리오 3).
- NO_FINANCIALS 외 `ok=False`는 캐시되지 않아 재계획 때 재호출된다(D0 §1).

## 6. 이벤트/로그 연계

- D4는 bus를 직접 쓰지 않는다. `tool_call`/`tool_result` 발행·duration·cache_hit은 D8(D1 §5). D8 `summary`용으로 `data.fiscal_period`·`meta.estimated`·(실패 시) `error.code`만 있으면 충분(예: "FY2025, 추정=아니오"). 원천값 전체를 summary에 넣지 않는다.
- 로깅: `logger.info`로 종목코드·채택 연도·사용 행(연결/별도)·시총 채택 여부(bool)만. 키·URL·응답 본문 금지.
- 재계획 시 같은 `(stock_code, base_date)` 입력은 캐시 키가 같아 재호출되지 않는다(DESIGN §4). D8은 후보마다 `base_date=conditions.base_date`로 호출(후보 행 `actual_basis`를 넘기지 않음; 입력이 같아야 캐시 적중).

## 7. 테스트 관점(실호출 없음)

- 분리: 모듈 수준 `async def _fetch_fin(crno, biz_year) -> dict`, `async def _fetch_price(basDt, stock_code) -> dict`, `_today() -> date`를 monkeypatch/`respx`로 대체. `get_crno`는 모듈 전역 참조로 monkeypatch(D1 §6 패턴).
- 점검 항목: 정상(연결 우선·`fiscal_period`/`meta` 값·타입·`estimated=False`), 연결 없음→별도 사용, Y 비어 Y-1 사용→`estimated=True`·`actual_basis=FY{Y-1}`, 두 해 모두 비면 NO_FINANCIALS, 핵심 5값 전부 빈 문자열→NO_FINANCIALS, 일부 None 통과, `fnclDebtRto` 없음→`debt_ratio=None`, 단건 dict·0건 `""` 응답 변형, `base_date`로 Y 결정(예: 2025-06-30→Y=2024), 시총 정상/주가 실패(→None, 전체 ok)/휴장 후진/`srtnCd` 불일치 행 무시, `get_crno` NO_RESULT→NO_FINANCIALS, 기타 `IdentityError` 1:1 변환, `resultCode`·HTTP 429·Timeout 매핑, 잘못된 stock_code·`base_date` 형식/달력 오류→INVALID_INPUT, `is_cacheable`(NO_FINANCIALS 캐시 대상·API_ERROR 비대상), 실패 메시지·로그에 키 미포함.

## 8. 구현 시 확인 항목

- **실호출 확인**: H3(`likeSrtnCd` 지원·주가 응답에 `srtnCd` 포함), H4·H6(0건/단건/빈 값 응답 형태), H5(키 오류·한도 `resultCode` 값; D3와 같은 가정값), `getSummFinaStat_V2` 호출 한도, 금융업(은행·보험)의 필드 구성(H7; DESIGN §10 시나리오 2가 은행), 최근 상장·재무 미공시 종목의 응답, `fnclDebtRto`가 연결·별도 행 모두에 있는지, `enpTastAmt`·`enpTcptAmt` 음수/0 표기.
- **임의 상수(근거 없음, 측정 후 조정)**: `register(timeout_s=…)`(재무 최대 2회 + 주가 최대 `LOOKBACK_DAYS`회 순차라 기본 10초로 부족할 수 있음, D3와 같은 방식으로 상향, D1 가정 4), `LOOKBACK_DAYS`(D3와 동일 값 사용 권장, 구현 시 한 곳으로 공유할지 판단), H1의 `Y = year-1`·H2의 1회 소급.
- **엣지**: 본문 내 `except Exception`이 래퍼 타임아웃(`asyncio.TimeoutError`/`CancelledError`)을 가로채지 않게 한다(D1 §3-5와 충돌 없게; 시총 단계 `try/except`는 `ToolFailure`·`httpx.HTTPError` 등으로 한정 권장). 같은 종목에 다른 `crno`가 매핑되는 경우, 연결 행은 있으나 값이 거의 None인 경우(별도로 대체하지 않음, 단순화), 시총 기준일(최근 영업일)과 재무 연도 불일치(의도된 단순화), D3 `_parse`와 같은 `resultCode` 해석 중복(공용 헬퍼 추출은 후속 정리).
- D2 `get_crno`는 crno가 NULL·pending·failed·none인 상장사를 모두 `NO_RESULT`로 준다(D2 §3). 따라서 **D2 build 미완료 종목의 일시 누락이 NO_FINANCIALS로 보일 수 있음**(구분 불가; 감수).

## 상위 계약에 대한 요청 (제안만)

1. **DESIGN §3 line 60 문구**: "재무는 확정 분기 기준"은 실호출 결과(연간만 제공, 분기 없음, DESIGN §3 line 66)와 어긋난다. "확정 사업연도 기준, `actual_basis`가 `requested_basis`와 연도가 다르면 `estimated=true`"로 정정 제안. D4는 연간 기준(H1·H2)으로 설계했다.
2. **D0 §1 `estimated` 정규화**: D4가 "문자열에서 첫 4자리 연도 추출 후 비교"로 확정했음을 D0 §1에 한 줄 반영(계약 변경 아님, 위임 확정 기록).
3. **D1 §2 예시**의 `requires_keys=("DATA_GO_KR_KEY","DART_API_KEY")`는 D4가 `DATA_GO_KR_KEY`만 선언하므로 예시 정정 제안(D2 운영 모델상 요청 경로는 DART 키 무관).
4. **시총 기준일·재무 구분(연결/별도) 미노출**: DESIGN §3 출력에 없어 노출하지 않았다. 응답에 필요하면 DESIGN·D6 MetricRow 확장이 필요(범위 확대이므로 사용자 판단, 기본은 미노출).
5. **D6 요청 3 답변**: `debt_ratio`는 API 값만이며 None 가능(§3). D6 문서에 반영 요청.
