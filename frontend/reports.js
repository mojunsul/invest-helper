/* D15 저장 리포트 화면. 외부 문자열은 항상 textContent(Common.el). */
(function () {
  "use strict";
  var C = typeof Common !== "undefined" ? Common : require("./common.js");

  var METRICS = ["ROE", "ROA", "OPM", "PER", "PBR", "PSR", "DEBT_RATIO"];
  var METRIC_LABEL = { ROE: "ROE", ROA: "ROA", OPM: "영업이익률", PER: "PER", PBR: "PBR", PSR: "PSR", DEBT_RATIO: "부채비율" };
  var PCT = { ROE: 1, ROA: 1, OPM: 1, DEBT_RATIO: 1 };
  var NOT_FOUND = "리포트를 찾을 수 없습니다(이미 삭제됐을 수 있습니다).";

  // ---------- 순수 영역 ----------
  function isObj(x) { return x !== null && typeof x === "object" && !Array.isArray(x); }
  function isNum(x) { return typeof x === "number" && isFinite(x); }

  function fmtMetric(m, v) {
    if (!isNum(v)) return "-";
    return PCT[m] ? v.toFixed(2) + "%" : v.toFixed(2) + "배";
  }

  function metricCell(metrics, m) {
    var vals = isObj(metrics) && isObj(metrics.values) ? metrics.values : {};
    if (isNum(vals[m])) return fmtMetric(m, vals[m]);
    var rs = isObj(metrics) && isObj(metrics.reasons) ? metrics.reasons : {};
    var code = rs[m];
    if (typeof code !== "string" || !code) return "재무정보 없음";
    return Object.prototype.hasOwnProperty.call(C.REASON_TEXT, code) ? C.REASON_TEXT[code] : code;
  }

  function avgCell(avg, m) {
    var a = isObj(avg) ? avg[m] : null;
    if (!isObj(a) || !isNum(a.value)) return "-";
    return fmtMetric(m, a.value) + " (n=" + a.n + ")";
  }

  function disclosureView(d) {
    if (d === null || d === undefined) return { kind: "none", items: [] };
    if (!Array.isArray(d)) return { kind: "invalid", items: [] };
    if (!d.length) return { kind: "empty", items: [] };
    var items = [];
    d.forEach(function (x) {
      if (!isObj(x) || typeof x.title !== "string") return;
      items.push({ title: x.title, date: typeof x.date === "string" ? x.date : "", href: C.safeUrl(x.url) });
    });
    return { kind: "list", items: items };
  }

  function parseHashId(h) {
    var m = /^#id=(\d+)$/.exec(typeof h === "string" ? h : "");
    if (!m) return null;
    var n = parseInt(m[1], 10);
    return Number.isSafeInteger(n) && n >= 1 ? n : null;
  }

  function buildPatchBody(cur, name, memo) {
    var n = String(name == null ? "" : name).trim();
    if (!n) return { error: "이름은 비울 수 없습니다." };
    var m = String(memo == null ? "" : memo);
    var c = isObj(cur) ? cur : {};
    var body = {};
    if (n !== c.name) body.name = n;
    if (m !== (c.memo == null ? "" : c.memo)) body.memo = m;
    return Object.keys(body).length ? { body: body } : { noop: true };
  }

  function errText(r) {
    var d = r && isObj(r.data) ? r.data : {};
    if ((r && r.status === 0) || d.code === "NETWORK_ERROR") return "서버에 연결하지 못했습니다.";
    if (d.code === "REPORT_NOT_FOUND") return NOT_FOUND;
    return typeof d.message === "string" && d.message ? d.message : "요청에 실패했습니다.";
  }

  function fmtDateTime(iso) {
    var d = new Date(iso);
    if (typeof iso !== "string" || isNaN(d.getTime())) return iso == null ? "" : String(iso);
    var p = function (n) { return String(n).padStart(2, "0"); };
    return d.getFullYear() + "-" + p(d.getMonth() + 1) + "-" + p(d.getDate()) + " " + p(d.getHours()) + ":" + p(d.getMinutes());
  }

  // ---------- DOM 영역 (init에서만) ----------
  function init() {
    var $ = function (id) { return document.getElementById(id); };
    var el = C.el;
    var msg = $("msg"), listEl = $("list"), emptyEl = $("empty"), detailEl = $("detail");
    var showMsg = function (t) { msg.textContent = t || ""; };
    var str = function (x) { return typeof x === "string" ? x : ""; };

    // 삭제 버튼 -> 인라인 확인. onDone(): 삭제 성공 또는 404일 때 호출.
    function deleteControl(id, onDone) {
      var box = el("span", { class: "del" });
      function idle() {
        box.textContent = "";
        var b = el("button", { text: "삭제", attrs: { type: "button" } });
        b.addEventListener("click", ask);
        box.appendChild(b);
        return b;
      }
      function ask() {
        box.textContent = "";
        var ok = el("button", { text: "삭제 확정", attrs: { type: "button" } });
        var no = el("button", { text: "취소", attrs: { type: "button" } });
        no.addEventListener("click", function () { idle().focus(); });
        ok.addEventListener("click", async function () {
          ok.disabled = no.disabled = true;
          var r = await C.api("DELETE", "/api/reports/" + id);
          if (r.ok) { showMsg("삭제했습니다."); onDone(); }
          else if (r.status === 404 && r.data.code === "REPORT_NOT_FOUND") { showMsg("이미 삭제된 리포트입니다."); onDone(); }
          else { showMsg(errText(r)); ok.disabled = no.disabled = false; }
        });
        box.appendChild(el("span", { class: "confirm", text: "정말 삭제할까요? " }, [ok, no]));
        ok.focus();
      }
      idle();
      return box;
    }

    function setEmpty(on) {
      emptyEl.textContent = "";
      emptyEl.hidden = !on;
      if (on) {
        emptyEl.appendChild(document.createTextNode("저장된 리포트가 없습니다. 채팅 화면에서 종목을 조회한 뒤 [저장]을 눌러 보세요. "));
        emptyEl.appendChild(el("a", { text: "채팅으로 이동", attrs: { href: "index.html" } }));
      }
    }

    async function loadList() {
      listEl.textContent = "";
      setEmpty(false);
      var r = await C.api("GET", "/api/reports");
      if (!r.ok) { showMsg(errText(r)); return; }
      var rows = Array.isArray(r.data) ? r.data : [];
      if (!rows.length) { setEmpty(true); return; }
      rows.forEach(function (x) {
        if (!isObj(x) || !(parseInt(x.id, 10) >= 1)) return;
        var id = parseInt(x.id, 10);
        var open = el("button", { class: "link", text: str(x.name) || "(이름 없음)", attrs: { type: "button" } });
        open.addEventListener("click", function () { location.hash = "#id=" + id; });
        var li = el("li");
        li.appendChild(open);
        li.appendChild(document.createTextNode(" " + str(x.stock_name) + "(" + str(x.stock_code) + ") · 기준일 " +
          (typeof x.base_date === "string" && x.base_date ? x.base_date : "-") + " · 저장 " + fmtDateTime(x.created_at) + " "));
        li.appendChild(deleteControl(id, function () {
          li.remove();
          if (!listEl.children.length) setEmpty(true);
        }));
        listEl.appendChild(li);
      });
    }

    function metricTable(rep) {
      var mt = isObj(rep.metrics) ? rep.metrics : {};
      var avg = isObj(rep.industry_avg) ? rep.industry_avg : {};
      var showAvg = METRICS.some(function (m) { return avgCell(avg, m) !== "-"; });
      var head = ["지표", "값"].concat(showAvg ? ["산업 평균"] : []).map(function (t) {
        return el("th", { text: t, attrs: { scope: "col" } });
      });
      var tbody = el("tbody");
      METRICS.forEach(function (m) {
        var cells = [el("th", { text: METRIC_LABEL[m], attrs: { scope: "row" } }), el("td", { class: "num", text: metricCell(mt, m) })];
        if (showAvg) cells.push(el("td", { class: "num", text: avgCell(avg, m) }));
        tbody.appendChild(el("tr", null, cells));
      });
      var t = el("table", { class: "metrics" }, [
        el("caption", { class: "sr-only", text: "재무 지표" }), el("thead", null, [el("tr", null, head)]), tbody]);
      return el("div", { class: "tbl-wrap" }, [t]);
    }

    function disclosureBlock(d) {
      var v = disclosureView(d), box = el("div");
      if (v.kind === "none") box.appendChild(el("p", { text: "공시는 조회하지 않았습니다." }));
      else if (v.kind === "empty") box.appendChild(el("p", { text: "관련 공시가 없습니다." }));
      else if (v.kind === "invalid") box.appendChild(el("p", { text: "공시 정보를 표시할 수 없습니다." }));
      else {
        var ul = el("ul");
        v.items.forEach(function (it) {
          var li = el("li");
          if (it.date) li.appendChild(document.createTextNode(it.date + " "));
          li.appendChild(it.href
            ? el("a", { text: it.title, attrs: { href: it.href, target: "_blank", rel: "noopener noreferrer" } })
            : document.createTextNode(it.title));
          ul.appendChild(li);
        });
        box.appendChild(ul);
      }
      return box;
    }

    var EDIT_NOTE = "지표·기준일은 분석 당시 조회한 근거라 수정할 수 없습니다. 바꾸려면 채팅에서 다시 분석해 새로 저장하세요.";

    function editBlock(rep) {
      var box = el("div");
      var btn = el("button", { text: "이름·메모 수정", attrs: { type: "button" } });
      btn.addEventListener("click", function () {
        btn.hidden = true;
        var ni = el("input", { attrs: { type: "text", id: "edit-name", maxlength: "100" } });
        ni.value = str(rep.name);
        var mi = el("textarea", { attrs: { id: "edit-memo", maxlength: "1000", rows: "4" } });
        mi.value = str(rep.memo);
        var save = el("button", { text: "저장", attrs: { type: "submit" } });
        var cancel = el("button", { text: "취소", attrs: { type: "button" } });
        var form = el("form", { class: "edit-form" }, [
          el("label", { text: "이름", attrs: { for: "edit-name" } }), ni,
          el("label", { text: "메모", attrs: { for: "edit-memo" } }), mi,
          el("p", { class: "hint", text: EDIT_NOTE }), save, cancel]);
        cancel.addEventListener("click", function () { form.remove(); btn.hidden = false; btn.focus(); });
        form.addEventListener("submit", async function (e) {
          e.preventDefault();
          var b = buildPatchBody(rep, ni.value, mi.value);
          if (b.error) { showMsg(b.error); return; }
          if (b.noop) { showMsg("변경 사항이 없습니다."); return; }
          save.disabled = cancel.disabled = true;
          var r = await C.api("PATCH", "/api/reports/" + rep.id, b.body);
          if (r.ok && isObj(r.data)) { renderDetail(r.data); showMsg("수정했습니다."); }
          else { showMsg(errText(r)); save.disabled = cancel.disabled = false; }
        });
        box.appendChild(form);
        ni.focus();
      });
      box.appendChild(btn);
      return box;
    }

    function renderDetail(rep) {
      detailEl.textContent = "";
      var mt = isObj(rep.metrics) ? rep.metrics : {};
      var d = function (n) { detailEl.appendChild(n); };
      var p = function (t, cls) { return el("p", { text: t, class: cls }); };
      d(el("h2", { text: str(rep.name) }));
      d(p(str(rep.stock_name) + " (" + str(rep.stock_code) + ")"));
      d(p("기준일 " + (typeof rep.base_date === "string" && rep.base_date ? rep.base_date : "-")));
      var times = "저장 " + fmtDateTime(rep.created_at);
      if (rep.updated_at && rep.updated_at !== rep.created_at) times += " · 수정 " + fmtDateTime(rep.updated_at);
      d(p(times));
      d(metricTable(rep));
      d(p("재무 기준: " + (typeof mt.fiscal_period === "string" && mt.fiscal_period ? mt.fiscal_period : "-") + (mt.estimated === true ? " *" : "")));
      if (mt.estimated === true) d(p("* 요청 기준 연도 재무가 없어 직전 확정 연도 값으로 대체(추정)"));
      if (typeof mt.actual_basis === "string" && mt.actual_basis && mt.actual_basis !== mt.fiscal_period)
        d(p("시세·재무 기준: " + mt.actual_basis));
      d(el("h3", { text: "공시" }));
      d(disclosureBlock(rep.disclosures));
      d(el("h3", { text: "메모" }));
      d(typeof rep.memo === "string" && rep.memo ? p(rep.memo, "memo") : p("메모 없음"));
      if (typeof rep.disclaimer === "string" && rep.disclaimer) d(p(rep.disclaimer, "disclaimer"));
      d(editBlock(rep));
      d(deleteControl(rep.id, function () { location.hash = ""; }));
    }

    async function route() {
      var id = parseHashId(location.hash);
      $("list-view").hidden = id !== null;
      $("detail-view").hidden = id === null;
      if (id === null) { await loadList(); return; }
      detailEl.textContent = "";
      var r = await C.api("GET", "/api/reports/" + id);
      if (r.ok && isObj(r.data)) { renderDetail(r.data); return; }
      showMsg(errText(r));
      if (r.status === 404) location.hash = "";
    }

    $("back").addEventListener("click", function () { showMsg(""); location.hash = ""; });
    window.addEventListener("hashchange", route);
    route();
  }

  var exported = { fmtMetric: fmtMetric, metricCell: metricCell, avgCell: avgCell, disclosureView: disclosureView,
                   parseHashId: parseHashId, buildPatchBody: buildPatchBody, errText: errText, fmtDateTime: fmtDateTime };
  if (typeof module !== "undefined") module.exports = exported;
  if (typeof document !== "undefined") init();
})();
