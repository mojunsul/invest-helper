const test = require("node:test");
const assert = require("node:assert");
const C = require("../../frontend/common.js");
const T = require("../../frontend/chat.js");

test("safeUrl", () => {
  assert.ok(C.safeUrl("https://a.com/x"));
  assert.ok(C.safeUrl("http://a.com"));
  for (const s of ["javascript:alert(1)", "JaVaScRiPt:alert(1)", " javascript:x", "java\nscript:x", "data:text/html,x",
    "vbscript:x", "file:///etc/passwd", "/rel", "", "a.com", null])
    assert.strictEqual(C.safeUrl(s), null, String(s));
});

test("splitUrls", () => {
  const u = (t) => C.splitUrls(t).filter((p) => p.type === "url").map((p) => p.value);
  assert.deepStrictEqual(u("see https://a.com/x. ok"), ["https://a.com/x"]);
  assert.deepStrictEqual(u("(https://a.com/x), 그리고"), ["https://a.com/x"]);
  assert.deepStrictEqual(u("https://a.com/x에서 확인"), ["https://a.com/x"]);
  assert.deepStrictEqual(u("a https://a.com b http://b.com/y"), ["https://a.com", "http://b.com/y"]);
  assert.deepStrictEqual(C.splitUrls("no url"), [{ type: "text", value: "no url" }]);
  assert.strictEqual(C.splitUrls("a https://x.com b").map((p) => p.value).join(""), "a https://x.com b");
  assert.deepStrictEqual(C.splitUrls(""), []);
});

const fakeFetch = (status, body, { json = true, reject = false } = {}) => async () => {
  if (reject) throw new Error("net");
  return { ok: status >= 200 && status < 300, status, json: async () => { if (!json) throw new Error("bad"); return body; } };
};

test("api", async () => {
  let r = await C.api("POST", "/x", {}, fakeFetch(200, { a: 1 }));
  assert.deepStrictEqual(r, { ok: true, status: 200, data: { a: 1 } });
  r = await C.api("POST", "/x", {}, fakeFetch(201, { id: 1, duplicate_of: [] }));
  assert.strictEqual(r.status, 201);
  r = await C.api("DELETE", "/x", undefined, fakeFetch(204, null));
  assert.deepStrictEqual(r, { ok: true, status: 204, data: null });
  for (const s of [404, 409, 422]) {
    r = await C.api("POST", "/x", {}, fakeFetch(s, { code: "C", message: "m", extra: 1 }));
    assert.deepStrictEqual(r, { ok: false, status: s, data: { code: "C", message: "m" } });
  }
  r = await C.api("POST", "/x", {}, fakeFetch(500, null, { json: false }));
  assert.deepStrictEqual(r.data, { code: "HTTP_ERROR", message: "요청에 실패했습니다." });
  r = await C.api("POST", "/x", {}, fakeFetch(500, { code: "X" }));
  assert.strictEqual(r.data.code, "HTTP_ERROR");
  r = await C.api("POST", "/x", {}, fakeFetch(0, null, { reject: true }));
  assert.deepStrictEqual(r, { ok: false, status: 0, data: { code: "NETWORK_ERROR", message: "서버에 연결하지 못했습니다." } });
  r = await C.api("POST", "/x", {}, fakeFetch(200, null, { json: false }));
  assert.strictEqual(r.data.code, "NETWORK_ERROR");
});

const V4 = /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/;
const mem = (init) => { const m = { v: init }; return { m, get: () => m.v, set: (_k, v) => { m.v = v; } }; };

test("getSessionId", () => {
  const good = "123E4567-E89B-42D3-A456-426614174000";
  const s = mem(good);
  assert.strictEqual(T.getSessionId(s), good.toLowerCase());
  for (const bad of [null, "", "nope", "123e4567-e89b-42d3-a456-42661417400"]) {
    const st = mem(bad);
    const id = T.getSessionId(st);
    assert.match(id, V4);
    assert.strictEqual(st.m.v, id);
  }
  const boom = { get() { throw new Error("x"); }, set() { throw new Error("y"); } };
  assert.match(T.getSessionId(boom), V4);
});

test("uuid fallback", () => {
  const fake = { getRandomValues: (b) => { b.fill(255); return b; } };
  const id = T.uuidV4(fake);
  assert.match(id, V4);
  assert.match(T.uuidV4({ randomUUID() { throw new Error("no"); }, getRandomValues: fake.getRandomValues }), V4);
});

test("timelineRows", () => {
  const steps = [
    { seq: 1, stage: "analyze", round: 0, started_at: "2024-01-01T00:00:01Z", ended_at: "2024-01-01T00:00:02Z", intent: "screen", reason: "r" },
    { seq: 6, stage: "weird", round: 1, started_at: "2024-01-01T00:00:06Z", ended_at: null },
  ];
  const calls = [
    { type: "result", seq: 4, round: 0, tool: "t", ok: false, error_code: "E", duration_ms: 5, summary: "s" },
    { type: "call", seq: 3, round: 0, tool: "t", input: { a: 1 }, cache_hit: true },
  ];
  const rows = T.timelineRows(steps, calls, [{ count: 1, trigger: "no_pass", change: "market_expand" }]);
  const t = rows.map((r) => r.text);
  assert.strictEqual(rows.length, 5);
  assert.match(t[0], /의도 분석.*screen.*r/);
  assert.match(t[1], /▶ t 호출 {"a":1} \(캐시\)/);
  assert.match(t[2], /◀ t 실패 E · 5ms · s/);
  assert.strictEqual(rows[3].kind, "sep");
  assert.match(t[3], /조건 충족 종목 없음 → 시장 확대/);
  assert.match(t[4], /^weird .*\(중단\)/);
  // replans 비면 구분선 없음, 알 수 없는 코드는 원문
  assert.ok(!T.timelineRows(steps, calls, []).some((r) => r.kind === "sep"));
  const unk = T.timelineRows([{ seq: 1, round: 0 }, { seq: 2, round: 2 }], [], [{ trigger: "zz", change: "yy" }, { trigger: "no_pass", change: "limit_up" }]);
  assert.strictEqual(unk[1].text, "── 재계획 2: no_pass".replace("no_pass", "조건 충족 종목 없음") + " → 후보 수 상한 확대 ──");
  assert.strictEqual(T.timelineRows([{ seq: 1, round: 0 }, { seq: 2, round: 1 }], [], [{}, {}]).length, 3);
  // 방어
  assert.deepStrictEqual(T.timelineRows(undefined, null, "x"), []);
  const nan = T.timelineRows([{ stage: "plan" }, { seq: 1, stage: "act" }], [], []);
  assert.match(nan[0].text, /^act/);
});

test("buildSaveBody", () => {
  assert.deepStrictEqual(T.buildSaveBody("s", "005930", "  ", ""), { session_id: "s", stock_code: "005930" });
  assert.deepStrictEqual(T.buildSaveBody("s", "005930", " n ", "m"), { session_id: "s", stock_code: "005930", name: "n", memo: "m" });
});
