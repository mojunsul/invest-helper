# D2c 구축 CLI 단계 B 진행 로그

기준: D2 §6(단계 B·로그 규칙), D2b(호출 간격·연속 실패 중단). 의사코드이며 구현 아님. **D0·D1·DESIGN.md는 수정하지 않는다.** 새 `ErrorCode`·`BuildReport` 필드 없음. 변경은 `backend/identity/build.py`의 `build()` 단계 B 루프에 INFO 로그 2종 추가뿐.

## 1. 배경

`python -m backend.identity.build` 단계 B(company.json 순회)는 성공 시 아무것도 출력하지 않는다. 실사용(VPN 경유)에서 요청당 ~2.6s, 전체 ~1.5시간이 걸렸는데 터미널에는 가끔 TIMEOUT 경고만 보여 진행 여부를 알 수 없었다.

목표: 단계 B 시작 시 처리 대상 건수, 이후 일정 건수마다 진행 상황 한 줄.

## 2. 로그 추가 (build.py)

```python
import time
...
        rows = con.execute(...).fetchall()                       # 기존 그대로
        total = len(rows)
        logger.info("stage B start total=%d", total)             # (1) 시작 줄
        n = streak = ok = none = failed = 0
        t0 = time.monotonic()
        try:
            for sc, cc in rows:
                ...
                try:
                    body = await source.fetch_company(cc)
                    if body is None:
                        con.execute(...)                         # 기존 그대로
                        none += 1
                    else:
                        con.execute(...)                         # 기존 그대로
                        ok += 1
                    streak = 0
                except IdentityError as e:
                    if e.code in ("RATE_LIMIT", "MISSING_API_KEY"):
                        stopped = e.code
                        break
                    con.execute(...failed...)                    # 기존 그대로
                    failed += 1
                    ...                                          # streak/NETWORK 중단 기존 그대로
                n += 1
                if n % commit_every == 0:
                    con.commit()
                    el = time.monotonic() - t0                   # (2) 진행 줄: 주기 커밋 지점에서만
                    logger.info("stage B progress %d/%d ok=%d none=%d failed=%d elapsed=%ds eta=%ds",
                                n, total, ok, none, failed, el, el / n * (total - n))
        finally:
            con.commit()                                         # 기존 그대로
```

- **주기**: 별도 상수 없이 기존 `commit_every`(`COMMIT_EVERY=100`)를 재사용. 진행 줄은 주기 커밋 직후에만 나온다 → "로그에 찍힌 건수까지는 DB에 반영됨"이 함께 보장된다.
- **필드**: `n/total`, 이번 실행분 `ok/none/failed`, 경과 초, 남은 시간 추정(평균 소요 × 남은 건수, 초 단위 정수 `%d` 절삭). 한 줄 고정.
- **카운터 의미**: 이번 실행에서 처리한 행만 센다(누적 DB 값 아님). 누적 값은 기존 최종 `build ok=... pending=...` 줄이 이미 출력하므로 중복하지 않는다.
- `total == 0`(이미 완료)이어도 시작 줄은 `total=0`으로 출력. 루프가 돌지 않으므로 진행 줄 없음 → `n=0` 나눗셈 없음(진행 줄은 `n % commit_every == 0`이고 `n ≥ 1`일 때만 실행).
- 마지막 부분 묶음(`total`이 `commit_every`의 배수가 아닐 때 나머지)이나 중단(`break`) 시 별도 진행 줄은 내지 않는다. 기존 최종 `build ...` 줄과 D2b 중단 안내가 그 역할을 한다.
- `n`은 기존과 동일하게 증가(RATE_LIMIT/MISSING_API_KEY/NETWORK `break` 시 해당 행은 미증가). 따라서 NETWORK 중단 직전의 failed 행은 `failed` 카운터에는 들어가지만 `n`에는 안 들어갈 수 있다 — 중단 직후엔 진행 줄이 없으므로 표시상 영향 없음.
- **비노출**: 로그 인자는 정수·경과 시간뿐. API 키·URL·corp_code·응답 본문 없음. `main`의 `install_log_mask()`는 기존 그대로 적용됨.
- **불변**: 오류 처리, D2b 간격(`_sleep`)·연속 실패 중단, DB 쓰기·커밋 시점, `BuildReport`·반환값·종료 코드 모두 변경 없음. CLI 옵션 추가 없음.

## 3. 테스트 (tests/test_d2.py, 실호출·실대기 없음)

기존 `run()` 헬퍼·`Fake`·`Flaky` 패턴 재사용. `CORPS` 8행, 정렬 순서 `000001,000002,000003,000004(none),000660,005930,005935,035420`.

1. **시작 줄·주기 진행 줄**: `caplog.set_level(logging.INFO)`, `run(commit_every=3)` →
   - `"stage B start total=8"` 포함.
   - `"stage B progress"`를 포함한 레코드가 정확히 2개, 각각 `"3/8 ok=3 none=0 failed=0"`, `"6/8 ok=5 none=1 failed=0"` 포함(elapsed/eta 값은 단언하지 않음).
2. **failed 카운트**: 기존 `test_failed_row_continues_and_retried`의 `Flaky`(005930 = 6번째 TIMEOUT)를 `commit_every=3`으로 실행 → 두 번째 진행 줄에 `"6/8 ok=4 none=1 failed=1"`. (`Flaky`를 모듈 수준으로 올리거나 테스트 내부 재정의 — 구현 재량.)
3. **이미 완료/주기 미도달**: `run()`(기본 `commit_every=100`) → 진행 줄 0개, 시작 줄 `total=8`. 이어서 `run()` 재실행 → `"stage B start total=0"`, 진행 줄 0개.
- 기존 테스트 단언 변경 없음.

## 4. 구현 시 확인 항목

- 실구축에서 100건 주기(요청당 ~2.6s 기준 약 4~5분 간격)가 진행 확인에 충분한지 관찰. 너무 뜸하면 별도 상수로 분리하되 커밋 주기와는 독립시킨다(이번 범위 아님).
- ETA는 초기 TIMEOUT 구간·VPN 지연 변동으로 크게 흔들릴 수 있음(단순 평균). 실사용에서 쓸 만한지만 확인.
- 초 단위 정수 출력이 장시간(수천 초)에서 읽기 불편하면 `분` 단위로 바꾸는 정도만 허용(필드 추가 없이).
- caplog에서 `build` 모듈 로거(`backend.identity.build`)의 INFO가 잡히는지(기존 `test_network_streak_stops_and_logs`와 동일 방식이므로 문제 없을 것으로 예상).

## 5. 상위 계약 영향

- **D0 변경 없음. DESIGN.md 변경 없음. D1 변경 없음.**
- D2 보완(문서 수정 제안만): §6 단계 B에 "시작 시 대상 건수, `commit_every`마다 진행 줄(INFO, 수치만) 출력" 한 줄.
