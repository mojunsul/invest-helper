const test = require("node:test");
const assert = require("node:assert");
const fs = require("node:fs");
const path = require("node:path");
const T = require("../../frontend/chat.js");

const P = (id, value) => ({ id, value });
const m = (i, p, t) => T.mergeQuery(i, p, t);

test("mergeQuery 예시 1~5", () => {
  assert.deepStrictEqual(m("반도체", [P("roe", "10"), P("per")], "공시도 같이 보여줘"),
    { ok: true, applied: true, text: "반도체 중 ROE 10% 이상, PER 산업 평균 이하인 종목 찾아줘 공시도 같이 보여줘" });
  assert.strictEqual(m("자동차", [P("debt", "100")], "ROE는 15% 이상으로").text, "자동차 중 부채비율 100% 이하인 종목 찾아줘 ROE는 15% 이상으로");
  assert.strictEqual(m("게임", [], "영업이익률 15% 이상 종목 찾아줘").text, "게임 중 영업이익률 15% 이상 종목 찾아줘");
  assert.deepStrictEqual(m("", [P("per"), P("roe", "10")], "은행 종목만"),
    { ok: true, applied: true, text: "PER 산업 평균 이하, ROE 10% 이상 조건. 은행 종목만" });
  assert.deepStrictEqual(m("", [], "삼성전자 PER 알려줘"), { ok: true, applied: false, text: "삼성전자 PER 알려줘" });
});

test("mergeQuery 오류·길이", () => {
  for (const v of ["", "101", "abc"]) assert.strictEqual(m("게임", [P("roe", v)], "x").ok, false);
  assert.strictEqual(m("", [P("roe", "")], "x").ok, false);
  assert.strictEqual(m("게임", [], "x").ok, true);  // 산업만이면 칩 검증 없음
  const pre = "게임 중 ".length;
  assert.strictEqual(m("게임", [], "a".repeat(2000 - pre)).ok, true);
  assert.strictEqual(m("게임", [], "a".repeat(2001 - pre)).ok, false);
});

// DOM(jsdom 있을 때): ①~⑦
test("DOM 병합·복원·안내문", async (t) => {
  let JSDOM;
  try { ({ JSDOM } = require("jsdom")); } catch (e) { t.skip("jsdom 없음"); return; }
  const html = fs.readFileSync(path.join(__dirname, "../../frontend/index.html"), "utf8");
  const w = new JSDOM(html, { runScripts: "outside-only", url: "http://localhost/" }).window;
  w.eval(fs.readFileSync(path.join(__dirname, "../../frontend/common.js"), "utf8"));
  const bodies = []; let fail = null;
  w.fetch = async (u, o) => {
    bodies.push(JSON.parse(o.body).message);
    if (fail) return fail;
    return { ok: true, status: 200, json: async () => ({ status: "answered", answer: "x", intent: "chitchat" }) };
  };
  w.eval(fs.readFileSync(path.join(__dirname, "../../frontend/chat.js"), "utf8"));
  w.document.dispatchEvent(new w.Event("DOMContentLoaded"));
  const d = w.document, $ = (i) => d.getElementById(i), tick = () => new Promise((r) => setTimeout(r, 20));
  const pick = () => { $("cf-industry").value = "게임"; $("cf-per").checked = true; $("cf-form").dispatchEvent(new w.Event("change")); };
  const send = async (t) => { $("msg").value = t; $("form").dispatchEvent(new w.Event("submit", { cancelable: true })); await tick(); };

  assert.ok($("cf-note").hidden);
  pick();
  assert.ok(!$("cf-note").hidden && /게임 · 저평가/.test($("cf-note").textContent));
  await send("공시도");
  assert.strictEqual(bodies[0], "게임 중 PER 산업 평균 이하인 종목 찾아줘 공시도");
  assert.strictEqual($("cf").open, false); assert.ok($("cf-note").hidden);
  await send("후속");
  assert.strictEqual(bodies[1], "후속");
  fail = { ok: false, status: 422, json: async () => ({ message: "bad" }) };
  $("cf").open = true; await send("실패");
  assert.strictEqual(bodies[2], "게임 중 PER 산업 평균 이하인 종목 찾아줘 실패");
  assert.strictEqual($("cf").open, true);
  fail = null;
  $("cf-roe").checked = true; $("cf-roe-v").disabled = false; $("cf-roe-v").value = "";
  await send("ROE");
  assert.strictEqual(bodies.length, 3); assert.ok(!$("cf-err").hidden);
  await send("산업을 ");  // prefill 단독은 병합 안 함(텍스트만)
  assert.strictEqual(bodies[3], "산업을");
});
