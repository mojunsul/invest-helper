"""기획 검증용: 외부 API 3종 + LLM을 한 번씩 호출해 응답 구조를 확인한다.
사용: python scripts/check_apis.py [종목명=삼성전자]
키는 .env 에서만 읽고, 출력에는 키를 찍지 않는다. 응답 원문은 scripts/out/ 에 저장(.gitignore 처리).
"""
import io, json, os, sys, zipfile, datetime as dt
import xml.etree.ElementTree as ET
import requests
from dotenv import load_dotenv

sys.stdout.reconfigure(encoding="utf-8")  # Windows cp949 콘솔에서 em dash 등 출력 오류 방지
load_dotenv()
NAME = sys.argv[1] if len(sys.argv) > 1 else "삼성전자"
OUT = os.path.join(os.path.dirname(__file__), "out")
os.makedirs(OUT, exist_ok=True)
DATA = "https://apis.data.go.kr/1160100"  # 서비스마다 /service 유무가 달라 경로에 포함


def need(k):
    v = os.getenv(k)
    if not v:
        print(f"  [MISSING_API_KEY] .env 에 {k} 없음")
    return v


def save(name, obj):
    with open(os.path.join(OUT, name), "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=2)


def items(js):
    it = js.get("response", {}).get("body", {}).get("items", {})
    it = it.get("item", []) if isinstance(it, dict) else []
    return it if isinstance(it, list) else [it]


def gov(path, key, **p):
    r = requests.get(f"{DATA}/{path}", params={"serviceKey": key, "resultType": "json", **p}, timeout=15)
    print(f"  HTTP {r.status_code}")
    try:
        return r.json()
    except ValueError:
        print("  JSON 아님(키 미승인/활성화 대기 가능):", r.text[:120].replace("\n", " "))
        return {}


def check_price(key):
    print(f"[1] 주가 API (getStockPriceInfo) — {NAME}")
    for back in range(1, 11):  # 최근 영업일 탐색: 오늘부터 과거로
        d = (dt.date.today() - dt.timedelta(days=back)).strftime("%Y%m%d")
        js = gov("GetStockSecuritiesInfoService_V2/getStockPriceInfo_V2", key, basDt=d, likeItmsNm=NAME, numOfRows=5)
        its = items(js)
        if its:
            save("price.json", js)
            print(f"  basDt={d} 건수={len(its)} 필드={list(its[0].keys())}")
            return its[0]
    print("  결과 없음(NO_RESULT 후보)")


def check_fin(key, crno):
    print(f"[2b] 재무정보 API (getSummFinaStat_V2) crno={crno}")
    for y in (dt.date.today().year - 1, dt.date.today().year - 2):
        js = gov("service/GetFinaStatInfoService_V2/getSummFinaStat_V2", key, crno=crno, bizYear=y, numOfRows=10)
        its = items(js)
        if its:
            save("fin.json", js)
            print(f"  bizYear={y} 건수={len(its)} 필드={list(its[0].keys())}")
            return
    print("  결과 없음(NO_FINANCIALS 후보). 응답 원문 확인 필요")


def check_dart(key):
    print(f"[3] DART 공시 — {NAME}")
    z = requests.get("https://opendart.fss.or.kr/api/corpCode.xml", params={"crtfc_key": key}, timeout=30)
    try:
        root = ET.fromstring(zipfile.ZipFile(io.BytesIO(z.content)).read("CORPCODE.xml"))
    except Exception:
        print("  corpCode 응답 이상(키 확인):", z.text[:120]); return None
    hit = [(c.findtext("corp_code"), c.findtext("stock_code", "").strip()) for c in root.iter("list")
           if c.findtext("corp_name") == NAME and c.findtext("stock_code", "").strip()]
    if not hit:
        print("  상장사 매칭 없음"); return None
    code, stock = hit[0]
    bgn = (dt.date.today() - dt.timedelta(days=180)).strftime("%Y%m%d")
    r = requests.get("https://opendart.fss.or.kr/api/list.json",
                     params={"crtfc_key": key, "corp_code": code, "bgn_de": bgn, "page_count": 5}, timeout=15).json()
    save("dart.json", r)
    print(f"  corp_code={code} stock_code={stock} status={r.get('status')} msg={r.get('message')}")
    if r.get("list"):
        print(f"  필드={list(r['list'][0].keys())}")
    c = requests.get("https://opendart.fss.or.kr/api/company.json",
                     params={"crtfc_key": key, "corp_code": code}, timeout=15).json()
    print(f"  company.json: jurir_no={c.get('jurir_no')} induty_code={c.get('induty_code')}")
    return c.get("jurir_no")


def check_llm():
    print("[4] LLM (GMS, OpenAI 호환) — 호출 + Tool 선택")
    key, base, model = need("GMS_API_KEY"), os.getenv("GMS_BASE_URL"), os.getenv("GMS_MODEL")
    if not (key and base and model):
        print("  GMS_API_KEY / GMS_BASE_URL / GMS_MODEL 필요"); return
    tool = {"type": "function", "function": {
        "name": "get_financials", "description": "종목코드로 재무정보를 조회할 때 쓴다",
        "parameters": {"type": "object", "properties": {"stock_code": {"type": "string"}}, "required": ["stock_code"]}}}
    r = requests.post(f"{base.rstrip('/')}/chat/completions",
                      headers={"Authorization": f"Bearer {key}"}, timeout=60,
                      json={"model": model, "tools": [tool],
                            "messages": [{"role": "user", "content": "005930 종목의 재무정보를 조회해줘"}]})
    print(f"  HTTP {r.status_code}")
    try:
        msg = r.json()["choices"][0]["message"]
        print("  tool_calls:", msg.get("tool_calls") or f"(없음) content={str(msg.get('content'))[:80]}")
    except Exception:
        print("  응답 이상:", r.text[:150])


if __name__ == "__main__":
    d = need("DART_API_KEY")
    crno = check_dart(d) if d else None  # 종목코드→DART corp_code→법인등록번호(crno)
    k = need("DATA_GO_KR_KEY")
    if k:
        check_price(k)
        if crno:
            check_fin(k, crno)
        else:
            print("[2] crno 없음 → 재무 호출 생략")
    check_llm()
