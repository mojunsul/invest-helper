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
  assert.deepStrictEqual(T.buildSaveBody("s", "005930", "", "", "2026-12-01"), { session_id: "s", stock_code: "005930", review_date: "2026-12-01" });
  assert.deepStrictEqual(T.buildSaveBody("s", "005930", "", "", ""), { session_id: "s", stock_code: "005930" });
});

// ---- D14c ----
test("fmtMetric", () => {
  assert.strictEqual(T.fmtMetric("ROE", 12.1), "12.10%");
  assert.strictEqual(T.fmtMetric("PER", 9.8), "9.80배");
  assert.strictEqual(T.fmtMetric("PER", null), "-");
  assert.strictEqual(T.fmtMetric("ROE", -3.456), "-3.46%");
});

test("condChips", () => {
  assert.deepStrictEqual(T.condChips(null), []);
  assert.deepStrictEqual(T.condChips({ industry: null, constraints: [], pending_metrics: [] }), []);
  const cs = T.condChips({ industry: "반도체", market: null, base_date: "2025-12-31", want_disclosures: true,
    constraints: [{ metric: "ROE", op: ">=", value: 10, ref: null }, { metric: "PER", op: "<=", value: null, ref: "industry_avg" }],
    pending_metrics: ["OPM"] });
  assert.deepStrictEqual(cs.map((c) => c.label), ["산업 반도체", "시장 전체", "기준일 2025-12-31", "ROE ≥ 10%",
    "PER ≤ 산업 평균", "영업이익률 기준값 필요", "공시 포함"]);
  assert.strictEqual(cs[cs.length - 1].prefill, null);
  cs.forEach((c) => assert.ok(!/\d/.test(c.prefill || "")));
  assert.strictEqual(T.condChips({ industry: "a", market: "KOSPI", constraints: [], pending_metrics: [] })[1].label, "시장 KOSPI");
});

test("groupVerdicts", () => {
  const g = T.groupVerdicts([{ status: "FAIL", stock_code: "a" }, { status: "PASS", stock_code: "b" },
    { status: "FAIL", stock_code: "c" }, { status: "??" }]);
  assert.deepStrictEqual(g.FAIL.map((v) => v.stock_code), ["a", "c"]);
  assert.strictEqual(g.PASS.length, 1);
  assert.deepStrictEqual(T.groupVerdicts("x"), { PASS: [], FAIL: [], UNDECIDABLE: [] });
});

test("viewKind", () => {
  const v = [{ status: "PASS" }];
  assert.strictEqual(T.viewKind({ status: "asked", intent: "screen", verdicts: v }), "text");
  assert.strictEqual(T.viewKind({ status: "error", intent: "screen", verdicts: v }), "text");
  assert.strictEqual(T.viewKind({ status: "answered", intent: "screen", verdicts: v }), "table");
  assert.strictEqual(T.viewKind({ status: "limit", intent: "refine", verdicts: v }), "table");
  assert.strictEqual(T.viewKind({ status: "answered", intent: "screen", verdicts: [] }), "text");
  assert.strictEqual(T.viewKind({ status: "answered", intent: "lookup", metrics: { a: {} } }), "metric");
  assert.strictEqual(T.viewKind({ status: "answered", intent: "lookup", metrics: {} }), "text");
  assert.strictEqual(T.viewKind({ status: "answered", intent: "filing_qa", evidence: [{}] }), "evidence");
  assert.strictEqual(T.viewKind({ status: "answered", intent: "filing_qa", evidence: [] }), "text");
  assert.strictEqual(T.viewKind({ status: "answered", verdicts: v }), "text");
  assert.strictEqual(T.viewKind({ status: "answered", intent: "chitchat" }), "text");
  assert.strictEqual(T.viewKind(null), "text");
  for (const k of ["screen", "refine", "lookup", "report_save", "chitchat", "filing_qa"]) assert.ok(T.INTENT_TEXT[k], k);
});

test("headline", () => {
  assert.strictEqual(T.headline("a b\n\nc"), "a b");
  assert.strictEqual(T.headline(""), "");
  assert.strictEqual(T.headline(null), "");
});

test("avgText", () => {
  assert.strictEqual(T.avgText({}), null);
  assert.match(T.avgText({ PER: { value: 14.2, n: 10 } }), /14\.20배 \(n=10/);
  assert.match(T.avgText({ PER: { value: null, n: 0 } }), /산출 불가/);
  assert.match(T.avgText({ PER: { value: 9, n: 2 } }), /표본 부족/);
  assert.ok(!/표본 부족/.test(T.avgText({ PER: { value: 9, n: 3 } })));
});

test("statusLine", () => {
  const t = { status: "answered", intent: "screen", verdicts: [{ status: "PASS" }, { status: "FAIL" }, { status: "UNDECIDABLE" }] };
  assert.strictEqual(T.statusLine(t), "분석 완료 — 충족 1 · 불충족 1 · 판단불가 1");
  assert.strictEqual(T.statusLine({ status: "answered", intent: "chitchat" }), "분석 완료");
  assert.strictEqual(T.statusLine({ status: "asked" }), "추가 정보가 필요합니다");
  assert.strictEqual(T.statusLine({ status: "limit" }), "조건을 충족한 종목이 없습니다");
  assert.strictEqual(T.statusLine({ status: "error" }), "처리하지 못했습니다");
});

test("tableModel", () => {
  const res = { status: "answered", intent: "screen", saveable: [{ stock_code: "A" }, { stock_code: "B" }],
    conditions: { want_disclosures: true, constraints: [{ metric: "ROE" }, { metric: "PER" }, { metric: "ROE" }] },
    verdicts: [
      { stock_code: "A", stock_name: "가", status: "PASS", reasons: [], failed: [] },
      { stock_code: "B", stock_name: "나", status: "FAIL", reasons: ["THRESHOLD_NOT_MET", "NO_MARKET_CAP"], failed: ["ROE"] },
      { stock_code: "C", stock_name: "다", status: "UNDECIDABLE", reasons: ["NO_FINANCIALS", "ZZZ"], failed: [] }],
    metrics: { A: { values: { ROE: 12.1, PER: 9.8 }, fiscal_period: "FY2025", estimated: true },
               B: { values: { ROE: 3, PER: 8 }, fiscal_period: "FY2025" } },
    disclosures: { A: [{ date: "2025-01-01", title: "t", url: "https://d.com/x" }, { date: "d", title: "bad", url: "javascript:x" }] } };
  const m = T.tableModel(res);
  assert.deepStrictEqual(m.cols, ["ROE", "PER"]);
  assert.strictEqual(m.estimated, true);
  const [p, f, u] = m.groups;
  assert.strictEqual(p.rows[0].basis, "FY2025*");
  assert.deepStrictEqual(p.rows[0].cells, [{ text: "12.10%", miss: false }, { text: "9.80배", miss: false }]);
  assert.strictEqual(p.rows[0].saveable, true);
  assert.strictEqual(p.rows[0].disc[0].href, "https://d.com/x");
  assert.strictEqual(p.rows[0].disc[1].href, null);
  assert.deepStrictEqual(f.rows[0].cells[0], { text: "3.00% ✗", miss: true });
  assert.strictEqual(f.rows[0].cells[1].miss, false);
  assert.strictEqual(f.rows[0].saveable, false);
  assert.strictEqual(f.rows[0].reason, "ROE 미충족 (시가총액 확인 불가)");
  assert.strictEqual(u.rows[0].reason, "재무정보 없음, ZZZ");
  assert.deepStrictEqual(u.rows[0].cells.map((c) => c.text), ["-", "-"]);
  assert.strictEqual(u.rows[0].basis, "-");
  assert.strictEqual(T.tableModel({ ...res, conditions: { constraints: [] } }).groups[0].rows[0].disc, null);
  // limit 카드는 이전 턴 draft 오인 방지로 저장 불가
  assert.strictEqual(T.tableModel({ ...res, status: "limit" }).groups[0].rows[0].saveable, false);
});

test("parseHistory / pushHistory", () => {
  const sid = "s1", rec = (q) => ({ q, res: { status: "answered" }, at: "t" });
  assert.deepStrictEqual(T.parseHistory(JSON.stringify({ sid, items: [rec("a")] }), sid), [rec("a")]);
  for (const raw of [null, "", "{bad", "[]", JSON.stringify({ sid, items: {} }), JSON.stringify({ sid: "x", items: [rec("a")] })])
    assert.deepStrictEqual(T.parseHistory(raw, sid), [], String(raw));
  const mixed = JSON.stringify({ sid, items: [rec("a"), { q: 1, res: {} }, { q: "b", res: [] }, null] });
  assert.deepStrictEqual(T.parseHistory(mixed, sid), [rec("a")]);

  let items = [];
  for (let i = 0; i < 11; i++) items = T.pushHistory(items, rec("q" + i), sid).items;
  assert.strictEqual(items.length, 10);
  assert.strictEqual(items[0].q, "q1");
  assert.strictEqual(items[9].q, "q10");
  const p = T.pushHistory(items, rec("q11"), sid);
  assert.deepStrictEqual(T.parseHistory(p.raw, sid), p.items);
  const small = T.pushHistory([rec("a"), rec("b")], rec("c"), sid, 10, JSON.stringify({ sid, items: [rec("c")] }).length + 5);
  assert.deepStrictEqual(small.items.map((x) => x.q), ["c"]);
  assert.deepStrictEqual(T.pushHistory([rec("a")], rec("b"), sid, 10, 5), { items: [], raw: null });
});
