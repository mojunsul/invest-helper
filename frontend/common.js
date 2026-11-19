/* D14/D15 공용 유틸. 외부 문자열은 항상 textContent. */
(function (root) {
  "use strict";

  var REASON_TEXT = {};
  var STATUS_TEXT = { asked: "추가 정보 필요", limit: "조건 완화 제안", error: "처리 실패" };

  function el(tag, opts, children) {
    var e = document.createElement(tag);
    opts = opts || {};
    if (opts.class) e.className = opts.class;
    if (opts.text != null) e.textContent = String(opts.text);
    if (opts.attrs) Object.keys(opts.attrs).forEach(function (k) { e.setAttribute(k, opts.attrs[k]); });
    (children || []).forEach(function (c) { if (c) e.appendChild(c); });
    return e;
  }

  function safeUrl(s) {
    if (typeof s !== "string") return null;
    try {
      var u = new URL(s);
      return u.protocol === "http:" || u.protocol === "https:" ? u.href : null;
    } catch (e) { return null; }
  }

  // shortcut: URL 끝 구두점/닫는 괄호만 제거, 한글 조사 분리는 실사용 확인 후
  function splitUrls(text) {
    var out = [], re = /https?:\/\/[^\s]+/g, last = 0, m;
    text = String(text == null ? "" : text);
    while ((m = re.exec(text)) !== null) {
      var url = m[0].replace(/[.,;:!?)\]}'"」』]+$/, "").replace(/[가-힣].*$/, "");
      if (!url) continue;
      var safe = safeUrl(url);
      if (!safe) continue;
      if (m.index > last) out.push({ type: "text", value: text.slice(last, m.index) });
      out.push({ type: "url", value: url, href: safe });
      last = m.index + url.length;
      re.lastIndex = last;
    }
    if (last < text.length) out.push({ type: "text", value: text.slice(last) });
    return out;
  }

  function linkify(text) {
    return splitUrls(text).map(function (p) {
      if (p.type === "text") return document.createTextNode(p.value);
      return el("a", { text: p.value, attrs: { href: p.href, target: "_blank", rel: "noopener noreferrer" } });
    });
  }

  async function api(method, path, body, fetchImpl) {
    var f = fetchImpl || (typeof fetch !== "undefined" ? fetch : null);
    var NET = { ok: false, status: 0, data: { code: "NETWORK_ERROR", message: "서버에 연결하지 못했습니다." } };
    try {
      var init = { method: method, headers: { "Content-Type": "application/json" } };
      if (body !== undefined) init.body = JSON.stringify(body);
      var r = await f(path, init);
      if (r.ok) {
        if (r.status === 204) return { ok: true, status: 204, data: null };
        return { ok: true, status: r.status, data: await r.json() };
      }
      var d = null;
      try { d = await r.json(); } catch (e) { d = null; }
      if (!d || typeof d.code !== "string" || typeof d.message !== "string")
        d = { code: "HTTP_ERROR", message: "요청에 실패했습니다." };
      else d = { code: d.code, message: d.message };
      return { ok: false, status: r.status, data: d };
    } catch (e) { return NET; }
  }

  function storageGet(k) {
    try { return root.localStorage.getItem(k); } catch (e) { return null; }
  }
  function storageSet(k, v) {
    try { root.localStorage.setItem(k, v); } catch (e) { /* 무시 */ }
  }

  function fmtTime(iso) {
    var d = new Date(iso);
    if (typeof iso !== "string" || isNaN(d.getTime())) return iso == null ? "" : String(iso);
    var p = function (n) { return String(n).padStart(2, "0"); };
    return p(d.getHours()) + ":" + p(d.getMinutes()) + ":" + p(d.getSeconds());
  }

  var Common = { el: el, safeUrl: safeUrl, splitUrls: splitUrls, linkify: linkify, api: api,
                 storageGet: storageGet, storageSet: storageSet, fmtTime: fmtTime,
                 REASON_TEXT: REASON_TEXT, STATUS_TEXT: STATUS_TEXT };
  root.Common = Common;
  if (typeof module !== "undefined") module.exports = Common;
})(typeof window !== "undefined" ? window : globalThis);
