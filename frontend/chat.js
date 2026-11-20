/* D14 채팅 화면. 상단은 순수 영역(node 테스트 대상), 하단은 DOM. */
(function (root) {
  "use strict";
  var C = root.Common || (typeof require !== "undefined" ? require("./common.js") : null);

  // ---------- 순수 영역 ----------
  var SESSION_KEY = "invest_helper_session_id";
  var UUID_RE = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/;
  var STAGE = { analyze: "의도 분석", plan: "계획", execute: "실행", judge: "판정", respond: "응답 작성" };
  var TRIGGER = { no_pass: "조건 충족 종목 없음", no_candidates: "후보 종목 없음" };
  var CHANGE = { market_expand: "시장 확대", similar_industry: "유사 업종 확대", limit_up: "후보 수 상한 확대" };
  var BUSY_TEXT = "이전 요청을 처리 중입니다. 잠시 후 다시 시도하세요.";

  var arr = function (x) { return Array.isArray(x) ? x : []; };
  var str = function (x) { return typeof x === "string" ? x : ""; };
  var name = function (map, k) { return Object.prototype.hasOwnProperty.call(map, k) ? map[k] : String(k); };

  function uuidV4(cryptoObj) {
    cryptoObj = cryptoObj || root.crypto;
    try { if (cryptoObj && cryptoObj.randomUUID) return cryptoObj.randomUUID().toLowerCase(); } catch (e) { /* 폴백 */ }
    var b = new Uint8Array(16);
    cryptoObj.getRandomValues(b);
    b[6] = (b[6] & 0x0f) | 0x40;
    b[8] = (b[8] & 0x3f) | 0x80;
    var h = Array.prototype.map.call(b, function (x) { return x.toString(16).padStart(2, "0"); }).join("");
    return [h.slice(0, 8), h.slice(8, 12), h.slice(12, 16), h.slice(16, 20), h.slice(20)].join("-");
  }

  // store: {get,set}. 유효 UUID 아니면 새로 만들어 저장.
  function getSessionId(store, cryptoObj) {
    var sid = null;
    try { sid = store.get(SESSION_KEY); } catch (e) { sid = null; }
    if (typeof sid === "string") sid = sid.toLowerCase();
    if (typeof sid !== "string" || !UUID_RE.test(sid)) {
      sid = uuidV4(cryptoObj);
      try { store.set(SESSION_KEY, sid); } catch (e) { /* 메모리 유지 */ }
    }
    return sid;
  }

  function tsRange(s) {
    var t = C.fmtTime(s.started_at) + "~" + (s.ended_at == null ? "" : C.fmtTime(s.ended_at));
    return s.ended_at == null ? t + " (중단)" : t;
  }

  // steps+tool_calls를 seq 오름차순 병합, round 증가 시 구분 행. 반환 [{kind,text}]
  function timelineRows(steps, toolCalls, replans) {
    var items = [];
    arr(steps).forEach(function (s) { items.push({ k: "step", v: s || {} }); });
    arr(toolCalls).forEach(function (t) { items.push({ k: "tool", v: t || {} }); });
    var isNum = function (n) { return typeof n === "number" && isFinite(n); };
    items = items.map(function (it, i) { it.i = i; return it; }).sort(function (a, b) {
      var an = isNum(a.v.seq), bn = isNum(b.v.seq);
      if (an && bn) return a.v.seq - b.v.seq || a.i - b.i;
      if (an !== bn) return an ? -1 : 1;
      return a.i - b.i;
    });
    var rp = arr(replans), rows = [], prev = null;
    items.forEach(function (it) {
      var v = it.v, r = v.round;
      if (isNum(r)) {
        if (prev !== null && r > prev && rp.length) {
          var p = rp[r - 1];
          rows.push({ kind: "sep", text: p
            ? "── 재계획 " + r + ": " + name(TRIGGER, p.trigger) + " → " + name(CHANGE, p.change) + " ──"
            : "── 재계획 " + r + " ──" });
        }
        if (prev === null || r > prev) prev = r;
      }
      if (it.k === "step") {
        var t = name(STAGE, v.stage) + " · " + tsRange(v);
        if (v.stage === "analyze") {
          if (v.intent) t += " · " + v.intent;
          if (v.reason) t += " · " + v.reason;
        }
        rows.push({ kind: "step", text: t });
      } else if (v.type === "call") {
        rows.push({ kind: "call", text: "▶ " + v.tool + " 호출 " + JSON.stringify(v.input) + (v.cache_hit ? " (캐시)" : "") });
      } else {
        rows.push({ kind: "result", text: "◀ " + v.tool + " " + (v.ok ? "성공" : "실패 " + v.error_code) +
          " · " + v.duration_ms + "ms · " + str(v.summary) });
      }
    });
    return rows;
  }

  function buildSaveBody(sid, stockCode, nameVal, memoVal, reviewVal) {
    var body = { session_id: sid, stock_code: stockCode };
    var n = String(nameVal == null ? "" : nameVal).trim();
    if (n) body.name = n;
    var m = String(memoVal == null ? "" : memoVal);
    if (m) body.memo = m;
    var r = String(reviewVal == null ? "" : reviewVal);
    if (r) body.review_date = r;
    return body;
  }

  // ---- D14c 대시보드 순수 함수 ----
  var INTENT_TEXT = { screen: "종목 탐색", refine: "조건 변경", lookup: "종목 조회", report_save: "리포트 저장",
                      chitchat: "일반 대화", filing_qa: "공시 질의" };
  var METRICS = ["ROE", "ROA", "OPM", "PER", "PBR", "PSR", "DEBT_RATIO"];
  var METRIC_LABEL = { ROE: "ROE", ROA: "ROA", OPM: "영업이익률", PER: "PER", PBR: "PBR", PSR: "PSR", DEBT_RATIO: "부채비율" };
  var PCT = { ROE: 1, ROA: 1, OPM: 1, DEBT_RATIO: 1 };
  var isObj = function (x) { return x !== null && typeof x === "object" && !Array.isArray(x); };
  var isNum = function (n) { return typeof n === "number" && isFinite(n); };
  var has = function (o, k) { return Object.prototype.hasOwnProperty.call(o, k); };
  var label = function (m) { return has(METRIC_LABEL, m) ? METRIC_LABEL[m] : String(m); };
  var unit = function (m) { return PCT[m] ? "%" : "배"; };

  function fmtMetric(m, v) { return isNum(v) ? v.toFixed(2) + unit(m) : "-"; }

  function condChips(cond) {
    if (!isObj(cond)) return [];
    var cs = arr(cond.constraints), pend = arr(cond.pending_metrics), out = [];
    if (!cond.industry && !cs.length && !pend.length) return [];
    if (cond.industry) out.push({ label: "산업 " + cond.industry, prefill: "산업을 " });
    out.push({ label: cond.market ? "시장 " + cond.market : "시장 전체", prefill: "시장을 " });
    if (cond.base_date) out.push({ label: "기준일 " + cond.base_date, prefill: "기준일을 " });
    cs.forEach(function (c) {
      if (!isObj(c)) return;
      var rhs = c.ref === "industry_avg" ? "산업 평균" : String(c.value) + unit(c.metric);
      out.push({ label: label(c.metric) + " " + (c.op === "<=" ? "≤" : "≥") + " " + rhs, prefill: label(c.metric) + " 기준을 " });
    });
    pend.forEach(function (m) { out.push({ label: label(m) + " 기준값 필요", prefill: label(m) + " 기준을 " }); });
    if (cond.want_disclosures === true) out.push({ label: "공시 포함", prefill: null });
    return out;
  }

  function groupVerdicts(vs) {
    var g = { PASS: [], FAIL: [], UNDECIDABLE: [] };
    arr(vs).forEach(function (v) { if (isObj(v) && has(g, v.status)) g[v.status].push(v); });
    return g;
  }

  function viewKind(res) {
    res = isObj(res) ? res : {};
    var st = res.status, it = res.intent;
    if (st === "asked" || st === "error") return "text";
    if ((it === "screen" || it === "refine") && arr(res.verdicts).length) return "table";
    if (it === "lookup" && isObj(res.metrics) && Object.keys(res.metrics).length) return "metric";
    if (it === "filing_qa" && arr(res.evidence).length) return "evidence";
    return "text";
  }

  function headline(answer) { return str(answer).split("\n\n")[0]; }

  function avgText(ia) {
    var p = isObj(ia) && isObj(ia.PER) ? ia.PER : null;
    if (!p) return null;
    var v = isNum(p.value) ? p.value.toFixed(2) + "배" : "산출 불가";
    return "산업 평균 PER " + v + " (n=" + p.n + ", 후보 집합 중앙값 · 공식 평균 아님)" + (isNum(p.n) && p.n < 3 ? " · 표본 부족" : "");
  }

  function reasonText(c) { return has(C.REASON_TEXT, c) ? C.REASON_TEXT[c] : String(c); }

  function tableModel(res) {
    res = isObj(res) ? res : {};
    var cond = isObj(res.conditions) ? res.conditions : {}, mets = isObj(res.metrics) ? res.metrics : {};
    var discs = isObj(res.disclosures) ? res.disclosures : {};
    var cols = [];
    arr(cond.constraints).forEach(function (c) { if (isObj(c) && cols.indexOf(c.metric) < 0) cols.push(c.metric); });
    var save = {};
    if (res.status !== "limit") arr(res.saveable).forEach(function (s) { if (isObj(s)) save[s.stock_code] = 1; });
    var estimated = false, g = groupVerdicts(res.verdicts);
    var groups = ["PASS", "FAIL", "UNDECIDABLE"].filter(function (k) { return g[k].length; }).map(function (k) {
      return { status: k, rows: g[k].map(function (v) {
        var code = v.stock_code, m = isObj(mets[code]) ? mets[code] : {}, vals = isObj(m.values) ? m.values : {};
        var failed = arr(v.failed), reasons = arr(v.reasons), reason = "";
        if (k === "FAIL") {
          reason = failed.map(label).join(", ") + " 미충족";
          var extra = reasons.filter(function (r) { return r !== "THRESHOLD_NOT_MET"; }).map(reasonText);
          if (extra.length) reason += " (" + extra.join(", ") + ")";
        } else if (k === "UNDECIDABLE") reason = reasons.map(reasonText).join(", ");
        if (m.estimated === true) estimated = true;
        return { code: code, name: str(v.stock_name) || code,
          cells: cols.map(function (c) {
            var miss = k === "FAIL" && failed.indexOf(c) >= 0;
            return { text: fmtMetric(c, vals[c]) + (miss ? " ✗" : ""), miss: miss };
          }),
          basis: m.fiscal_period ? m.fiscal_period + (m.estimated === true ? "*" : "") : "-",
          reason: reason,
          disc: cond.want_disclosures === true ? arr(discs[code]).map(function (d) {
            d = isObj(d) ? d : {};
            return { text: str(d.date) + " " + str(d.title), href: C.safeUrl(d.url) };
          }) : null,
          saveable: k === "PASS" && save[code] === 1 };
      }) };
    });
    return { cols: cols, groups: groups, estimated: estimated };
  }

  function statusLine(res) {
    res = isObj(res) ? res : {};
    var st = res.status;
    if (st === "answered") {
      if (viewKind(res) !== "table") return "분석 완료";
      var g = groupVerdicts(res.verdicts);
      return "분석 완료 — 충족 " + g.PASS.length + " · 불충족 " + g.FAIL.length + " · 판단불가 " + g.UNDECIDABLE.length;
    }
    if (st === "asked") return "추가 정보가 필요합니다";
    if (st === "limit") return "조건을 충족한 종목이 없습니다";
    return "처리하지 못했습니다";
  }

  // ---- D14d 카드 복원 (sessionStorage) ----
  var HISTORY_KEY = "invest_helper_cards", HISTORY_MAX = 10, HISTORY_MAX_LEN = 1000000;

  function parseHistory(raw, sid) {
    var v;
    try { v = JSON.parse(raw); } catch (e) { return []; }
    if (!isObj(v) || v.sid !== sid || !Array.isArray(v.items)) return [];
    return v.items.filter(function (x) { return isObj(x) && typeof x.q === "string" && isObj(x.res); });
  }

  function pushHistory(items, rec, sid, max, maxLen) {
    max = max == null ? HISTORY_MAX : max; maxLen = maxLen == null ? HISTORY_MAX_LEN : maxLen;
    var out = items.concat([rec]).slice(-max);
    while (out.length) {
      var raw = JSON.stringify({ sid: sid, items: out });
      if (raw.length <= maxLen) return { items: out, raw: raw };
      out = out.slice(1);
    }
    return { items: [], raw: null };
  }

  var exported = { HISTORY_KEY: HISTORY_KEY, HISTORY_MAX: HISTORY_MAX, HISTORY_MAX_LEN: HISTORY_MAX_LEN,
                   parseHistory: parseHistory, pushHistory: pushHistory, SESSION_KEY: SESSION_KEY, UUID_RE: UUID_RE, uuidV4: uuidV4, getSessionId: getSessionId,
                   timelineRows: timelineRows, buildSaveBody: buildSaveBody, INTENT_TEXT: INTENT_TEXT, METRICS: METRICS,
                   METRIC_LABEL: METRIC_LABEL, fmtMetric: fmtMetric, condChips: condChips, groupVerdicts: groupVerdicts,
                   viewKind: viewKind, headline: headline, statusLine: statusLine, avgText: avgText, tableModel: tableModel };

  // ---------- DOM 영역 ----------
  function init() {
    var $ = function (id) { return document.getElementById(id); };
    var el = C.el;
    var log = $("log"), status = $("status"), banner = $("banner"), form = $("form"), empty = $("empty"),
        msg = $("msg"), send = $("send"), newChat = $("new-chat"), disc = $("disclaimer"),
        chipsEl = $("chips"), tl = $("timeline"), sideSum = $("side-sum"), sidePanel = $("side-panel"), side = $("side");
    var store = { get: C.storageGet, set: C.storageSet };
    var sid = getSessionId(store), busy = false, seq = 0;
    function ssGet(k) { try { return sessionStorage.getItem(k); } catch (e) { return null; } }
    function ssSet(k, v) { try { sessionStorage.setItem(k, v); } catch (e) { /* 조용히 포기 */ } }
    function ssDel(k) { try { sessionStorage.removeItem(k); } catch (e) { /* 조용히 포기 */ } }
    var hist = parseHistory(ssGet(HISTORY_KEY), sid);
    if (window.matchMedia && matchMedia("(max-width: 899px)").matches) sidePanel.open = false;

    function setBusy(b) {
      busy = b;
      msg.disabled = send.disabled = newChat.disabled = b;
      Array.prototype.forEach.call(chipsEl.querySelectorAll("button"), function (x) { x.disabled = b; });
      form.setAttribute("aria-busy", b ? "true" : "false");
      status.textContent = b ? "분석 중입니다… (최대 수십 초 걸릴 수 있습니다)" : "";
    }
    function syncEmpty() { empty.hidden = log.children.length > 0; }

    function renderChips(cond) {
      chipsEl.textContent = "";
      var cs = condChips(cond);
      if (!cs.length) { chipsEl.appendChild(el("li", { class: "chip-none", text: "아직 조건이 없습니다" })); return; }
      cs.forEach(function (c) {
        if (c.prefill == null) { chipsEl.appendChild(el("li", { class: "chip-static", text: c.label })); return; }
        var b = el("button", { class: "chip", text: c.label, attrs: { type: "button", "aria-label": c.label + " 조건 변경 문장 입력" } });
        b.addEventListener("click", function () {
          msg.value = c.prefill; msg.focus(); msg.setSelectionRange(msg.value.length, msg.value.length);
        });
        chipsEl.appendChild(el("li", {}, [b]));
      });
    }

    function showTimeline(res, li) {
      tl.textContent = "";
      timelineRows(res.steps, res.tool_calls, res.replans).forEach(function (r) {
        tl.appendChild(el("li", { class: "tl-" + r.kind, text: r.text }));
      });
      sideSum.textContent = "실행 과정 (단계 " + arr(res.steps).length + " · 도구 " +
        arr(res.tool_calls).filter(function (t) { return t && t.type === "call"; }).length + " · 재계획 " + arr(res.replans).length + ")";
      Array.prototype.forEach.call(log.querySelectorAll(".selected"), function (n) { n.classList.remove("selected"); });
      if (li) li.classList.add("selected");
    }

    // 카드당 저장 패널 1개. open(item, btn)
    function makeSavePanel() {
      var uid = "s" + Math.random().toString(36).slice(2, 8), cur = null;
      var box = el("div", { class: "save-panel" }); box.hidden = true;
      var title = el("strong");
      var inName = el("input", { attrs: { type: "text", id: uid + "n", maxlength: "100" } });
      var inMemo = el("textarea", { attrs: { id: uid + "m", maxlength: "1000", rows: "2" } });
      var inReview = el("input", { attrs: { type: "date", id: uid + "r" } });
      var ok = el("button", { text: "확정", attrs: { type: "button" } });
      var cancel = el("button", { text: "취소", attrs: { type: "button" } });
      var msgEl = el("div", { class: "save-msg", attrs: { "aria-live": "polite" } });
      [title, el("label", { text: "이름 (선택)", attrs: { for: uid + "n" } }), inName,
       el("label", { text: "메모 (선택)", attrs: { for: uid + "m" } }), inMemo,
       el("label", { text: "재검토 예정일 (선택)", attrs: { for: uid + "r" } }), inReview, ok, cancel, msgEl]
        .forEach(function (n) { box.appendChild(n); });
      cancel.addEventListener("click", function () { box.hidden = true; if (cur) cur.btn.focus(); });
      ok.addEventListener("click", async function () {
        var c = cur; if (!c) return;
        ok.disabled = cancel.disabled = true;
        var r = await C.api("POST", "/api/reports", buildSaveBody(sid, c.code, inName.value, inMemo.value, inReview.value));
        ok.disabled = cancel.disabled = false;
        msgEl.textContent = "";
        if (r.ok && r.data) {
          var dup = arr(r.data.duplicate_of);
          msgEl.removeAttribute("role");
          msgEl.appendChild(document.createTextNode("저장했습니다. (리포트 #" + r.data.id + ") "));
          msgEl.appendChild(el("a", { text: "저장된 리포트 보기", attrs: { href: "reports.html" } }));
          if (dup.length) msgEl.appendChild(el("div", { text: "같은 종목·기준일로 이미 저장된 리포트가 " + dup.length +
            "건 있습니다(#" + dup.join(", #") + "). 중복은 reports.html에서 삭제할 수 있습니다." }));
          c.btn.textContent = "저장됨"; c.btn.disabled = true;
          inName.value = inMemo.value = inReview.value = "";
        } else {
          msgEl.textContent = r.data.code === "DRAFT_NOT_FOUND"
            ? "저장할 결과가 없습니다. 먼저 해당 종목을 조회하세요." : r.data.message;
          msgEl.setAttribute("role", "alert");
        }
      });
      function open(code, name, btn) {
        cur = { code: code, btn: btn };
        title.textContent = name + "(" + code + ") 리포트 저장";
        inName.placeholder = name + " 리포트";
        msgEl.textContent = ""; msgEl.removeAttribute("role");
        box.hidden = false; inName.focus();
      }
      return { box: box, open: open };
    }
    function saveBtn(sp, code, name) {
      var b = el("button", { class: "save-btn", text: "저장", attrs: { type: "button", "aria-label": name + "(" + code + ") 리포트 저장" } });
      b.addEventListener("click", function () { sp.open(code, name, b); });
      return b;
    }

    function linkNode(text, href) {
      return href ? el("a", { text: text, attrs: { href: href, target: "_blank", rel: "noopener noreferrer" } })
                  : document.createTextNode(text);
    }

    function renderTable(res, sp, body) {
      var tm = tableModel(res), cond = isObj(res.conditions) ? res.conditions : {};
      var hasDisc = cond.want_disclosures === true;
      body.appendChild(el("p", { class: "headline", text: headline(res.answer) }));
      var avg = avgText(res.industry_avg);
      if (avg) body.appendChild(el("p", { class: "hint", text: avg }));
      if (arr(res.replans).length) body.appendChild(el("p", { class: "hint", text: "↻ 재계획 " + res.replans.length + "회: " +
        res.replans.map(function (p) { return name(TRIGGER, p && p.trigger) + "→" + name(CHANGE, p && p.change); }).join(", ") }));
      if (res.status === "limit") body.appendChild(el("p", { class: "hint",
        text: "조건을 충족한 종목이 없습니다. 위 조건 칩을 눌러 기준을 바꿔 다시 분석하세요." }));
      var openStatus = res.status === "limit" ? "FAIL" : "PASS", firstOpened = false;
      var TITLE = { PASS: "충족", FAIL: "불충족", UNDECIDABLE: "판단불가" };
      var anySave = tm.groups.some(function (g) { return g.rows.some(function (r) { return r.saveable; }); });
      tm.groups.forEach(function (g) {
        var head = ["종목"].concat(tm.cols.map(label), ["재무기준", "사유"], hasDisc ? ["공시"] : [], anySave ? ["동작"] : []);
        var tr = el("tr", {}, head.map(function (h) { return el("th", { text: h, attrs: { scope: "col" } }); }));
        var tbody = el("tbody");
        g.rows.forEach(function (r) {
          var row = el("tr", {}, [el("th", { text: r.name + " (" + r.code + ")", attrs: { scope: "row" } })]);
          r.cells.forEach(function (c) { row.appendChild(el("td", { class: "num" + (c.miss ? " miss" : ""), text: c.text })); });
          row.appendChild(el("td", { text: r.basis }));
          row.appendChild(el("td", { text: r.reason }));
          if (hasDisc) {
            var td = el("td");
            if (!r.disc || !r.disc.length) td.textContent = "-";
            r.disc && r.disc.forEach(function (d) { var dv = el("div"); dv.appendChild(linkNode(d.text, d.href)); td.appendChild(dv); });
            row.appendChild(td);
          }
          if (anySave) { var ta = el("td"); if (r.saveable) ta.appendChild(saveBtn(sp, r.code, r.name)); row.appendChild(ta); }
          tbody.appendChild(row);
        });
        var d = el("details", {}, [el("summary", { text: TITLE[g.status] + " " + g.rows.length }),
          el("div", { class: "tbl-wrap" }, [el("table", { class: "metrics" }, [el("thead", {}, [tr]), tbody])])]);
        if (!firstOpened && g.status === openStatus) { d.open = true; firstOpened = true; }
        body.appendChild(d);
      });
      if (!firstOpened && body.querySelector("details")) body.querySelector("details").open = true;
      if (tm.estimated) body.appendChild(el("p", { class: "hint", text: "* 요청 기준 연도 재무가 없어 직전 확정 연도 값으로 대체(추정)" }));
    }

    function renderMetric(res, sp, body) {
      var code = Object.keys(res.metrics)[0], m = res.metrics[code] || {}, vals = isObj(m.values) ? m.values : {};
      var rs = isObj(m.reasons) ? m.reasons : {};
      var it = arr(res.saveable).filter(function (s) { return s && s.stock_code === code; })[0];
      var nm = it && it.stock_name || code;
      body.appendChild(el("p", { class: "headline", text: headline(res.answer) }));
      body.appendChild(el("p", { text: nm + "(" + code + ") · 재무기준 " + (m.fiscal_period || "-") + (m.estimated === true ? "*" : "") }));
      var dl = el("dl", { class: "metric-grid" });
      METRICS.forEach(function (k) {
        var v = vals[k];
        dl.appendChild(el("div", {}, [el("dt", { text: label(k) }),
          el("dd", { text: isNum(v) ? fmtMetric(k, v) : (has(C.REASON_TEXT, rs[k]) ? C.REASON_TEXT[rs[k]] : "재무정보 없음") })]));
      });
      body.appendChild(dl);
      if (it) body.appendChild(saveBtn(sp, code, nm));
    }

    function renderEvidence(res, body) {
      body.appendChild(el("p", { class: "headline", text: headline(res.answer) }));
      var ol = el("ol", { class: "evidence" });
      res.evidence.forEach(function (e, i) {
        e = isObj(e) ? e : {};
        var href = C.safeUrl(e.url);
        var meta = "[" + (i + 1) + "] " + str(e.corp_name) + " · " + str(e.title) + " · " + str(e.date) + " · 접수번호 " + str(e.rcept_no);
        var li = el("li", {}, [el("div", { text: meta }), el("div", { class: "hint", text: "섹션: " + str(e.section) }),
          el("blockquote", { text: str(e.text) })]);
        li.appendChild(href ? el("a", { text: "DART 원문 보기", attrs: { href: href, target: "_blank", rel: "noopener noreferrer",
          "aria-label": str(e.corp_name) + " " + str(e.title) + " DART 원문 (새 창)" } }) : el("span", { text: str(e.url) }));
        ol.appendChild(li);
      });
      body.appendChild(ol);
      body.appendChild(el("p", { class: "hint", text: "※ 공시 원문 발췌이며, 공시 접수일 이후 변동은 반영되지 않습니다." }));
    }

    function cardHead(li, question, res, at) {
      var id = "q" + (++seq);
      var h = el("div", { class: "run-head" }, [el("h2", { text: question, attrs: { id: id } })]);
      if (res) {
        var st = str(res.status);
        if (str(res.intent)) h.appendChild(el("span", { class: "badge", text: has(INTENT_TEXT, res.intent) ? INTENT_TEXT[res.intent] : res.intent }));
        if (st && st !== "answered") h.appendChild(el("span", { class: "badge", text: C.STATUS_TEXT[st] || st }));
        h.appendChild(el("time", { class: "hint", text: C.fmtTime(at || new Date().toISOString()) }));
      }
      var art = el("article", { attrs: { "aria-labelledby": id } }, [h]);
      li.appendChild(art);
      return art;
    }

    function renderCard(res, question, at) {
      res = isObj(res) ? res : {};
      var st = str(res.status), kind = viewKind(res);
      var li = el("li", { class: "run" + (st && st !== "answered" ? " status-" + st : "") });
      var art = cardHead(li, question, res, at), body = el("div", { class: "run-body" }), sp = makeSavePanel();
      art.appendChild(body);
      if (kind === "table") renderTable(res, sp, body);
      else if (kind === "metric") renderMetric(res, sp, body);
      else if (kind === "evidence") renderEvidence(res, body);
      else body.appendChild(el("div", { class: "answer" }, C.linkify(str(res.answer))));
      body.appendChild(sp.box);
      var foot = el("div", { class: "run-foot" });
      var pb = el("button", { text: "과정 보기", attrs: { type: "button", "aria-label": question + " 실행 과정 보기" } });
      pb.addEventListener("click", function () {
        showTimeline(res, li); sidePanel.open = true;
        if (window.matchMedia && matchMedia("(max-width: 899px)").matches) side.scrollIntoView();
      });
      foot.appendChild(pb);
      if (kind !== "text") foot.appendChild(el("details", {}, [el("summary", { text: "전체 응답 텍스트" }),
        el("div", { class: "answer" }, C.linkify(str(res.answer)))]));
      art.appendChild(foot);
      if (str(res.disclaimer)) art.appendChild(el("p", { class: "disclaimer", text: res.disclaimer }));
      return li;
    }

    // 이전 카드 저장 비활성(최신 결과만 저장)
    function disableOldSaves() {
      Array.prototype.forEach.call(log.querySelectorAll(".save-btn"), function (b) { b.disabled = true; });
      Array.prototype.forEach.call(log.querySelectorAll(".save-panel"), function (p) { p.hidden = true; });
    }

    function pendingCard(question) {
      var li = el("li", { class: "run", attrs: { "aria-busy": "true" } });
      cardHead(li, question, null).appendChild(el("p", { class: "hint", text: "분석 중…" }));
      return li;
    }

    async function submit() {
      var text = msg.value.trim();
      if (!text || busy) return;
      setBusy(true);
      var tmp = pendingCard(text);
      log.insertBefore(tmp, log.firstChild); syncEmpty();
      var r = await C.api("POST", "/api/chat", { session_id: sid, message: text });
      setBusy(false);
      if (r.ok) {
        var res = isObj(r.data) ? r.data : {};
        disableOldSaves();
        var at = new Date().toISOString();
        var card = renderCard(res, text, at);
        tmp.replaceWith(card);
        banner.hidden = res.missing_api_key !== true;
        banner.textContent = res.missing_api_key === true ? "API 키가 설정되지 않았습니다. 서버의 .env를 확인하세요." : "";
        if (str(res.disclaimer)) disc.textContent = res.disclaimer;
        if (isObj(res.conditions)) renderChips(res.conditions);
        showTimeline(res, card);
        status.textContent = statusLine(res);
        var p = pushHistory(hist, { q: text, res: res, at: at }, sid);
        hist = p.items; if (p.raw) ssSet(HISTORY_KEY, p.raw); else ssDel(HISTORY_KEY);
        msg.value = ""; msg.focus();
        return;
      }
      if (r.status === 409 || r.status === 422) {
        tmp.remove(); syncEmpty();
        status.textContent = r.status === 409 ? BUSY_TEXT : r.data.message;  // setBusy(false) 이후에 써야 남는다
      } else {
        var errCard = renderCard({ status: "error", answer: r.data.message }, text);
        tmp.replaceWith(errCard);
        status.textContent = statusLine({ status: "error" });
      }
      msg.focus();
    }

    form.addEventListener("submit", function (e) { e.preventDefault(); submit(); });
    msg.addEventListener("keydown", function (e) {
      if (e.key === "Enter" && !e.shiftKey && !e.isComposing && e.keyCode !== 229) { e.preventDefault(); submit(); }
    });
    newChat.addEventListener("click", function () {
      if (busy) return;
      sid = uuidV4();
      C.storageSet(SESSION_KEY, sid);
      hist = []; ssDel(HISTORY_KEY);
      log.textContent = ""; tl.textContent = ""; sideSum.textContent = "실행 과정";
      status.textContent = "";
      banner.hidden = true; banner.textContent = "";
      renderChips(null); syncEmpty();
    });
    hist.forEach(function (h) {  // 오래된 순, 최신이 위
      disableOldSaves();
      log.insertBefore(renderCard(h.res, h.q, str(h.at) || undefined), log.firstChild);
    });
    if (hist.length) {
      var last = hist[hist.length - 1].res;
      showTimeline(last, log.firstChild);
      renderChips(isObj(last.conditions) ? last.conditions : null);
      if (str(last.disclaimer)) disc.textContent = last.disclaimer;
    } else renderChips(null);
    syncEmpty();
  }

  if (typeof document !== "undefined") document.addEventListener("DOMContentLoaded", init);
  if (typeof module !== "undefined") module.exports = exported;
})(typeof window !== "undefined" ? window : globalThis);
