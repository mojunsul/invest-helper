const test = require("node:test");
const assert = require("node:assert");
const C = require("../../frontend/common.js");
const R = require("../../frontend/reports.js");

test("fmtMetric", () => {
  assert.strictEqual(R.fmtMetric("ROE", 12.345), "12.35%");
  assert.strictEqual(R.fmtMetric("DEBT_RATIO", 50), "50.00%");
  assert.strictEqual(R.fmtMetric("PER", 8), "8.00배");
  assert.strictEqual(R.fmtMetric("PER", null), "-");
  assert.strictEqual(R.fmtMetric("PER", undefined), "-");
});

test("metricCell", () => {
  const m = { values: { ROE: 10, PER: null, PBR: null, PSR: null }, reasons: { PER: "NEGATIVE_EARNINGS", PBR: "WEIRD_CODE" } };
  assert.strictEqual(R.metricCell(m, "ROE"), "10.00%");
  assert.strictEqual(R.metricCell(m, "PER"), "순이익 적자(PER 산출 불가)");
  assert.strictEqual(R.metricCell(m, "PBR"), "WEIRD_CODE");
  assert.strictEqual(R.metricCell(m, "PSR"), "재무정보 없음");
  assert.strictEqual(R.metricCell(m, "ROA"), "재무정보 없음"); // 키 누락
  assert.strictEqual(R.metricCell(null, "ROE"), "재무정보 없음");
  assert.strictEqual(R.metricCell({ values: {}, reasons: { ROE: "constructor" } }, "ROE"), "constructor");
});

test("avgCell", () => {
  assert.strictEqual(R.avgCell({ PER: { value: 12.5, n: 7 } }, "PER"), "12.50배 (n=7)");
  assert.strictEqual(R.avgCell({ PER: { value: null, n: 0 } }, "PER"), "-");
  assert.strictEqual(R.avgCell({}, "PER"), "-");
  assert.strictEqual(R.avgCell(null, "PER"), "-");
});

test("disclosureView", () => {
  assert.strictEqual(R.disclosureView(null).kind, "none");
  assert.strictEqual(R.disclosureView([]).kind, "empty");
  assert.strictEqual(R.disclosureView({ a: 1 }).kind, "invalid");
  const v = R.disclosureView([
    { title: "A", date: "2024-01-01", url: "https://a.com/x" },
    { title: "B", date: "d", url: "javascript:alert(1)" },
    { title: "C", url: "data:text/html,x" },
    { title: "D", url: "/rel" },
    { date: "x", url: "https://a.com" },
    "str",
    { title: 5 },
  ]);
  assert.strictEqual(v.kind, "list");
  assert.deepStrictEqual(v.items.map((i) => i.title), ["A", "B", "C", "D"]);
  assert.strictEqual(v.items[0].href, "https://a.com/x");
  assert.strictEqual(v.items[0].date, "2024-01-01");
  assert.deepStrictEqual(v.items.slice(1).map((i) => i.href), [null, null, null]);
});

test("parseHashId", () => {
  assert.strictEqual(R.parseHashId("#id=3"), 3);
  for (const h of ["#id=0", "#id=-1", "#id=1.5", "#id=3abc", "#id=<script>", "", "#", "#id=", "id=3", null, undefined])
    assert.strictEqual(R.parseHashId(h), null, String(h));
});

test("buildPatchBody", () => {
  const cur = { name: "A", memo: "m" };
  assert.deepStrictEqual(R.buildPatchBody(cur, "B", "m"), { body: { name: "B" } });
  assert.deepStrictEqual(R.buildPatchBody(cur, " A ", "n"), { body: { memo: "n" } });
  assert.deepStrictEqual(R.buildPatchBody(cur, "B", ""), { body: { name: "B", memo: "" } });
  assert.deepStrictEqual(R.buildPatchBody(cur, "A", "m"), { noop: true });
  assert.deepStrictEqual(R.buildPatchBody({ name: "A", memo: null }, "A", ""), { noop: true });
  assert.ok(R.buildPatchBody(cur, "   ", "m").error);
  assert.deepStrictEqual(R.buildPatchBody(cur, "A", "m", "2026-12-01"), { body: { review_date: "2026-12-01" } });
  assert.deepStrictEqual(R.buildPatchBody({ name: "A", memo: "m", review_date: "2026-12-01" }, "A", "m", ""), { body: { review_date: "" } });
  assert.deepStrictEqual(R.buildPatchBody({ name: "A", memo: "m", review_date: null }, "A", "m", ""), { noop: true });
  assert.deepStrictEqual(Object.keys(R.buildPatchBody(cur, "B", "n").body).sort(), ["memo", "name"]);
});

test("errText", () => {
  const e = (status, code, message) => ({ ok: false, status, data: { code, message } });
  assert.match(R.errText(e(404, "REPORT_NOT_FOUND", "x")), /찾을 수 없습니다/);
  assert.strictEqual(R.errText(e(422, "INVALID_REQUEST", "요청 형식이 올바르지 않습니다.")), "요청 형식이 올바르지 않습니다.");
  assert.strictEqual(R.errText(e(500, "INTERNAL_ERROR", "요청을 처리하지 못했습니다.")), "요청을 처리하지 못했습니다.");
  assert.strictEqual(R.errText(e(0, "NETWORK_ERROR", "n")), "서버에 연결하지 못했습니다.");
  assert.strictEqual(R.errText(e(418, "WHATEVER", "서버문구")), "서버문구");
  assert.strictEqual(R.errText(e(500, "HTTP_ERROR", "")), "요청에 실패했습니다.");
  assert.strictEqual(R.errText({ ok: false, status: 500 }), "요청에 실패했습니다.");
});

test("fmtDateTime", () => {
  assert.match(R.fmtDateTime("2024-05-06T01:02:03+00:00"), /^\d{4}-\d{2}-\d{2} \d{2}:\d{2}$/);
  assert.strictEqual(R.fmtDateTime("garbage"), "garbage");
  assert.strictEqual(R.fmtDateTime(null), "");
});

test("REASON_TEXT 9키", () => {
  assert.strictEqual(Object.keys(C.REASON_TEXT).length, 9);
  assert.strictEqual(C.REASON_TEXT.NEGATIVE_EQUITY, "자본잠식");
});
