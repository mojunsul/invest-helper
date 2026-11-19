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

  function buildSaveBody(sid, stockCode, nameVal, memoVal) {
    var body = { session_id: sid, stock_code: stockCode };
    var n = String(nameVal == null ? "" : nameVal).trim();
    if (n) body.name = n;
    var m = String(memoVal == null ? "" : memoVal);
    if (m) body.memo = m;
    return body;
  }

  var exported = { SESSION_KEY: SESSION_KEY, UUID_RE: UUID_RE, uuidV4: uuidV4, getSessionId: getSessionId,
                   timelineRows: timelineRows, buildSaveBody: buildSaveBody };

  // ---------- DOM 영역 ----------
  function init() {
    var $ = function (id) { return document.getElementById(id); };
    var log = $("log"), status = $("status"), banner = $("banner"), form = $("form"),
        msg = $("msg"), send = $("send"), newChat = $("new-chat"), disc = $("disclaimer");
    var store = { get: C.storageGet, set: C.storageSet };
    var sid = getSessionId(store), busy = false;

    function setBusy(b) {
      busy = b;
      msg.disabled = send.disabled = newChat.disabled = b;
      form.setAttribute("aria-busy", b ? "true" : "false");
      status.textContent = b ? "분석 중입니다… (최대 수십 초 걸릴 수 있습니다)" : "";
    }
    function userBubble(text) {
      var li = C.el("li", { class: "user" }, [C.el("div", { class: "answer", text: text })]);
      log.appendChild(li);
      return li;
    }
    function errorBubble(text) {
      var li = C.el("li", { class: "error", attrs: { role: "alert" } }, [C.el("div", { text: text })]);
      log.appendChild(li);
      li.scrollIntoView();
    }

    function renderTimeline(li, res) {
      var rows = timelineRows(res.steps, res.tool_calls, res.replans);
      if (!rows.length) return;
      var sum = "처리 과정 (단계 " + arr(res.steps).length + " · 도구 호출 " +
        arr(res.tool_calls).filter(function (t) { return t && t.type === "call"; }).length +
        " · 재계획 " + arr(res.replans).length + ")";
      var ol = C.el("ol", { class: "timeline" });
      rows.forEach(function (r) { ol.appendChild(C.el("li", { class: "tl-" + r.kind, text: r.text })); });
      li.appendChild(C.el("details", {}, [C.el("summary", { text: sum }), ol]));
    }

    function renderSave(li, item) {
      var code = str(item && item.stock_code), nm = str(item && item.stock_name) || code;
      var btn = C.el("button", { text: "저장", attrs: { type: "button" } });
      var row = C.el("div", { class: "save-row" }, [btn, C.el("span", { text: " " + nm + " (" + code + ")" })]);
      var msgEl = C.el("div", { class: "save-msg", attrs: { "aria-live": "polite" } });
      var formBox = C.el("div", { class: "save-form" });
      formBox.hidden = true;
      var uid = "s" + Math.random().toString(36).slice(2, 8);
      var inName = C.el("input", { attrs: { type: "text", id: uid + "n", maxlength: "100", placeholder: nm + " 리포트" } });
      var inMemo = C.el("textarea", { attrs: { id: uid + "m", maxlength: "1000", rows: "2" } });
      var ok = C.el("button", { text: "확정", attrs: { type: "button" } });
      var cancel = C.el("button", { text: "취소", attrs: { type: "button" } });
      [C.el("label", { text: "이름 (선택)", attrs: { for: uid + "n" } }), inName,
       C.el("label", { text: "메모 (선택)", attrs: { for: uid + "m" } }), inMemo, ok, cancel]
        .forEach(function (n) { formBox.appendChild(n); });
      btn.addEventListener("click", function () { formBox.hidden = false; inName.focus(); });
      cancel.addEventListener("click", function () { formBox.hidden = true; btn.focus(); });
      ok.addEventListener("click", async function () {
        ok.disabled = cancel.disabled = true;
        var r = await C.api("POST", "/api/reports", buildSaveBody(sid, code, inName.value, inMemo.value));
        ok.disabled = cancel.disabled = false;
        msgEl.textContent = "";
        if (r.ok && r.data) {
          var dup = arr(r.data.duplicate_of);
          msgEl.removeAttribute("role");
          msgEl.appendChild(document.createTextNode("저장했습니다. (리포트 #" + r.data.id + ") "));
          msgEl.appendChild(C.el("a", { text: "저장된 리포트 보기", attrs: { href: "reports.html" } }));
          if (dup.length) msgEl.appendChild(C.el("div", { text: "같은 종목·기준일로 이미 저장된 리포트가 " + dup.length +
            "건 있습니다(#" + dup.join(", #") + "). 중복은 reports.html에서 삭제할 수 있습니다." }));
          formBox.hidden = true;
          btn.textContent = "저장됨";
          btn.disabled = true;
        } else {
          msgEl.textContent = r.data.code === "DRAFT_NOT_FOUND"
            ? "저장할 결과가 없습니다. 먼저 해당 종목을 조회하세요." : r.data.message;
          msgEl.setAttribute("role", "alert");
        }
      });
      li.appendChild(C.el("div", { class: "save-area" }, [row, formBox, msgEl]));
    }

    function renderBot(res) {
      res = res && typeof res === "object" ? res : {};
      var st = str(res.status);
      var li = C.el("li", { class: "bot" + (st && st !== "answered" ? " status-" + st : "") });
      if (st === "error") li.classList.add("error");
      if (st && st !== "answered") li.appendChild(C.el("span", { class: "badge", text: C.STATUS_TEXT[st] || st }));
      var ans = C.el("div", { class: "answer" });
      C.linkify(str(res.answer)).forEach(function (n) { ans.appendChild(n); });
      li.appendChild(ans);
      banner.hidden = res.missing_api_key !== true;
      banner.textContent = res.missing_api_key === true ? "API 키가 설정되지 않았습니다. 서버의 .env를 확인하세요." : "";
      if (str(res.disclaimer)) disc.textContent = res.disclaimer;
      Array.prototype.forEach.call(log.querySelectorAll(".save-area"), function (n) { n.remove(); });
      arr(res.saveable).forEach(function (it) { renderSave(li, it); });
      renderTimeline(li, res);
      log.appendChild(li);
      li.scrollIntoView();
    }

    async function submit() {
      var text = msg.value.trim();
      if (!text || busy) return;
      setBusy(true);
      var userLi = userBubble(text);
      var r = await C.api("POST", "/api/chat", { session_id: sid, message: text });
      setBusy(false);
      if (r.ok) { renderBot(r.data); msg.value = ""; msg.focus(); return; }
      if (r.status === 409 || r.status === 422) userLi.remove();
      errorBubble(r.status === 409 ? BUSY_TEXT : r.data.message);
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
      log.textContent = "";
      status.textContent = "";
      banner.hidden = true;
      banner.textContent = "";
    });
  }

  if (typeof document !== "undefined") document.addEventListener("DOMContentLoaded", init);
  if (typeof module !== "undefined") module.exports = exported;
})(typeof window !== "undefined" ? window : globalThis);
