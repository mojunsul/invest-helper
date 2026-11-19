# D1c Tool 종류 구분(외부 API 툴 / 로컬 서버 API 툴)

기준: D1 §4(`register`·`describe`), DESIGN §3, 원본 명세 F3001·셀프체크리스트 1번. 의사코드이며 구현 아님. **D0·DESIGN.md는 수정하지 않는다.** 새 클래스·추상화 없음.

## 1. 목적

F3001은 Tool을 "외부 API 툴"과 "로컬 서버 API 툴"로 구분해 정의·등록하길 요구하고, 체크리스트 1번은 Tool 목록 출력에서 그 구분을 확인한다. 현재 `Tool` 정의와 `registry.describe()`에는 구분 필드가 없다. → Tool마다 `kind` 필드 하나를 두고 `describe()`에 함께 낸다.

## 2. 변경 범위

| 파일 | 변경 |
|---|---|
| `backend/tools/base.py` | `ToolKind = Literal["external", "local"]` 추가. `Tool`에 `kind: ToolKind` 필드 추가(`fn` 다음, 기본값 없음). `register(*, name, description, input_model, kind, requires_keys=(), timeout_s=None)` — `kind`는 **필수 키워드, 기본값 없음**. `kind not in get_args(ToolKind)`이면 `ValueError`(기존 `requires_keys` 검사와 같은 등록 시점 검사). |
| `backend/tools/registry.py` | `describe()` 각 항목에 `"kind": t.kind` 추가 → `{"name","kind","description","input_schema"}`. |
| `stock_candidates.py`, `financials.py`, `disclosures.py` | 각 `@register(...)`에 `kind="external"` 한 줄. |
| `tests/test_d1.py` | `register(...)` 호출 3곳(`mk`, 28·63·66행 부근)에 `kind="external"` 추가, `describe()` 키 집합 단언 갱신. |

- 기본값을 두지 않는 이유: 새 Tool을 등록할 때 구분을 빠뜨리면 import 시점에 바로 `TypeError`로 드러난다(F3001 "구분하여 정의").
- 현재 Tool 3종은 모두 `external`(DESIGN §3 제목 "전부 외부 API 툴"과 일치).
- 심화 F3022의 `search_filing_text`는 `kind="local"`로 등록될 예정이다(구현 범위 아님).
- 체크리스트 1번 확인 방법(코드 추가 없음): `python -c "from backend.tools import registry; registry.load_all(); import json; print(json.dumps(registry.describe(), ensure_ascii=False, indent=2))"`.

## 3. LLM 전달 영향

- **`describe()`는 현재 어떤 LLM 프롬프트에도 들어가지 않는다.** `backend/` 안에서 `describe()` 호출처는 없고(정의만 존재), D7 설계대로 plan은 규칙 기반이라 `describe()`를 쓰지 않는다(D7 문서의 테스트 절·D1 대비표·§10 요청 6).
- 따라서 "LLM에 넘기는 목록"과 "사람이 확인하는 출력"은 같은 함수(`describe()`)지만, 실사용 소비자는 사람(체크리스트 출력)과 테스트뿐이다. 필드 추가로 프롬프트 길이·Agent 동작 변화 **없음**.
- 향후 `describe()`를 LLM에 넘기게 되더라도 증가분은 Tool당 `"kind":"external"` 한 쌍(수 토큰)이며, `kind`는 실행 경로를 바꾸지 않는 설명 정보다. 별도 함수로 분리하지 않는다.

## 4. 계약 변경 여부

- `ToolResult`·`ErrorCode`·`Tool.run` 래퍼·캐시 키(`cache_key_for`)·이벤트 스키마 **불변**. D0 변경 없음.
- D1 §4 `describe()` 반환 형태에 `kind` 키 1개 추가(확장). D1 §4 "describe()에 내부 구현을 넣지 않는다"와 충돌하지 않는다 — `kind`는 명세가 요구하는 공개 분류이지 `fn`·`requires_keys`·`timeout_s`·API 경로 같은 내부 구현이 아니다.

### D1 문구 수정 제안(사용자 승인 후 반영, 이 단위에서 직접 수정하지 않음)
1. D1 §4 `describe()` 주석을 `[{"name","kind","description","input_schema"}]`로, `register` 시그니처에 `kind: Literal["external","local"]`(필수)를 추가.
2. (선택) DESIGN §3 표에 "종류" 열 또는 문장 "모든 Tool은 `kind`로 external/local을 명시한다" 한 줄 추가.

## 5. 테스트 관점 (`tests/test_d1.py`에 추가·갱신)

1. `describe()` 항목 키 집합이 `{"name","kind","description","input_schema"}`이고 `kind`가 등록값과 같다(기존 `test_registry_and_cache` 단언 갱신).
2. `register(..., kind="remote")` → `ValueError`, `kind` 생략 → `TypeError`.
3. `registry.load_all()` 후 실제 Tool 3종(`find_stock_candidates`, `get_financials`, `search_disclosures`)의 `kind`가 모두 `"external"`.

## 6. 구현 시 확인 항목

- `Tool`은 `@dataclass(frozen=True)`이고 기본값 없는 필드는 기본값 있는 필드보다 앞이어야 하므로 `kind`를 `fn` 바로 다음에 둔다. `register` 안의 위치 인자 생성식(`Tool(name, description, input_model, fn, ...)`)도 순서에 맞게 고치거나 키워드 인자로 바꾼다.
- `test_d1`의 `_TOOLS.clear()` 후 `load_all()`은 이미 import된 모듈을 다시 등록하지 않는다. 테스트 3은 `importlib.reload` 또는 새 프로세스 대신, 각 모듈의 모듈 전역 Tool 객체(`stock_candidates.find_stock_candidates.kind` 등)를 직접 확인하는 편이 단순하다.
- 다른 테스트 파일(test_d7·d8·d9·d13)은 `register`를 직접 부르지 않음을 grep으로 확인했다. 구현 후 전체 테스트를 한 번 돌려 확인한다.
