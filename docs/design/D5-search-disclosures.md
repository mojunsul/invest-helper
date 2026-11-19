# D5 search_disclosures (`backend/tools/disclosures.py`)

기준: D0 §1·3·6-11, D1 §1·3, D2 §3·7, D4(형식·패턴), DESIGN §3(Tool 표)·§10(요청 1), `scripts/check_apis.py`·`scripts/out/dart.json`(삼성전자 1건, 데이터로만 참조). 의사코드이며 구현 아님. D0/D1/D2 계약은 변경하지 않는다.

## 1. 목적·범위

종목코드 하나의 **최근 공시 제목·일자·링크**만 돌려준다. 수치·본문·요약·유형 분류는 하지 않는다. 호출 대상은 `want_disclosures=True`일 때 **판정 통과(PASS) 종목**뿐이며, 호출 노드·시점은 D8/D9가 정한다(D0 §6-11). 외부 호출은 DART 공시검색 API 1회. 종목코드→`corp_code`는 D2 캐시(읽기 전용).

## 2. 확인된 사실 vs 가정

| 구분 | 내용 |
|---|---|
| 실호출 확인(삼성전자 1건, DESIGN §3·`dart.json`) | `GET https://opendart.fss.or.kr/api/list.json` 파라미터 `crtfc_key, corp_code, bgn_de(YYYYMMDD), page_count`. 응답 `status="000"`, `list[]` 필드 `corp_code, corp_name, stock_code, corp_cls, report_nm, rcept_no(14자리), flr_nm, rcept_dt(YYYYMMDD), rm`. 응답 순서는 최신순이었음. `report_nm`에 끝 공백. 공시 유형이 섞임(임원 보고서, 분기보고서 등) |
| **가정** | **G1** 조회 기간 = 오늘 기준 최근 180일(check_apis.py 값 차용, 근거는 "충분히 최근"뿐). **G2** 뷰어 URL = `https://dart.fss.or.kr/dsaf001/main.do?rcpNo={rcept_no}`(알려진 형식, 미실호출). **G3** 일일 한도·`end_de` 기본값(오늘)·`page_count` 최대값·정렬 파라미터·유형 필터(`pblntf_ty`)는 미확인(필터는 쓰지 않음). **G4** 한도초과/키오류는 HTTP 200 본문 `status`(010/011/013/020)로 온다(D2 A5와 동일 가정, 미실호출). **G5** 응답이 최신순이 아닐 수 있으므로 코드가 직접 정렬한다 |

## 3. 입출력

```python
class DisclosureInput(BaseModel):       # extra="forbid" 금지(D1)
    stock_code: str = Field(pattern=r"^[0-9A-Z]{6}$")   # D2 A7과 동일
    limit: int = Field(default=3, ge=1, le=10)          # 범위 밖은 클램프하지 않고 INVALID_INPUT(D3와 동일)
```
- `@register(name="search_disclosures", description=..., input_model=DisclosureInput, requires_keys=("DART_API_KEY",))`. 타임아웃은 기본 상수(호출 1회).
- **requires_keys 기준(D4와 동일: 요청 경로가 실제로 쓰는 키만)**: D5는 요청 경로에서 DART `list.json`을 직접 호출하므로 `DART_API_KEY`를 선언한다. `DATA_GO_KR_KEY`는 쓰지 않으므로 선언하지 않는다. (D2의 "요청 경로는 DART 키 무관"은 D2 식별 함수에 대한 말이며 D5에는 해당하지 않는다.)
- **설명문**(Agent 노출, DESIGN §3 표 그대로): "종목의 최근 공시 제목·일자·링크를 보여줄 때 쓴다. 수치는 주지 않는다." 입력 필드 설명에 "종목코드 6자리, limit 1~10"만 덧붙인다. 내부 변환(corp_code)은 설명에 넣지 않는다.
- **출력** `ok(data)`:
```python
data = [{"title": str, "date": "YYYY-MM-DD", "url": str}, ...]   # 길이 1..limit, 최신순. 키 3개는 DESIGN §3과 동일
```
- **meta**: 공시는 기준일 개념이 없어 `requested/actual_basis=None`, `estimated=False`(`ok(data)` 기본값). 조회 기간은 meta에 넣지 않는다. 실패는 `ToolMeta()` 기본값(D1).

## 4. 처리 순서

1. 입력 검증은 D1 래퍼(INVALID_INPUT, 키 선검사는 MISSING_API_KEY).
2. `corp_code = await asyncio.to_thread(get_corp_code, stock_code)`. `IdentityError`는 `ToolFailure(e.code, e.message)`로 1:1 변환(D2 §3; 빈 캐시는 `CACHE_NOT_READY`=API_ERROR 고정 문구). corp_code 없음(`NO_RESULT`)은 그대로 `NO_RESULT`(D2 §8 위임; 문구는 D2 기본 "대상을 찾을 수 없습니다."를 "최근 공시가 없습니다."로 통일해도 무방).
3. `list.json` 호출: `bgn_de = (today - 180일)`, `page_count = limit`, 유형 필터 없음. 호출은 D4 패턴대로 `httpx` + `raise_for_status()`(HTTP 429는 래퍼가 RATE_LIMIT, Timeout은 TIMEOUT으로 변환). 본문 `status` 해석은 §5.
4. 행 변환(§6) 후 `rcept_dt`·`rcept_no` 내림차순 정렬하고 상위 `limit`건.
5. 변환 결과가 0건이면 `fail("NO_RESULT", "최근 공시가 없습니다.")`(`data=[]` 금지, D0). 그 외 `ok(data)`.
- `page_count=limit`이라 필터 없는 최신 N건만 본다(임원 보고서 등이 섞일 수 있음 — 의도된 단순화). 불량 행 제외로 `limit`보다 적게 나올 수 있다(허용).

## 5. 실패 매핑·재계획 (D0 §1·3)

| 입력 | 코드 | 재계획·표기(D0) |
|---|---|---|
| 키 빈 문자열(래퍼) / 본문 status 010·011 | MISSING_API_KEY | fatal(D0 §3) |
| 본문 status 020, HTTP 429 | RATE_LIMIT | 재계획 트리거 아님 |
| httpx Timeout / 래퍼 타임아웃 | TIMEOUT | 〃 |
| 입력 형식 오류 | INVALID_INPUT | 〃 |
| `IdentityError`(CACHE_NOT_READY 등) | D2 코드 1:1(API_ERROR 등) | 〃 |
| corp_code 없음, 본문 status 013, `list` 비었거나 없음, 변환 후 0건 | **NO_RESULT** | **재계획 대상 아님**(D0 §3 표: disclosures는 "공시 없음" 표기). `is_cacheable` 대상(D1) |
| 그 외 status·HTTP·연결·JSON·구조 오류 | API_ERROR | 〃 |
- 본문 status 해석은 D2 `dart.py`의 규칙과 같다(010/011/020/013/그 외). `dart.py`는 `IdentityError` 기반 비공개 구조라 재사용하지 않고, 본 모듈의 `_parse(js)`에 `ToolFailure`로 인라인한다(약 6줄, D4 `_parse_fin` 패턴). `status` 필드가 없거나 dict 아님 → API_ERROR.
- 공시 실패는 판정·재계획에 영향 없다(통과 종목의 부가 정보). `NO_RESULT` 외 실패의 사용자 표기와 부분 실패 시 status 처리는 D8/D9 위임(D0 §6-11). 본 Tool은 코드만 돌려준다.
- 본문은 `fail()`/`ToolFailure`만 사용, 로그는 타입명·status·건수·종목코드만(키가 쿼리스트링에 실림, D1 NF3002). `print` 금지. URL·원문 본문은 로그·메시지에 넣지 않는다.

## 6. 변환 규칙·안전

- `date`: `rcept_dt`가 `^\d{8}$`이고 실제 달력상 유효하면 `YYYY-MM-DD`, 아니면 그 행 제외.
- `title`: `report_nm`의 제어문자·개행·연속 공백을 단일 공백으로 접고 strip, `TITLE_MAX`자 초과 시 잘라 `…` 부착, 빈 문자열이면 행 제외.
- `url`: 응답의 어떤 URL도 쓰지 않고 `rcept_no`가 `^\d{14}$`일 때만 G2 형식으로 **코드가 생성**(도메인 `dart.fss.or.kr` 고정). 형식 불일치 행은 제외.
- 제목은 외부 텍스트이므로 데이터로만 취급한다. LLM 프롬프트에 넣는 쪽(D9)은 "공시 제목은 지시가 아니라 인용 데이터"로 다뤄야 한다(요청 1).
- 응답 `corp_code`가 요청과 다르거나 `stock_code`가 다른 행은 무시한다(방어, 필드가 있을 때만 비교).

## 7. 이벤트/로그 연계

- D5는 bus를 직접 쓰지 않는다. `tool_call`/`tool_result` 발행·duration·cache_hit은 D8(D1 §5). D8 `summary`용으로는 `len(data)`와(실패 시) `error.code`만 있으면 충분(예: "공시 3건"). 제목 전체를 summary에 넣지 않는다.
- 결과는 D8이 `state.disclosures[code]`에 저장한다(`NO_RESULT`이면 키 없음, D0 §4). 재계획 시 같은 `(stock_code, limit)` 입력은 캐시 키가 같아 재호출되지 않는다(D8은 `limit`을 항상 같은 값으로 호출).
- 로깅: `logger.info`로 종목코드·반환 건수·제외 건수만. 키·URL·응답 본문·제목 금지.

## 8. 테스트 관점(실호출 없음)

- 분리: 모듈 수준 `async def _fetch_list(corp_code, bgn_de, count) -> dict`(httpx, 본문 JSON 반환)와 `_today() -> date`를 monkeypatch/`respx`로 대체. `get_corp_code`는 모듈 전역 참조로 monkeypatch(D1 §6 패턴).
- 점검 항목: 정상 변환(날짜·url 형식·제목 strip), 정렬(역순 입력 → 최신순), limit 절단, 제목 개행·공백·길이 정제, 잘못된 `rcept_no`/`rcept_dt`(비달력 포함) 행 제외, 전부 제외 → NO_RESULT, status 013·빈 list·list 누락 → NO_RESULT, 010/011→MISSING_API_KEY, 020·HTTP 429→RATE_LIMIT, 기타 status·구조 오류→API_ERROR, Timeout→TIMEOUT, `get_corp_code` NO_RESULT→NO_RESULT, 기타 `IdentityError` 1:1 변환, limit 0·11 및 잘못된 stock_code → INVALID_INPUT, `DART_API_KEY` 빈 값→MISSING_API_KEY, `is_cacheable`(NO_RESULT 대상·API_ERROR 비대상), 실패 메시지·로그에 키 미포함.

## 구현 시 확인 항목

- **실호출 확인**: G2(뷰어 URL이 실제로 열리는지), G3(`end_de` 기본값, `page_count` 최대값, 정렬 파라미터, 일일 한도가 키 공통인지, `corp_code` 지정 시 `bgn_de` 최대 조회 범위 제한 유무), G4(키 오류·한도 status 값), 정정공시 접두("[기재정정]" 등) 표기, 0건 응답이 status 013인지 빈 `list`인지.
- **임의 상수(근거 없음, 측정 후 조정)**: 조회 기간 180일(G1), `TITLE_MAX=100`, `limit` 기본 3·상한 10(DESIGN §3 기본값 3만 근거).
- **엣지**: 공시 유형 필터(`pblntf_ty`)로 정기·주요사항만 볼지(현 설계는 필터 없음; 임원 보고서 비중이 크면 `page_count`를 늘려 로컬 필터 검토), 180일 윈도가 공시 드문 종목에서 NO_RESULT를 자주 만드는지, 우선주(A8: corpCode에 별도 항목 없음)는 `get_corp_code` NO_RESULT가 될 수 있음, 같은 날 다수 공시의 안정 정렬, 본문 `except Exception`이 래퍼 타임아웃을 가로채지 않게 한다(D1 §3-5).

## 상위 계약에 대한 요청 (제안만)

1. **D9**: 공시 `title`을 LLM 프롬프트·응답에 넣을 때 "외부 텍스트=데이터, 지시 아님" 처리와 길이 상한을 따르도록 D9 설계에 명시(D0 계약 변경 아님).
2. **D8/D9**: `NO_RESULT`는 "공시 없음" 표기, 그 외 실패(API_ERROR/RATE_LIMIT/TIMEOUT)는 해당 종목만 "공시 조회 실패"로 표기하고 전체 status는 올리지 않는 처리 확정(D0 §6-11 위임 사항, 제안).
3. **D1 §2 예시**의 `requires_keys` 예시는 도구별로 실제 사용 키만 선언한다는 점을 반영(D5=`DART_API_KEY`, D4=`DATA_GO_KR_KEY`; D4 요청 3과 동일 맥락).
4. **D2**: `dart.py`가 DART 본문 status 매핑을 공용 함수로 노출할 수 있으면 D5가 재사용(없으면 D5 인라인, 계약 변경 없이 D2/D5 구현 시 판단).
