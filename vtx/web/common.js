"use strict";
// Shared by index.html and settings.html: element lookup, API calls with the optional access token.
const $ = id => document.getElementById(id);
let token = new URLSearchParams(location.search).get("token") || sessionStorage.getItem("vtx-token") || "";
if (token) sessionStorage.setItem("vtx-token", token);

async function api(path, opts = {}) {
  const headers = Object.assign({}, opts.headers || {});
  if (token) headers["Authorization"] = "Bearer " + token;
  const res = await fetch(path, Object.assign({}, opts, {headers}));
  if (res.status === 401) {
    const t = prompt("这个服务需要访问令牌：");
    if (!t) throw new Error("需要访问令牌");
    token = t; sessionStorage.setItem("vtx-token", t);
    return api(path, opts);
  }
  return res;
}
async function json(path, opts) {
  const res = await api(path, opts);
  const body = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(body.detail ? (typeof body.detail === "string" ? body.detail : body.detail[0].msg) : res.statusText);
  return body;
}
const send = (path, method, body) =>
  json(path, {method, headers: {"Content-Type": "application/json"}, body: JSON.stringify(body)});
const esc = s => String(s).replace(/[&<>"]/g, c => ({"&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;"}[c]));
const fmt = s => { s = Math.floor(s); return [s / 3600 | 0, (s / 60 | 0) % 60, s % 60].map(n => String(n).padStart(2, "0")).join(":"); };
function button(text, action, id, label, cls = "secondary small") {
  const b = document.createElement("button");
  b.type = "button"; b.className = cls; b.textContent = text;
  b.dataset.action = action; b.dataset.id = id; b.setAttribute("aria-label", label || text);
  return b;
}
const LLM_ERRORS = {
  LLM_ACCESS_DENIED: "Key 无效或权限不足", LLM_RATE_LIMITED: "请求频繁，请稍后重试",
  LLM_BILLING_UNAVAILABLE: "余额不足，请检查额度", LLM_TOO_LONG: "内容较长，请选择更大上下文的模型",
  LLM_NOT_CONFIGURED: "请先配置 Key", LLM_SERVICE_ERROR: "服务暂不可用，请稍后重试", LLM_BAD_OUTPUT: "输出格式有误，请重试",
  LLM_BAD_SOURCE: "识别内容不足，请重新识别",
};
