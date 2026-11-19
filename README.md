# invest-helper

산업·지표 조건으로 국내 상장 종목을 탐색·판정하고 근거와 함께 보여주는 투자 보조 챗봇(FastAPI + LangGraph).

## 1. 설치

개발 환경: Python 3.12.10.

```
python -m venv .venv
.venv\Scripts\activate        # macOS/Linux: source .venv/bin/activate
pip install -r requirements.txt
```

## 2. .env 준비

`.env.example`을 `.env`로 복사하고 키를 채운다. `.env`는 커밋하지 않는다(`.gitignore` 등록됨).

| 키 | 용도 |
|---|---|
| `DATA_GO_KR_KEY` | 금융위 주가·재무 (data.go.kr) |
| `DART_API_KEY` | DART 기업정보·공시 |
| `GMS_API_KEY`, `GMS_BASE_URL`, `GMS_MODEL` | LLM (SSAFY GMS, OpenAI 호환) |
| `REPLAN_MAX` | 재계획 최대 횟수 (1~3, 기본 3) |

## 3. 산업 캐시 구축 (앱 실행 전 1회)

```
python -m backend.identity.build            # 중단 후 재실행하면 이어서 진행
python -m backend.identity.build --refresh  # 상장사 목록(corpCode) 갱신
python -m backend.identity.build --force    # 상세 정보 전체 재수집
```

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

| 출처 | 사용 | 이용조건·호출 한도 |
|---|---|---|
| data.go.kr 금융위원회 `GetStockSecuritiesInfoService_V2` (주가) | 후보 시세·시가총액 | 미확인 |
| data.go.kr 금융위원회 `getSummFinaStat_V2` (재무) | 재무 요약 | 미확인 |
| OpenDART (`corpCode.xml`, `company.json`, `list.json`) | 종목·산업 캐시, 공시 | 미확인 |
| SSAFY GMS (OpenAI 호환) | 의도 분석·요약 | 미확인 |

각 서비스 페이지에서 확인하기 전까지 "미확인"으로 둔다.

## 8. 구현 범위

- 제외: 점수화 추천, 산업 전망, 관심 종목 비교, 스케줄러 수집, 세션 영속화, Forward PER 등 예상 실적 기반 지표, F3020/3021/3023.
- 세션 메모리는 프로세스 메모리라 서버를 재시작하면 사라진다. 리포트·실행 로그는 SQLite에 남는다.
- 실제 외부 API 호출 기반 검증과 브라우저 수동 시나리오는 아직 수행하지 않았다(미확인).

## 9. 면책

투자 권유가 아니며 전문가 조언을 대신하지 않습니다. 모든 응답 끝에 같은 문구가 붙습니다.
