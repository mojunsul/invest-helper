# invest-helper

산업·지표 조건으로 국내 상장 종목을 탐색·판정하고 근거와 함께 보여주는 투자 보조 챗봇(FastAPI + LangGraph).

## 1. 설치

개발 환경: Python 3.12.10.

```
python -m venv .venv
source .venv/Scripts/activate   # Git Bash
.venv\Scripts\Activate.ps1      # PowerShell (macOS/Linux: source .venv/bin/activate)
python -m pip install -r requirements.txt
```

활성화하지 않고 쓰려면 `.venv/Scripts/python.exe -m ...` 로 직접 호출한다. 이후 절의 `python` 명령(테스트·서버 기동)은 모두 활성화된 `.venv`에서 실행한다.

## 2. .env 준비

`.env.example`을 `.env`로 복사하고 키를 채운다. `.env`는 커밋하지 않는다(`.gitignore` 등록됨).

기존 `.env`에 `GMS_BASE_URL=https://gms.ssafy.io/gmsapi/api.openai.com/v1`·`GMS_MODEL=gpt-4o-mini`가 남아 있으면 두 줄을 지우거나(코드 기본값 사용) 아래로 고친다. 그대로 두면 LLM 호출이 실패해 의도 분석이 기본 응답으로 떨어진다.

```
GMS_BASE_URL=https://gms.ssafy.io/gmsapi/api.anthropic.com/v1
GMS_MODEL=claude-haiku-5-5
```

| 키 | 용도 | 발급 |
|---|---|---|
| `DATA_GO_KR_KEY` | 금융위 주가·재무 (data.go.kr) | [공공데이터포털](https://www.data.go.kr) 회원가입 → "금융위원회_주식시세정보"·"금융위원회_기업 재무정보" 두 API 각각 활용신청(개발계정) → 마이페이지의 **일반 인증키** 복사 |
| `DART_API_KEY` | DART 기업정보·공시 | [OpenDART](https://opendart.fss.or.kr) 회원가입 → 인증키 신청/관리에서 키 발급(회원당 1개) |
| `GMS_API_KEY`, `GMS_BASE_URL`, `GMS_MODEL` | LLM (SSAFY GMS Anthropic 경로, 기본 `claude-haiku-5-5`) | SSAFY 계정으로 GMS 로그인 후 발급(SSAFY 교육생 전용) |
| `REPLAN_MAX` | 재계획 최대 횟수 (1~3, 기본 3) | - |

키는 `.env`에만 넣고 소스·화면·로그에는 남기지 않는다(마스킹).

## 3. 산업 캐시 구축 (앱 실행 전 1회)

```
python -m backend.identity.build            # 중단 후 재실행하면 이어서 진행
python -m backend.identity.build --refresh  # 상장사 목록(corpCode) 갱신
python -m backend.identity.build --force    # 상세 정보 전체 재수집
```

구축은 DART 호출 사이에 약 0.2초씩 쉬어 수천 종목에 15분 안팎 걸린다. 연결 실패가 연속되면(IP 차단 등) 자동으로 멈추며, 잠시 후 또는 다른 네트워크에서 옵션 없이 다시 실행하면 실패·미처리 종목만 이어서 처리한다.

`DART_API_KEY`가 필요하다. 구축하지 않아도 앱은 뜨지만 산업 질의는 "먼저 build를 실행하세요" 안내로 끝난다.

## 4. 산업명 사전 채우기

`backend/identity/data/industry_terms.json`은 현재 빈 배열(`[]`)이라, 숫자 접두를 직접 입력하지 않는 한 모든 산업명이 인식되지 않아 되묻기로 끝난다. 아래 형식으로 채운다.

```json
[{"term": "반도체", "prefixes": ["261"]}, {"term": "은행", "prefixes": ["6412"]}]
```

- `term`: 정규화 후 2자 이상이고 중복 불가. `prefixes`: KSIC 코드 접두 문자열 목록.
- 위 접두값은 형식 예시일 뿐이며 실데이터 확인 전 확정하지 않는다. 구축된 캐시의 `induty_code`와 KSIC 표로 확인한다.

## 5. 실행

**저장소 루트에서** 실행한다.

```
python -m uvicorn backend.main:app --host 127.0.0.1 --port 8000
```

- 채팅: http://127.0.0.1:8000/ , 리포트: http://127.0.0.1:8000/reports.html
- 인증이 없으므로 `0.0.0.0` 바인드 금지.
- 루트에서 실행해야 하는 이유: DB 경로가 상대 경로(`invest_helper.db`, 실행 위치 기준)라서, 다른 위치에서 실행하면 빈 DB가 새로 생겨 구축한 캐시를 찾지 못한다.

## 6. 테스트

```
python -m pytest
```

JS 단위 테스트는 node가 설치된 경우에만 실행되고 없으면 skip된다. 테스트는 네트워크·실제 키를 쓰지 않는다.

## 7. 외부 API 출처·이용조건

확인일 2026-11-20(공식 페이지 열람). 확인하지 못한 항목은 "미확인"이다.

| 출처 | 사용 | 이용조건·호출 한도 | 확인 출처 |
|---|---|---|---|
| data.go.kr 금융위원회_주식시세정보 `GetStockSecuritiesInfoService_V2/getStockPriceInfo_V2` | 후보 시세·시가총액 | 제공: 금융위원회(원천 한국거래소). 이용허락범위 **공공누리 제4유형**(출처표시·상업적 이용금지·변경금지, 제3자 재배포 금지). 개발계정 일 10,000건 / 운영계정 일 100,000건(금융위 FAQ), 초과 시 오류·다음 날 재사용 | [서비스 페이지](https://www.data.go.kr/data/15094808/openapi.do), [금융위 FAQ](https://fsc.go.kr/in060501) |
| data.go.kr 금융위원회_기업 재무정보 `GetFinaStatInfoService_V2/getSummFinaStat_V2` (요약재무제표) | 재무 요약 | 제공: 금융위원회. 서비스 페이지에 "이용허락범위 제한 없음" 표기(공공누리 유형 표기 없음). 일 한도는 위 FAQ의 개발계정 10,000건 기준(서비스 페이지에는 트래픽 수치 미표기 → 개별 값 **미확인**) | [서비스 페이지](https://www.data.go.kr/data/15043459/openapi.do), [금융위 FAQ](https://fsc.go.kr/in060501) |
| OpenDART (`corpCode.xml`, `company.json`, `list.json`, `document.xml`) | 종목·산업 캐시, 공시, 공시 원문 색인 | 약관: 허용량 제한이 있으며 홈페이지에 게시(제10조 ④), 인증키 제3자 이용 금지·1인 1키(제19조), 과도한 접속은 이용 제한 사유(제10조 ⑥). 일일 호출 한도 수치는 공식 페이지에서 **미확인**(이 프로젝트 키에서 일 40,000건 한도가 관측됨) | [이용약관](https://opendart.fss.or.kr/intro/terms.do) |
| SSAFY GMS (Anthropic Messages, `claude-haiku-5-5`) | 의도 분석·요약 | SSAFY 교육생 전용, 크레딧 기반(요청당 소진). 공개 문서가 없어 한도·약관은 **미확인** | 공개 문서 없음 |

우리가 지키는 방식:

- 호스트별 최소 호출 간격: `opendart.fss.or.kr` 0.2초, `apis.data.go.kr` 0.1초(`backend/tools/base.py` `HOST_INTERVAL_S`, 요청 직전 `throttle`). 산업 캐시 구축도 DART 호출 사이 0.2초(`backend/identity/build.py`).
- 연결 실패가 연속 5회면 구축을 중단한다(IP 차단 보호, 이어서 재실행 가능). 429는 `RATE_LIMIT`로 처리하고 재시도 폭주를 하지 않는다.
- 오프라인 색인: 산업 캐시와 공시 원문 색인은 사전에 1회 구축해 로컬에 두고, 질의 시에는 호출하지 않는다.
- 공공누리 제4유형 데이터는 변경·재배포하지 않고 출처(금융위원회/한국거래소, DART)를 표시하며 로컬에서 조회·계산한 근거로만 쓴다.

## 8. 구현 범위

- 제외: 점수화 추천, 산업 전망, 관심 종목 비교, 스케줄러 수집, 세션 영속화, Forward PER 등 예상 실적 기반 지표, F3020/3021/3023.
- 리포트 수정(F3016): 이름(`name`)·메모(`memo`)·재검토 예정일(`review_date`, 다시 볼 날짜), 내 기준값(`target_per` PER 상한·`target_roe` ROE 하한, 사용자 판단 기준)을 고칠 수 있고, 리포트 삭제도 할 수 있다. 재검토 예정일은 저장할 때 지정하거나 나중에 넣고 지울 수 있다. 수정·삭제 결과는 리포트 화면에 바로 반영된다. 핵심 값은 사용자 판단 기준값과 재검토일이며, 저장 지표와 비교해 충족 여부를 함께 보여 준다. 종목·기준일·지표 값·산업 평균·공시 목록은 수정할 수 없다(지표 불변은 의도된 변조 방지). 이 값들은 저장 시점에 조회·계산한 근거를 그대로 남긴 것이라, 손으로 고치면 출처 데이터와 맞지 않는 리포트가 된다. API도 `PATCH /api/reports/{id}`에 `name`·`memo`·`review_date`·`target_per`·`target_roe` 외 필드가 오면 422로 거부한다. 다른 기준일이나 종목이 필요하면 채팅에서 다시 분석한 뒤 새 리포트로 저장한다.
- 공시 원문 검색(`search_filing_text`)의 `kind=local`은 데이터 출처가 로컬 색인이라는 뜻이다(질의 임베딩도 로컬 해시 `local-hash-v1`, 네트워크 호출 없음).
- 세션 메모리는 프로세스 메모리라 서버를 재시작하면 사라진다. 리포트·실행 로그는 SQLite에 남는다.
- 실데이터 검증: 대표 요청 5개 실행과 공시 RAG 비교(D17)는 실제 외부 API로 수행했다(§11). 브라우저 수동 확인은 아직 수행하지 않았다(미확인).
- 라이브러리와 다른 점(명세 §6.5): langchain/openai 대신 httpx로 GMS Anthropic Messages를 직접 호출하고, 에이전트 흐름은 LangGraph를 쓴다.

## 9. 전체 기능 (필수 F3001~F3019 / 심화 F3022)

| 요구 | 기능 | 위치 |
|---|---|---|
| F3001 | Tool 명세 정의·등록(external/local 구분) | `backend/tools/base.py`(`register`, `Tool`), `registry.py` |
| F3002 | 후보 조회 Tool | `backend/tools/stock_candidates.py` |
| F3003 | 상세(재무) 조회 Tool | `backend/tools/financials.py` |
| F3004 | 보조 정보(공시) 검색 Tool | `backend/tools/disclosures.py` |
| F3005 | 요청 분석(산업·기준일·시장·지표 조건) | `backend/agent/nodes/analyze.py` |
| F3006 | 작업 계획 | `backend/agent/nodes/plan.py` |
| F3007 | Tool 호출·결과 통합 | `backend/agent/nodes/execute.py` |
| F3008 | 제약 적합성 판단(코드 계산) | `backend/agent/nodes/judge.py`, `backend/agent/constraints.py` |
| F3009 | 재계획(상한 `REPLAN_MAX`) | `backend/agent/nodes/replan.py`, `judge.py`, `plan.py`, `backend/agent/graph.py`(라우팅) |
| F3010 | 대화 맥락 유지(세션 메모리) | `backend/agent/memory.py` |
| F3011 | 최종 응답·면책 | `backend/agent/nodes/respond.py` |
| F3012 | 실행 이벤트 발행 | `backend/events/bus.py` |
| F3013 | 실행 로그 저장(LLM 미사용) | `backend/events/logger_agent.py` → SQLite |
| F3014 | 로그 조회·삭제 | `backend/api/logs.py`, 채팅 화면 타임라인 |
| F3015 | 리포트 저장·목록·상세(날짜·중복 알림) | `backend/api/reports.py`, `reports.html` |
| F3016 | 리포트 수정(이름·메모·재검토 예정일·내 기준값)·삭제 | 위와 동일 |
| F3017 | FastAPI 서버(대화·리포트·로그) | `backend/main.py`, `backend/api/`(chat·logs·reports) |
| F3018 | 프론트 화면 2개 | `index.html`(채팅 대시보드), `reports.html` |
| F3019 | 실행 단계·Tool 이력·상태·오류 표시 | `index.html` 타임라인 |

심화 **F3022 RAG 검색 Tool** 적용 요약:

- Tool `search_filing_text`(`backend/tools/filing_text.py`, `kind="local"`): 사전 색인한 공시 원문 문단에서 근거를 찾는다. 저장소 chromadb(`data/filing_index`), 임베딩은 로컬 해시 `local-hash-v1`(GMS에 임베딩 모델이 없음, 네트워크 호출 없음).
- 의도 `filing_qa`로 라우팅(`rule_tools`)되어 "사업 내용·가이던스·위험 요인" 같은 서술형 질문에 쓰이며, 응답에 근거 문단·링크를 붙인다. 색인은 `python -m scripts.build_filing_index`(기본 8종목)로 1회 구축한다.
- 필수 기능 대비 비교 결과: [docs/verification/D17-rag-comparison.md](docs/verification/D17-rag-comparison.md).
- 알려진 한계: 해시 임베딩은 2-gram 겹침 기반이라 의미 검색이 약해 관련 없는 섹션이 올라오거나 "확인되지 않습니다"로 끝날 수 있다. 무관 질문 차단은 intent 분류가 맡는다. 의미 임베딩 경로가 열리면 재측정이 필요하다.

## 10. Tool 등록 및 확장 방법

1. `backend/tools/`에 모듈을 추가한다(`registry.load_all()`이 `base`·`registry` 외 모듈을 자동 import하므로 별도 목록 수정 불필요).
2. 입력 pydantic 모델과 `@register(...)` 함수를 쓴다. `kind`는 외부 API 호출이면 `"external"`, 로컬 데이터·계산이면 `"local"`. `description`은 "언제 쓰는지"를 적는다(analyze LLM이 읽는다). `requires_keys`는 `settings` 필드명(없으면 등록 시 오류), `timeout_s`는 생략하면 기본값.
3. 함수는 항상 `ToolResult`를 반환한다. 성공은 `ok(data)`, 실패는 `fail(코드, 한국어 메시지)` 또는 `ToolFailure(코드, 메시지)` 발생. 코드는 `MISSING_API_KEY, INVALID_INPUT, API_ERROR, RATE_LIMIT, TIMEOUT, NO_RESULT, NO_FINANCIALS, NO_INDEX`. 키·원문 오류는 메시지에 넣지 않는다(마스킹됨). 예상 밖 예외·타임아웃·HTTP 오류는 `Tool.run`이 실패 결과로 바꾼다. 외부 호출 직전 `await throttle(URL)`을 부른다.
4. `registry.describe()`가 name·kind·description·input schema를 analyze LLM에 자동 노출한다(D7d). 새 Tool이 쓰일 의도가 있으면 `backend/agent/nodes/plan.py`의 `rule_tools`에 의도→Tool 매핑을 추가하고, 필요하면 plan 단계 입력 구성을 추가한다.
5. `tests/`에 네트워크·실제 키 없이 도는 테스트를 추가한다.

```python
# backend/tools/my_tool.py
from pydantic import BaseModel, Field
from backend.tools.base import fail, ok, register, throttle

URL = "https://example.com/api"

class MyInput(BaseModel):
    stock_code: str = Field(pattern=r"^[0-9A-Z]{6}$", description="종목코드 6자리")

@register(name="my_tool", kind="external",
          description="~를 물을 때 쓴다. ~에는 쓰지 않는다.",
          input_model=MyInput, requires_keys=("DATA_GO_KR_KEY",), timeout_s=20)
async def my_tool(inp: MyInput):
    await throttle(URL)
    rows = ...  # httpx 호출
    return ok(rows) if rows else fail("NO_RESULT", "결과가 없습니다.")
```

## 11. 검증 결과

- 대표 요청 5개 실제 실행 기록: [docs/verification/representative-requests.md](docs/verification/representative-requests.md)
- 공시 RAG 실데이터 비교: [docs/verification/D17-rag-comparison.md](docs/verification/D17-rag-comparison.md)
- 실행 로그 샘플(JSON): [docs/verification/logs/](docs/verification/logs/)
- 셀프 체크리스트: [docs/셀프체크리스트_투자도우미.xlsx](docs/셀프체크리스트_투자도우미.xlsx)

## 12. 면책

투자 권유가 아니며 전문가 조언을 대신하지 않습니다. 모든 응답 끝에 같은 문구가 붙습니다.
