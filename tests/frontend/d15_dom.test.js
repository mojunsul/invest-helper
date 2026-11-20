// 가짜 DOM으로 renderDetail 결과(수정·삭제 버튼 존재)를 확인한다.
const test = require("node:test");
const assert = require("node:assert");

function mk(tag) {
  const e = {
    tagName: tag, children: [], attrs: {}, listeners: {}, hidden: false, className: "", value: "", _t: "",
    appendChild(c) { this.children.push(c); return c; },
    setAttribute(k, v) { this.attrs[k] = v; },
    addEventListener(t, f) { this.listeners[t] = f; },
    remove() {}, focus() {},
    get textContent() { return this._t + this.children.map((c) => c.textContent).join(""); },
    set textContent(v) { this._t = String(v); this.children = []; },
  };
  return e;
}
const byId = {};
globalThis.document = {
  createElement: mk, createTextNode: (t) => ({ textContent: t }),
  getElementById: (id) => (byId[id] = byId[id] || mk("div")),
};
globalThis.window = { addEventListener() {} };
globalThis.location = { hash: "#id=1" };
const rep = { id: 1, name: "N", stock_name: "S", stock_code: "005930", base_date: "2024", created_at: "2024-01-01T00:00:00Z",
  updated_at: "2024-01-01T00:00:00Z", metrics: { values: { ROE: 1 }, reasons: {}, fiscal_period: "2024", estimated: false },
  industry_avg: {}, disclosures: null, memo: "", disclaimer: "d" };
globalThis.fetch = async () => ({ ok: true, status: 200, json: async () => rep });

function find(n, pred, out = []) {
  if (pred(n)) out.push(n);
  (n.children || []).forEach((c) => find(c, pred, out));
  return out;
}

test("renderDetail은 수정·삭제 버튼을 모두 그린다", async () => {
  require("../../frontend/reports.js");
  await new Promise((r) => setTimeout(r, 20));
  const btns = find(byId.detail, (n) => n.tagName === "button").map((b) => b.textContent);
  assert.ok(btns.includes("이름·메모·재검토일·내 기준 수정"), btns.join("|"));
  assert.ok(btns.includes("삭제"), btns.join("|"));
});

test("D12b: 상세에 재검토 예정일 줄, 수정 폼에 date 입력", async () => {
  const txt = (n) => n.textContent;
  assert.ok(txt(byId.detail).includes("재검토 예정일 -"));
  const edit = find(byId.detail, (n) => n.tagName === "button" && n.textContent === "이름·메모·재검토일·내 기준 수정")[0];
  edit.listeners.click();
  const ri = find(byId.detail, (n) => n.tagName === "input" && n.attrs.id === "edit-review");
  assert.strictEqual(ri.length, 1);
  assert.strictEqual(ri[0].attrs.type, "date");
});

test("D19: 상세 내 기준 줄, 폼에 edit-per·edit-roe", () => {
  assert.ok(byId.detail.textContent.includes("내 기준 -"));
  // 앞 테스트에서 이미 수정 폼을 열었다
  for (const id of ["edit-per", "edit-roe"]) assert.ok(find(byId.detail, (n) => n.attrs.id === id).length >= 1);
});
