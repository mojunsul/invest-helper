swagger-ui-dist 5.35.1 (License: Apache-2.0, https://github.com/swagger-api/swagger-ui)
/docs 를 CSP(script-src 'self') 유지한 채 로컬로 서빙하기 위한 사본 (D23). docs-init.js 는 본 프로젝트 파일.

Source:
  https://cdn.jsdelivr.net/npm/swagger-ui-dist@5.35.1/swagger-ui-bundle.js
  https://cdn.jsdelivr.net/npm/swagger-ui-dist@5.35.1/swagger-ui.css
SHA-256:
  d0e0bee93732408c91fbad05cb0c2812e9ad37e94d12799c5503a9fe7afa2b82  swagger-ui-bundle.js
  5ed08757e53da3e026d6cbecc61fe5d43585c9560b302d534ad56692519a273d  swagger-ui.css

Update: 같은 URL 형식에서 버전만 바꿔 두 파일을 다시 받아 교체하고, 이 파일의 버전·SHA-256 을 갱신한다
(FastAPI 기본값은 swagger-ui-dist@5). 교체 후 tests/test_d23_docs.py 와 /docs 실브라우저 확인.
