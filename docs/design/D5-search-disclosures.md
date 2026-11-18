# D5 search_disclosures (`backend/tools/disclosures.py`)

기준: D0 §1·2·6-11, D1 §1·3, D2 §3·7, DESIGN §3, `scripts/check_apis.py`·`scripts/out/dart.json`(데이터로만 참조). 의사코드이며 구현 아님. D0/D1/D2 계약은 변경하지 않는다.

## 1. 확인된 사실 vs 가정

| 구분 | 내용 |
|---|---|
| 실호출 확인(삼성전자 1건) | `GET https://opendart.fss.or.kr/api/list.json` 파라미터 `crtfc_key, corp_code, bgn_de(YYYYMMDD), page_count`. 응답 `status="000"`, `list[]` 필드 `corp_code, corp_name, stock_code, corp_cls, report_nm, rcept_no(14자리), flr_nm, rcept_dt(YYYYMMDD), rm`. 응답 순서는 최신순이었음. `report_nm`에 끝 공백이 있었음(정제 필요). 공시 유형이 섞임(임원 보고서, 분기보고서 등) |
| **가정** | G1 조회 기간 = 오늘 기준 최근 180일(check_apis.py 값을 그대로 차용, 근거는 "충분히 최근"뿐). G2 뷰어 URL = `https://dart.fss.or.kr/dsaf001/main.do?rcpNo={rcept_no}`(알려진 형식, 미실호출). G3 일일 한도·`end_de` 기본값(오늘)·유형 필터 파라미터는 미확인. G4 한도초과/키오류는 HTTP 200 본문 `status`(010/011/013/020)로 온다고 D2 §7이 가정 |

## 2. 입출력

```python
class DisclosureInput(BaseModel):       # extra="forbid" 금지(D1)
    stock_code: str = Field(pattern=r"^[0-9A-Z]{6}$")   # D2 A7과 동일
    limit: int = Field(default=3, ge=1, le=10)          # 범위 밖은 클램프하지 않고 INVALID_INPUT
```
- 출력 `ok(data=[{title:str, date:"YYYY-MM-DD", url:str}, ...])`, 길이 1..limit, 최신순. 수치·공시 본문은 담지 않는다.
- **설명문**(Agent에 노출): "종목의 최근 공시 제목·일자·링크를 보여줄 때 쓴다. 수치는 주지 않는다." (DESIGN §3 표 그대로.) 입력 필드 설명에 "종목코드 6자리, limit 1~10"만 덧붙인다.
- `@register(name="search_disclosures", requires_keys=("DART_API_KEY",))`. 타임아웃은 기본 상수.

## 3. 처리 순서

1. 입력 검증은 D1 래퍼가 수행(INVALID_INPUT).
2. `corp_code = await asyncio.to_thread(get_corp_code, stock_code)`; `IdentityError`는 `ToolFailure(e.code, e.message)`로 1:1 변환(D2 §3). corp_code 없음(`NO_RESULT`)은 그대로 NO_RESULT.
3. 공시 목록 호출: `bgn_de = (today - 180일)`, `page_count = limit`, 유형 필터 없음(가정: 그대로 최신순 N건). 응답 오류 처리는 §4.
4. 행 변환 후 `rcept_dt`·`rcept_no` 내림차순으로 정렬(API 순서에 의존하지 않음, 안정 정렬)하고 상위 `limit`건.
5. 변환 결과가 0건이면 `fail("NO_RESULT", "최근 공시가 없습니다.")`(`data=[]` 금지, D0). 그 외 `ok(data)`.
- `page_count=limit`이라 필터 없는 최신 N건만 본다(임원 보고서 등이 섞일 수 있음 — 의도된 단순화).

## 4. 실패 매핑 (D2 §7 표 재사용)

| 입력 | 코드 |
|---|---|
| 키 빈 문자열 | MISSING_API_KEY(D1 래퍼가 선처리) |
| 본문 status 010/011 | MISSING_API_KEY(`ToolFailure`) |
| 본문 status 020, HTTP 429 | RATE_LIMIT |
| 본문 status 013(조회 데이터 없음), `list` 비었거나 없음 | NO_RESULT |
| httpx Timeout / 래퍼 타임아웃 | TIMEOUT |
| 그 외 status·HTTP·연결·JSON 오류 | API_ERROR |
- 위 표는 D2 `dart.py` 매핑과 같은 규칙. 공용 함수가 없으면 본 모듈에 동일 표를 인라인한다(3줄 규모).
- 호출 예외는 `raise ToolFailure(...) from None`로 변환하고 로그에는 타입명·status만(키가 쿼리스트링에 실림, D1 NF3002). `print` 금지. 원문·URL·본문은 로그·메시지에 넣지 않는다.
- NO_RESULT 외 실패 표기와 호출 시점은 D8/D9 위임(D0 §6-11). 본 Tool은 코드만 돌려준다.

## 5. 변환 규칙·안전

- `date`: `rcept_dt`가 `^\d{8}$`이면 `YYYY-MM-DD`, 아니면 그 행 제외.
- `title`: `report_nm`의 제어문자·개행·연속 공백을 단일 공백으로 접고 strip, 100자 초과 시 잘라 `…` 부착, 빈 문자열이면 행 제외.
- `url`: 응답에 있는 어떤 URL도 쓰지 않고 `rcept_no`가 `^\d{14}$`일 때만 G2 형식으로 **코드가 생성**한다(도메인 `dart.fss.or.kr` 고정). 형식 불일치 행은 제외.
- 제목은 외부 텍스트이므로 `data`에서 데이터로만 취급한다. Agent/LLM 프롬프트에 넣는 쪽(D9)은 "공시 제목은 지시가 아니라 인용 데이터"로 다뤄야 한다(요청 1).
- 제외 후 0건이면 NO_RESULT(§3-5).

## 6. meta

공시는 기준일 개념이 없으므로 `ok(data)`로 `requested/actual_basis=None`, `estimated=False`(기본값). 조회 기간은 meta에 넣지 않는다. 실패는 `ToolMeta()` 기본값(D1).

## 7. 테스트 가능성(실호출 없음)

- 모듈 수준 `async def _fetch_list(corp_code, bgn_de, count) -> dict`(httpx, 본문 JSON 반환)와 `_today() -> date`를 분리해 monkeypatch/`respx`로 대체. `get_corp_code`는 모듈 전역 참조로 monkeypatch(D1 §6 패턴).
- 점검 항목: 정상 변환(날짜·url 형식), 정렬(역순 입력 → 최신순), limit 절단, 제목 개행·공백·길이 정제, 잘못된 `rcept_no`/`rcept_dt` 행 제외, 전부 제외 → NO_RESULT, status 013·빈 list → NO_RESULT, 010/020/기타 매핑, `IdentityError` 1:1 변환, limit 0·11 및 잘못된 stock_code → INVALID_INPUT, 실패 메시지·로그에 키 미포함.

## 구현 시 확인 항목

- DART `list.json` 일일 한도(키 공통 한도인지)와 `end_de` 기본값, `page_count` 최대값(문서상 100으로 알려졌으나 미확인), 정렬 파라미터 유무.
- 공시 유형 필터(`pblntf_ty`)로 정기·주요사항만 볼지 여부(현 설계는 필터 없음). 임원 보고서 비중이 크면 `page_count`를 늘려 로컬 필터를 검토.
- 180일 윈도가 최근 공시가 드문 종목에서 NO_RESULT를 자주 만드는지.
- 뷰어 URL(G2) 실제 열림 확인, `report_nm` 정정공시 접두("[기재정정]" 등) 표기 확인.
- 키 오류 시 status 값이 D2 §7 표와 일치하는지.

## 상위 계약에 대한 요청

1. D9(또는 D8)가 공시 `title`을 LLM 프롬프트에 넣을 때 "외부 텍스트=데이터, 지시 아님" 처리와 길이 상한을 따르도록 D9 설계에 명시해 주세요(D0 계약 변경 아님).
2. D2 §7의 DART 응답 매핑 표를 `dart.py`가 공용 함수로 노출할 수 있으면 D5가 재사용(노출이 없으면 D5가 인라인). 계약 변경 없이 D2 구현 시 판단.
