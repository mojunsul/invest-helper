const test = require("node:test");
const assert = require("node:assert");
const fs = require("node:fs");
const path = require("node:path");
const T = require("../../frontend/chat.js");

const P = (id, value) => ({ id, value });
const ok = (ind, picks) => { const r = T.buildQuery(ind, picks); assert.ok(r.ok, JSON.stringify(r)); return r.text; };

test("buildQuery 예시 5개", () => {
  assert.strictEqual(ok("반도체", [P("roe", "10"), P("per")]), "반도체 중 ROE 10% 이상, PER 산업 평균 이하인 종목 찾아줘");
  assert.strictEqual(ok("자동차", [P("pbr", "1"), P("debt", "150")]), "자동차 중 PBR 1배 이하, 부채비율 150% 이하인 종목 찾아줘");
  assert.strictEqual(ok("게임", [P("opm", "15")]), "게임 중 영업이익률 15% 이상인 종목 찾아줘");
  assert.strictEqual(ok("화학", [P("roe", "10"), P("opm", "10"), P("per"), P("pbr", "1"), P("debt", "100")]),
    "화학 중 ROE 10% 이상, 영업이익률 10% 이상, PER 산업 평균 이하, PBR 1배 이하, 부채비율 100% 이하인 종목 찾아줘");
  assert.strictEqual(ok("은행", [P("per")]), "은행 중 PER 산업 평균 이하인 종목 찾아줘");
});

test("buildQuery 오류·정규화", () => {
  for (const ind of ["", "없는산업", undefined]) {
    const r = T.buildQuery(ind, [P("per")]);
    assert.deepStrictEqual([r.ok, r.field], [false, "cf-industry"]);
  }
  assert.strictEqual(T.buildQuery("게임", []).ok, false);
  for (const [id, v] of [["roe", ""], ["roe", "abc"], ["roe", "101"], ["pbr", "0"], ["debt", "-1"], ["opm", null]]) {
    const r = T.buildQuery("게임", [P("per"), P(id, v)]);
    assert.deepStrictEqual([r.ok, r.field], [false, id], id + v);
  }
  assert.match(ok("게임", [P("roe", "010")]), /ROE 10% 이상/);
  assert.match(ok("게임", [P("roe", "10.0")]), /ROE 10% 이상/);
  assert.match(ok("게임", [P("pbr", "0.5")]), /PBR 0.5배/);
});

test("INDUSTRIES = 사전 term 부분집합, 30개, 중복 없음", () => {
  const terms = JSON.parse(fs.readFileSync(path.join(__dirname, "../../backend/identity/data/industry_terms.json"), "utf8")).map((x) => x.term);
  assert.strictEqual(T.INDUSTRIES.length, 30);
  assert.strictEqual(new Set(T.INDUSTRIES).size, 30);
  for (const n of T.INDUSTRIES) assert.ok(terms.includes(n), n);
});

test("COND_DEFS 지표 키워드·순서", () => {
  assert.deepStrictEqual(T.COND_DEFS.map((d) => d.id), ["roe", "opm", "per", "pbr", "debt"]);
  for (const d of T.COND_DEFS) {
    const s = d.fixed || d.fmt(String(d.def));
    assert.ok(/^(ROE|영업이익률|PER|PBR|부채비율)/.test(s), s);
  }
});

// DOM: 폼 전송이 #msg 초안을 건드리지 않고, busy 해제 시 미체크 값 입력은 비활성 유지
test("폼 DOM: 초안 보존·setBusy", async (t) => {
  let JSDOM;
  try { ({ JSDOM } = require("jsdom")); } catch (e) { t.skip("jsdom 없음"); return; }
  const html = fs.readFileSync(path.join(__dirname, "../../frontend/index.html"), "utf8");
  const dom = new JSDOM(html, { runScripts: "outside-only", url: "http://localhost/" });
  const w = dom.window;
  w.eval(fs.readFileSync(path.join(__dirname, "../../frontend/common.js"), "utf8"));
  let resolve; w.fetch = () => new Promise((r) => { resolve = r; });
  w.eval(fs.readFileSync(path.join(__dirname, "../../frontend/chat.js"), "utf8"));
  w.document.dispatchEvent(new w.Event("DOMContentLoaded"));
  const d = w.document, $ = (i) => d.getElementById(i);
  assert.strictEqual($("cf").open, true);  // 로드 시 열림
  assert.ok(!/용어 몰라도/.test($("cf").textContent));
  $("msg").value = "초안";
  $("cf-industry").value = "게임"; $("cf-roe").checked = true; $("cf-roe").dispatchEvent(new w.Event("change"));
  $("cf-form").dispatchEvent(new w.Event("submit", { cancelable: true }));
  assert.strictEqual($("msg").value, "초안");
  assert.ok($("cf-go").disabled && $("cf-industry").disabled && $("cf-roe-v").disabled);
  assert.strictEqual($("cf").open, false);  // 전송 시 닫힘
  resolve({ ok: true, status: 200, json: async () => ({ status: "answered", answer: "x", intent: "chitchat" }) });
  await new Promise((r) => setTimeout(r, 20));
  assert.strictEqual($("msg").value, "초안");
  assert.ok(!$("cf-go").disabled && !$("cf-roe-v").disabled && $("cf-opm-v").disabled);
  assert.strictEqual(d.activeElement, $("cf-go"));
});
