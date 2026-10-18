"use strict";
// Settings page: services, prompts, poster templates, header/footer, cookies.
let caps = {reveal_keys: false, browser_login: false, diagnostics: false};

// ---- tabs (arrow keys move between them)
const TABS = ["asr", "llm", "outputs", "layouts", "system", "frame", "cookie", "project"];
function selectTab(which) {
  for (const t of TABS) {
    $("tab-" + t).setAttribute("aria-selected", String(t === which));
    $("panel-" + t).hidden = t !== which;
  }
  history.replaceState(null, "", "#" + which);
}
TABS.forEach(t => $("tab-" + t).onclick = () => selectTab(t));
document.querySelector("[role=tablist]").addEventListener("keydown", e => {
  if (e.key !== "ArrowRight" && e.key !== "ArrowLeft") return;
  const visible = TABS.filter(t => !$("tab-" + t).hidden);
  const cur = visible.findIndex(t => $("tab-" + t).getAttribute("aria-selected") === "true");
  const next = visible[(cur + (e.key === "ArrowRight" ? 1 : visible.length - 1)) % visible.length];
  selectTab(next); $("tab-" + next).focus();
});
if (TABS.includes(location.hash.slice(1))) selectTab(location.hash.slice(1));
function syncTabFromHash() {
  const requested = location.hash.slice(1);
  selectTab(TABS.includes(requested) && !$("tab-" + requested).hidden ? requested : "asr");
}
window.addEventListener("hashchange", syncTabFromHash);

// ---- secret inputs: one per credential field, hidden by default, eye button reveals the saved value
function secretInputs(box, prefix, fields, editing, revealUrl, configured = editing) {
  box.replaceChildren(...fields.map(f => {
    const wrap = document.createElement("div");
    const label = document.createElement("label");
    label.htmlFor = `${prefix}-${f.name}`; label.textContent = f.label;
    const row = document.createElement("div"); row.className = "secret";
    const input = document.createElement("input");
    input.type = "password"; input.id = `${prefix}-${f.name}`; input.autocomplete = "off"; input.dataset.secret = f.name;
    input.dataset.saved = editing && (f.configured ?? configured) ? "true" : "false";
    input.dataset.masked = input.dataset.saved;
    input.value = input.dataset.masked === "true" ? "********" : "";
    input.onfocus = () => { if (input.dataset.masked === "true") input.select(); };
    input.addEventListener("input", () => { input.dataset.saved = "false"; input.dataset.masked = "false"; });
    const eye = document.createElement("button");
    eye.type = "button"; eye.className = "secondary secret-eye"; eye.innerHTML = '<svg class="icon-off" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" aria-hidden="true"><path d="M2 12s3.5-7 10-7 10 7 10 7-3.5 7-10 7S2 12 2 12Z"/><circle cx="12" cy="12" r="3"/></svg><svg class="icon-on" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" aria-hidden="true"><path d="m3 3 18 18M10.5 5.1 12 5c6.5 0 10 7 10 7a23 23 0 0 1-3 3.9M6.2 6.2A23 23 0 0 0 2 12s3.5 7 10 7a11 11 0 0 0 5.8-1.8M10 10a3 3 0 0 0 4 4"/></svg>'; eye.setAttribute("aria-pressed", "false");
    eye.setAttribute("aria-label", `显示${f.label}`);
    eye.title = `显示${f.label}`;
    eye.disabled = input.dataset.saved === "true" && !caps.reveal_keys;
    if (eye.disabled) eye.title = "仅本机可查看";
    eye.onclick = async () => {
      const show = input.type === "password";
      if (show && input.dataset.masked === "true" && revealUrl && caps.reveal_keys) {
        try {
          input.value = (await json(revealUrl))[f.name] || "";
          input.dataset.masked = "false";
        } catch (e) { eye.title = "读取失败：" + e.message; return; }
      }
      if (!show && input.dataset.saved === "true") { input.value = "********"; input.dataset.masked = "true"; }
      input.type = show ? "text" : "password";
      eye.setAttribute("aria-pressed", String(show));
      eye.setAttribute("aria-label", `${show ? "隐藏" : "显示"}${f.label}`);
      eye.title = `${show ? "隐藏" : "显示"}${f.label}`;
    };
    row.append(input, eye);
    wrap.append(label, row);
    return wrap;
  }));
}
const secretValues = box => Object.fromEntries([...box.querySelectorAll("[data-secret]")]
  .filter(i => i.dataset.saved !== "true").map(i => [i.dataset.secret, i.value]));

function providerLinks(prefix, preset) {
  const box = $(prefix + "-provider-links");
  box.replaceChildren(...[["官网", "official_url"], ["API Key", "console_url"], ["文档", "docs_url"]].flatMap(([label, key]) => {
    if (!preset?.[key]?.startsWith("https://")) return [];
    const link = document.createElement("a");
    link.href = preset[key]; link.textContent = label + " ↗"; link.target = "_blank"; link.rel = "noopener noreferrer";
    return [link];
  }));
  box.hidden = !box.childElementCount;
}
function presetOf(service, presets) {
  const matches = presets.filter(p => p.preset !== "custom" && (!service.kind || p.kind === service.kind) &&
    (p.base_url || "").replace(/\/$/, "") === (service.base_url || "").replace(/\/$/, ""));
  return matches.find(p => p.model === service.model) || matches[0] || presets.find(p => p.preset === "custom");
}

function defaultChoice(s, selected) {
  const b = button(selected ? "●" : "○", "set-default", s.id, selected ? `默认：${s.label}` : `设为默认：${s.label}`);
  b.classList.add("default-choice");
  b.title = selected ? "首页默认" : "设为首页默认";
  b.setAttribute("aria-pressed", String(selected));
  b.disabled = !s.configured;
  return b;
}

async function testService(prefix, url, message) {
  const btn = $(prefix + "-test");
  const id = $(prefix + "-id").value;
  btn.disabled = true;
  $(message).textContent = "连接中…";
  try {
    const r = await json(url, {method: "POST"});
    if ($(prefix + "-id").value === id) $(message).textContent = r.ok ? "连接成功" : (r.message || r.error || "连接失败");
  } catch (e) { if ($(prefix + "-id").value === id) $(message).textContent = "连接失败：" + e.message; }
  finally { if ($(prefix + "-id").value === id) btn.disabled = btn.textContent === "保存后测试"; }
}

// ---- recognition services
let engineData = {services: [], presets: [], default: null};
const KIND_SECRETS = {tencent: [{name: "secret_id", label: "SecretId"}, {name: "secret_key", label: "SecretKey"}]};
const secretFieldsOf = (kind, hint) => KIND_SECRETS[kind] || [{name: "key", label: hint || "API key"}];

function renderEngines() {
  $("engines").querySelector("tbody").replaceChildren(...engineData.services.map(s => {
    const tr = document.createElement("tr");
    [s.label, s.model, s.configured ? "已配置" : "未配置"]
      .forEach(t => { const td = document.createElement("td"); td.textContent = t; tr.append(td); });
    const td = document.createElement("td");
    td.append(defaultChoice(s, s.id === engineData.default), button("编辑", "edit", s.id, `编辑：${s.label}`),
      button("删除", "delete", s.id, `删除：${s.label}`));
    tr.append(td);
    return tr;
  }));
}
async function loadEngines() {
  engineData = await json("/api/engines");
  if (!$("ef-preset").options.length) {
    $("ef-preset").replaceChildren(...engineData.presets.map(p => new Option(p.preset === "custom" ? "自定义（OpenAI 兼容）" : p.label, p.preset)));
    applyEnginePreset();
  }
  renderEngines();
}
function applyEnginePreset() {
  const p = engineData.presets.find(x => x.preset === $("ef-preset").value);
  if (!p) return;
  $("ef-label").value = p.label; $("ef-url").value = p.base_url || ""; $("ef-model").value = p.model;
  $("ef-url-row").hidden = p.kind !== "openai";
  providerLinks("ef", p);
  secretInputs($("ef-secrets"), "ef", secretFieldsOf(p.kind, p.key_hint), false);
}
$("ef-preset").addEventListener("change", applyEnginePreset);
function resetEngineForm() {
  $("ef-id").value = ""; $("ef-local").checked = false; $("ef-preset").disabled = false;
  $("engine-form-title").textContent = "添加识别服务"; $("engine-msg").textContent = ""; $("ef-cancel").hidden = true;
  $("ef-test").hidden = true;
  applyEnginePreset();
}
$("ef-cancel").onclick = () => { resetEngineForm(); closeEditor("engine-form"); };
$("ef-test").onclick = () => testService("ef", `/api/engines/${$("ef-id").value}/test`, "engine-msg");
$("engine-form").addEventListener("submit", async ev => {
  ev.preventDefault();
  const id = $("ef-id").value;
  const preset = engineData.presets.find(x => x.preset === $("ef-preset").value);
  const svc = id ? engineData.services.find(s => s.id === id) : null;
  const kind = svc ? svc.kind : preset.kind;
  const body = {label: $("ef-label").value, model: $("ef-model").value, secrets: secretValues($("ef-secrets"))};
  if (!id) body.kind = kind;
  if (kind === "openai") {
    body.base_url = $("ef-url").value; body.allow_local = $("ef-local").checked;
    if (!id && preset.timestamps) body.timestamps = preset.timestamps;
  }
  try {
    await send(id ? `/api/engines/${id}` : "/api/engines", id ? "PUT" : "POST", body);
    $("engine-msg").textContent = id ? "已保存修改" : "已添加";
    resetEngineForm(); await loadEngines(); closeEditor("engine-form");
  } catch (e) { $("engine-msg").textContent = "保存失败：" + e.message; }
});
$("engines").addEventListener("click", async e => {
  const b = e.target.closest("button[data-action]");
  if (!b) return;
  const s = engineData.services.find(x => x.id === b.dataset.id);
  if (b.dataset.action === "set-default") {
    try {
      await json(`/api/engines/${s.id}/set-default`, {method: "POST"});
      await loadEngines();
      $("engine-list-msg").textContent = "";
    } catch (err) { $("engine-list-msg").textContent = err.message; }
  } else if (b.dataset.action === "edit") {
    $("ef-id").value = s.id; $("engine-form-title").textContent = `编辑：${s.label}`; $("ef-cancel").hidden = false;
    $("ef-preset").disabled = true;
    const preset = presetOf(s, engineData.presets);
    $("ef-preset").value = preset.preset; providerLinks("ef", preset);
    $("ef-label").value = s.label; $("ef-model").value = s.model; $("ef-url").value = s.base_url || "";
    $("ef-local").checked = !!s.allow_local; $("ef-url-row").hidden = s.kind !== "openai";
    secretInputs($("ef-secrets"), "ef", s.secret_fields, true, `/api/engines/${s.id}/secrets`, s.configured);
    $("engine-msg").textContent = ""; $("ef-test").hidden = false; $("ef-test").disabled = false; $("ef-test").textContent = "测试连接"; $("ef-test").title = "测试已保存配置";
    openEditor("engine-form");
    $("ef-label").focus();
  } else if (b.dataset.action === "delete") {
    if (!confirm(`删除“${s.label}”及凭据？`)) return;
    try { await json(`/api/engines/${s.id}`, {method: "DELETE"}); await loadEngines(); }
    catch (err) { $("engine-list-msg").textContent = err.message; }
  }
});

// ---- LLM services
let llmData = {services: [], presets: [], default: null};
let llmTestRevision = 0;
function clearLLMTest() {
  llmTestRevision++;
  $("llm-msg").textContent = "";
  $("lf-test").disabled = false; $("lf-test").textContent = "测试连接";
}
const LLM_SECRET = [{name: "key", label: "API key"}];
function renderLLMs() {
  const body = $("llms").querySelector("tbody");
  if (!llmData.services.length) {
    body.innerHTML = '<tr><td colspan="4" class="muted">暂无服务</td></tr>';
    return;
  }
  body.replaceChildren(...llmData.services.map(s => {
    const tr = document.createElement("tr");
    [s.label, s.model, s.configured ? (s.key_from ? "共用百炼凭据" : "已配置") : "未配置"].forEach(t => {
      const td = document.createElement("td"); td.textContent = t; tr.append(td);
    });
    const td = document.createElement("td");
    td.append(defaultChoice(s, s.id === llmData.default), button("编辑", "edit", s.id, `编辑：${s.label}`), button("删除", "delete", s.id, `删除：${s.label}`));
    tr.append(td);
    return tr;
  }));
}
function applyLLMPreset() {
  clearLLMTest();
  const p = llmData.presets.find(x => x.preset === $("lf-preset").value);
  if (!p) return;
  $("lf-label").value = p.label; $("lf-url").value = p.base_url; $("lf-model").value = p.model;
  $("lf-model").placeholder = p.model_hint || ""; $("lf-context").value = p.context;
  $("lf-reuse").hidden = p.key_from !== "dashscope";
  providerLinks("lf", p);
  secretInputs($("lf-secrets"), "lf", LLM_SECRET, false);
}
async function loadLLMs() {
  llmData = await json("/api/llm/services");
  if (!$("lf-preset").options.length) {
    $("lf-preset").replaceChildren(...llmData.presets.map(p => new Option(p.preset === "custom" ? "自定义" : p.label, p.preset)));
    applyLLMPreset();
  }
  renderLLMs();
}
$("lf-preset").addEventListener("change", applyLLMPreset);
function resetLLMForm() {
  $("lf-id").value = ""; $("lf-local").checked = false; $("lf-preset").disabled = false;
  $("llm-form-title").textContent = "添加大模型"; $("llm-msg").textContent = ""; $("lf-cancel").hidden = true; applyLLMPreset();
}
$("lf-cancel").onclick = () => { resetLLMForm(); closeEditor("llm-form"); };
function llmFormBody() {
  const id = $("lf-id").value;
  const preset = llmData.presets.find(x => x.preset === $("lf-preset").value);
  const body = {label: $("lf-label").value, base_url: $("lf-url").value, model: $("lf-model").value,
                context: parseInt($("lf-context").value, 10) || null, allow_local: $("lf-local").checked,
                key: secretValues($("lf-secrets")).key || null};
  if (!id && preset.key_from) body.key_from = preset.key_from;
  return body;
}
$("lf-test").onclick = async () => {
  const revision = ++llmTestRevision;
  const id = $("lf-id").value;
  $("lf-test").disabled = true; $("lf-test").textContent = "测试中…";
  $("llm-msg").textContent = "";
  try {
    const result = await send("/api/llm/services/test" + (id ? "?sid=" + encodeURIComponent(id) : ""), "POST", llmFormBody());
    if (revision === llmTestRevision) $("llm-msg").textContent = result.ok ? "连接成功" : (result.message || "连接失败");
  } catch (e) { if (revision === llmTestRevision) $("llm-msg").textContent = "连接失败：" + e.message; }
  finally { if (revision === llmTestRevision) { $("lf-test").disabled = false; $("lf-test").textContent = "测试连接"; } }
};
$("llm-form").addEventListener("input", clearLLMTest);
$("llm-form").addEventListener("submit", async ev => {
  ev.preventDefault();
  clearLLMTest();
  const id = $("lf-id").value;
  const body = llmFormBody();
  try {
    await send(id ? `/api/llm/services/${id}` : "/api/llm/services", id ? "PUT" : "POST", body);
    $("llm-msg").textContent = id ? "已保存修改" : "已添加";
    resetLLMForm(); await loadLLMs(); closeEditor("llm-form");
  } catch (e) { $("llm-msg").textContent = "保存失败：" + e.message; }
});
$("llms").addEventListener("click", async e => {
  const b = e.target.closest("button[data-action]");
  if (!b) return;
  const s = llmData.services.find(x => x.id === b.dataset.id);
  if (b.dataset.action === "set-default") {
    try {
      await json(`/api/llm/services/${s.id}/set-default`, {method: "POST"});
      await loadLLMs();
      $("llm-list-msg").textContent = "";
    } catch (err) { $("llm-list-msg").textContent = err.message; }
  } else if (b.dataset.action === "edit") {
    clearLLMTest();
    $("lf-id").value = s.id; $("llm-form-title").textContent = `编辑：${s.label}`; $("lf-cancel").hidden = false;
    $("lf-preset").disabled = true; $("lf-label").value = s.label; $("lf-url").value = s.base_url;
    const preset = presetOf(s, llmData.presets);
    $("lf-preset").value = preset.preset; providerLinks("lf", preset);
    $("lf-model").value = s.model; $("lf-context").value = s.context; $("lf-local").checked = !!s.allow_local;
    $("lf-reuse").hidden = !s.key_from;
    secretInputs($("lf-secrets"), "lf", s.secret_fields || LLM_SECRET, true, `/api/llm/services/${s.id}/secrets`, s.configured);
    $("llm-msg").textContent = ""; $("lf-test").title = "测试当前配置"; openEditor("llm-form");
    $("lf-label").focus();
  } else if (confirm(`删除“${s.label}”及凭据？`)) {
    try { await json(`/api/llm/services/${s.id}`, {method: "DELETE"}); await loadLLMs(); }
    catch (err) { $("llm-list-msg").textContent = err.message; }
  }
});
// ---- output styles and layouts share one binding relation: output.layouts.
let outputData = {outputs: [], kinds: {}};
let layoutData = [];
const groupOfKind = kind => kind === "points" ? "points" : "article";
const KIND_VARS = {
  structure: "必须包含 <code>{sentences}</code>，可用 <code>{title}</code>。返回结构 JSON，正文由原句组合。",
  source: "必须包含 <code>{paragraphs}</code>，可用 <code>{title}</code>。返回段落 JSON。",
  points: "必须包含 <code>{text}</code>，可用 <code>{title}</code><code>{max_points}</code>。返回 title、conclusion、points 组成的 JSON。",
  free: "可用 <code>{text}</code><code>{title}</code>。输出 Markdown。未包含 {text} 时会自动附加原文。",
};
const LAYOUT_VARS = {
  article: '可用 title、kicker、blocks、footer_lines、page、pages、first、last。正文：<code>{% for b in blocks %}{{ b.html|safe }}{% endfor %}</code>，放入 <code>class="content"</code> 容器。',
  points: '可用 title、conclusion、kicker、points、left、right、quotes、footer_lines。points 每项包含 heading、body、key、detail、quotes。',
};
const layoutName = id => (layoutData.find(t => t.id === id) || {name: id}).name;
function itemInfo(name, detail) {
  const info = document.createElement("div"); info.className = "setting-item-info";
  const title = document.createElement("span"); title.textContent = name;
  const sub = document.createElement("span"); sub.className = "muted"; sub.textContent = detail;
  info.append(title, sub); return info;
}
function renderOutputs() {
  $("out-list").replaceChildren(...outputData.outputs.filter(o => !o.hidden || $("out-show-hidden").checked).map(o => {
    const li = document.createElement("li"); li.dataset.output = o.id; li.tabIndex = -1;
    const info = itemInfo(o.name, [o.kind_label, o.hidden ? "已隐藏" : "", o.modified ? "已修改" : "", `${o.layouts.length} 个版式`].filter(Boolean).join(" · "));
    info.title = o.layouts.map(layoutName).join(" · ");
    const actions = document.createElement("div"); actions.className = "setting-item-actions";
    actions.append(button("编辑", "edit", o.id, `编辑：${o.name}`));
    li.append(info, actions);
    if (o.builtin && o.modified) actions.append(button("恢复", "reset", o.id, `恢复：${o.name}`));
    if (o.builtin) actions.append(button(o.hidden ? "显示" : "隐藏", o.hidden ? "show" : "hide", o.id, `${o.hidden ? "显示" : "隐藏"}：${o.name}`));
    else actions.append(button("删除", "delete", o.id, `删除：${o.name}`, "secondary small danger"));
    const download = button("", "export", o.id, `导出：${o.name}`, "secondary small package-export");
    download.title = `导出：${o.name}`;
    download.innerHTML = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6" aria-hidden="true"><path d="M12 3v12m-5-5 5 5 5-5M4 16v4h16v-4"/></svg>';
    actions.append(download);
    return li;
  }));
}
async function loadOutputs() {
  outputData = await json("/api/outputs?hidden=true"); renderOutputs(); renderLayouts();
  if (!$("of-kind").options.length) $("of-kind").replaceChildren(...Object.entries(outputData.kinds).map(([id, label]) => new Option(label, id)));
}
$("out-import").onclick = () => $("out-package-file").click();
$("out-package-file").onchange = async () => {
  const input = $("out-package-file"), file = input.files[0]; if (!file) return;
  const trigger = $("out-import"); trigger.disabled = true; trigger.textContent = "导入中…";
  $("out-list-msg").textContent = "";
  try {
    if (file.size > 1024 * 1024) throw new Error("风格包不能超过 1 MB");
    let body;
    try { body = JSON.parse(await file.text()); }
    catch (_) { throw new Error("请选择有效的 JSON 风格包"); }
    const imported = await send("/api/style-packages/import", "POST", body);
    await loadTemplates(); await loadOutputs();
    const item = [...$("out-list").children].find(li => li.dataset.output === imported.output);
    item?.scrollIntoView({block:"center"}); item?.focus({preventScroll:true});
    $("out-list-msg").textContent = "已导入";
  } catch (err) { $("out-list-msg").textContent = err.message; }
  finally { input.value = ""; trigger.disabled = false; trigger.textContent = "导入"; }
};
function fillLayoutBoxes(kind, chosen = []) {
  const layouts = layoutData.filter(t => t.group === groupOfKind(kind));
  const ordered = [...chosen.map(id => layouts.find(t => t.id === id)).filter(Boolean), ...layouts.filter(t => !chosen.includes(t.id))];
  $("of-layout-boxes").replaceChildren(...ordered.map(t => {
    const label = document.createElement("label"); label.className = "inline";
    const cb = document.createElement("input"); cb.type = "checkbox"; cb.value = t.id; cb.checked = chosen.includes(t.id);
    label.append(cb, " " + t.name); return label;
  }));
}
function fillOutputForm(o = null) {
  const editing = !!o;
  $("out-msg").textContent = "";
  $("of-id").value = o?.id || ""; $("of-name").value = o?.name || ""; $("of-prompt").value = o?.prompt || "";
  $("of-kind").value = o?.kind || "free"; $("of-kind").disabled = editing;
  $("of-vars").innerHTML = KIND_VARS[$("of-kind").value];
  $("out-form-title").textContent = editing ? `编辑 · ${o.name}` : "新建输出风格";
  $("of-cancel").hidden = false;
  fillLayoutBoxes($("of-kind").value, o?.layouts || []);
}
$("of-kind").onchange = () => { fillLayoutBoxes($("of-kind").value); $("of-vars").innerHTML = KIND_VARS[$("of-kind").value]; };
$("of-cancel").onclick = () => { fillOutputForm(); closeEditor("out-form"); };
$("out-show-hidden").onchange = renderOutputs;
$("out-form").onsubmit = async ev => {
  ev.preventDefault(); const id = $("of-id").value;
  const body = {name: $("of-name").value, prompt: $("of-prompt").value,
    layouts: [...$("of-layout-boxes").querySelectorAll("input:checked")].map(cb => cb.value)};
  if (!body.layouts.length) { $("out-msg").textContent = "至少选择一个版式"; return; }
  if (!id) body.kind = $("of-kind").value;
  try {
    await send(id ? `/api/outputs/${id}` : "/api/outputs", id ? "PUT" : "POST", body);
    $("out-msg").textContent = "已保存"; await loadOutputs(); await loadTemplates(); fillOutputForm(); closeEditor("out-form");
  } catch (e) { $("out-msg").textContent = e.message; }
};
$("out-list").onclick = async e => {
  const b = e.target.closest("button[data-action]"); if (!b) return;
  const o = outputData.outputs.find(x => x.id === b.dataset.id);
  try {
    if (b.dataset.action === "export") {
      b.disabled = true;
      $("out-list-msg").textContent = "";
      const res = await api(`/api/outputs/${o.id}/package`);
      if (!res.ok) throw new Error((await res.json().catch(() => ({}))).detail || res.statusText);
      const url = URL.createObjectURL(await res.blob());
      const link = document.createElement("a"); link.href = url; link.download = "pianyu-style.json";
      document.body.append(link); link.click(); link.remove();
      setTimeout(() => URL.revokeObjectURL(url), 1000);
      return;
    }
    if (b.dataset.action === "edit") { fillOutputForm(o); openEditor("out-form"); $("of-name").focus(); return; }
    if (b.dataset.action === "reset") await json(`/api/outputs/${o.id}/reset`, {method:"POST"});
    if (["hide", "show"].includes(b.dataset.action)) await json(`/api/outputs/${o.id}/hide?hidden=${b.dataset.action === "hide"}`, {method:"POST"});
    if (b.dataset.action === "delete") { if (!confirm(`删除“${o.name}”？`)) return; await json(`/api/outputs/${o.id}`, {method:"DELETE"}); }
    await loadOutputs(); await loadTemplates(); $("out-list-msg").textContent = "";
  } catch (err) { $("out-list-msg").textContent = err.message; }
  finally { b.disabled = false; }
};
function renderLayouts() {
  for (const group of ["points", "article"]) $("lay-list-" + group).replaceChildren(...layoutData.filter(t => t.group === group).map(t => {
    const li = document.createElement("li");
    const size = t.height_mode === "auto" ? "长图" : t.height_mode.slice(6) + " px";
    const styles = outputData.outputs.filter(o => o.layouts.includes(t.id)).map(o => o.name);
    const info = itemInfo(t.name, [`${t.width} px · ${size}`, styles.length ? `${styles.length} 个风格` : "未绑定"].join(" · "));
    info.title = styles.join(" · ");
    const actions = document.createElement("div"); actions.className = "setting-item-actions";
    actions.append(button("预览", "preview", t.id, `预览：${t.name}`), button("编辑", "edit", t.id, `编辑：${t.name}`));
    if (t.builtin && t.modified) actions.append(button("恢复", "reset", t.id, `恢复：${t.name}`));
    if (!t.builtin) actions.append(button("删除", "delete", t.id, `删除：${t.name}`, "secondary small danger"));
    li.append(info, actions);
    return li;
  }));
}
async function loadTemplates() { layoutData = (await json("/api/layouts")).layouts; renderLayouts(); }
function blankLayout(group) {
  const content = group === "article" ? '<main class="content">{% for b in blocks %}{{ b.html|safe }}{% endfor %}</main>'
    : '<main>{% for p in points %}<section><h2>{{ p.heading }}</h2><p>{{ p.body }}</p></section>{% endfor %}</main>';
  return '<!doctype html>\n<html lang="zh-CN"><head><meta charset="utf-8">\n<style>\n* { box-sizing: border-box; }\nbody { margin: 0; padding: 64px; color: #25221e; background: #fffdf8; font: 28px/1.8 "Microsoft YaHei", sans-serif; }\nh1 { font-size: 48px; line-height: 1.4; }\nh2 { font-size: 34px; }\nfooter { color: #888; font-size: 20px; }\n</style></head><body>\n<h1>{{ title }}</h1>\n' + content + '\n<footer>{% for line in footer_lines %}<div>{{ line }}</div>{% endfor %}</footer>\n</body></html>';
}
function fillOutputBoxes(group, chosen = []) {
  $("tf-output-boxes").replaceChildren(...outputData.outputs.filter(o => groupOfKind(o.kind) === group).map(o => {
    const label = document.createElement("label");
    const cb = document.createElement("input"); cb.type = "checkbox"; cb.value = o.id; cb.checked = chosen.includes(o.id);
    cb.onchange = () => {
      const id = $("tf-id").value;
      if (!cb.checked && id && o.layouts.includes(id) && o.layouts.length === 1) {
        cb.checked = true; $("tpl-msg").textContent = `“${o.name}”需先选择替代版式`;
      } else $("tpl-msg").textContent = "";
    };
    label.append(cb, " " + o.name + (o.hidden ? " · 已隐藏" : "")); return label;
  }));
}
function fillTemplateForm(t = null) {
  const group = t?.group || "article";
  $("tpl-msg").textContent = "";
  $("tf-id").value = t?.id || ""; $("tf-name").value = t?.name || "";
  $("tf-group").value = group; $("tf-group").disabled = !!t;
  $("tf-width").value = t?.width || 1080; $("tf-html").value = t?.html || blankLayout(group);
  const fixed = t ? t.height_mode !== "auto" : group === "article";
  $("tf-height").value = fixed ? "fixed" : "auto";
  $("tf-height-px").hidden = !fixed; $("tf-height-px").value = fixed && t ? t.height_mode.slice(6) : 1440;
  $("tf-vars").textContent = ""; $("tf-vars").innerHTML = LAYOUT_VARS[group];
  fillOutputBoxes(group, t ? outputData.outputs.filter(o => o.layouts.includes(t.id)).map(o => o.id) : []);
  $("tpl-form-title").textContent = t ? `编辑 · ${t.name}` : "新建版式"; $("tf-cancel").hidden = false;
}
$("tf-group").onchange = () => {
  const group = $("tf-group").value; $("tf-html").value = blankLayout(group);
  $("tf-vars").innerHTML = LAYOUT_VARS[group]; fillOutputBoxes(group);
  $("tf-height").value = group === "article" ? "fixed" : "auto"; $("tf-height-px").hidden = group !== "article";
};
$("tf-height").onchange = () => { $("tf-height-px").hidden = $("tf-height").value === "auto"; };
$("tf-cancel").onclick = () => { fillTemplateForm(); closeEditor("tpl-form"); };
function templateFields() {
  return {name: $("tf-name").value, group: $("tf-group").value, html: $("tf-html").value,
    width: parseInt($("tf-width").value, 10) || 1080,
    height_mode: $("tf-height").value === "auto" ? "auto" : `fixed:${parseInt($("tf-height-px").value, 10) || 1440}`,
    outputs: [...$("tf-output-boxes").querySelectorAll("input:checked")].map(cb => cb.value)};
}
let previewUrl = "";
async function previewLayout(body, name) {
  $("preview-title").textContent = name || "版式预览"; $("preview-msg").textContent = "生成预览…"; $("tpl-preview-img").hidden = true;
  if (!$("layout-preview").open) $("layout-preview").showModal();
  try {
    const res = await api("/api/layouts/preview", {method:"POST", headers:{"Content-Type":"application/json"}, body:JSON.stringify(body)});
    if (!res.ok) throw new Error((await res.json().catch(() => ({}))).detail || res.statusText);
    if (previewUrl) URL.revokeObjectURL(previewUrl);
    previewUrl = URL.createObjectURL(await res.blob()); $("tpl-preview-img").src = previewUrl; $("tpl-preview-img").hidden = false;
    $("preview-msg").textContent = "";
  } catch (err) { $("preview-msg").textContent = err.message; }
}
$("preview-close").onclick = () => $("layout-preview").close();
$("layout-preview").addEventListener("close", () => { if (previewUrl) URL.revokeObjectURL(previewUrl); previewUrl = ""; });
$("tf-preview").onclick = () => previewLayout(templateFields(), $("tf-name").value);
$("tpl-form").onsubmit = async ev => {
  ev.preventDefault(); const id = $("tf-id").value; const body = templateFields();
  const blocked = outputData.outputs.find(o => id && o.layouts.includes(id) && !body.outputs.includes(o.id) && o.layouts.length === 1);
  if (blocked) { $("tpl-msg").textContent = `“${blocked.name}”需先选择替代版式`; return; }
  try {
    await send(id ? `/api/layouts/${id}` : "/api/layouts", id ? "PUT" : "POST", body);
    await loadTemplates(); await loadOutputs(); fillTemplateForm(); closeEditor("tpl-form"); $("tpl-msg").textContent = "已保存";
  } catch (err) { $("tpl-msg").textContent = err.message; }
};
$("panel-layouts").onclick = async e => {
  const b = e.target.closest("ul button[data-action]"); if (!b) return;
  const t = layoutData.find(x => x.id === b.dataset.id);
  try {
    if (b.dataset.action === "preview") { await previewLayout({copy_of:t.id}, t.name); return; }
    if (b.dataset.action === "edit") { fillTemplateForm(t); openEditor("tpl-form"); $("tf-name").focus(); return; }
    if (b.dataset.action === "reset") await json(`/api/layouts/${t.id}/reset`, {method:"POST"});
    if (b.dataset.action === "delete") { if (!confirm(`删除“${t.name}”？`)) return; await json(`/api/layouts/${t.id}`, {method:"DELETE"}); }
    await loadTemplates(); await loadOutputs(); $("tpl-list-msg").textContent = "";
  } catch (err) { $("tpl-list-msg").textContent = err.message; }
};

// ---- header / footer
let frameData = null;
async function loadFrame() {
  frameData = await json("/api/frame");
  const form = $("frame-form");
  form.replaceChildren(...Object.keys(frameData.defaults).map(k => {
    const wrap = document.createElement("div");
    const label = document.createElement("label");
    label.htmlFor = "fr-" + k; label.textContent = frameData.labels[k] + (frameData.modified.includes(k) ? "（已修改）" : "");
    const ta = document.createElement("textarea");
    ta.id = "fr-" + k; ta.rows = 4; ta.value = frameData.templates[k]; ta.dataset.key = k;
    const actions = document.createElement("div"); actions.className = "actions";
    const save = button("保存", "save", k, `保存：${frameData.labels[k]}`, "secondary");
    const reset = button("恢复默认", "reset", k, `恢复默认：${frameData.labels[k]}`, "secondary");
    reset.hidden = !frameData.modified.includes(k);
    actions.append(save, reset);
    wrap.append(label, ta, actions);
    return wrap;
  }));
}
$("frame-form").addEventListener("click", async e => {
  const b = e.target.closest("button[data-action]");
  if (!b) return;
  const k = b.dataset.id;
  try {
    if (b.dataset.action === "save") await send("/api/frame", "PUT", {[k]: $("fr-" + k).value});
    else await json(`/api/frame/${k}/reset`, {method: "POST"});
    $("frame-msg").textContent = b.dataset.action === "save" ? "已保存" : "已恢复默认";
    await loadFrame();
  } catch (err) { $("frame-msg").textContent = "保存失败：" + err.message; }
});

// ---- cookies, proxy and common model rules
let cookieSettings = {cookies: {}};
const cookieLogins = new Map();
const COOKIE_PLATFORMS = {bilibili: "B站", douyin: "抖音", youtube: "YouTube"};
const loginStorageKey = platform => "vtx-cookie-login-" + platform;
function renderCookiePlatforms() {
  $("cookie-platforms").replaceChildren(...Object.entries(COOKIE_PLATFORMS).map(([platform, name]) => {
    const entry = cookieLogins.get(platform);
    const busy = !!entry && ["opening", "waiting"].includes(entry.status);
    const connected = !!cookieSettings.cookies[platform];
    const row = document.createElement("div"); row.className = "cookie-platform"; row.dataset.platform = platform;
    row.setAttribute("aria-busy", String(busy));
    const title = document.createElement("strong"); title.textContent = name;
    const state = document.createElement("div"); state.className = "cookie-state"; state.setAttribute("aria-live", "polite");
    const status = document.createElement("span"); status.className = "status" + (connected ? " ok" : "");
    status.textContent = busy ? (entry.status === "opening" ? "打开中…" : "等待登录…") : connected ? "已保存" : "未登录";
    state.append(status);
    if (entry?.error) { const error = document.createElement("span"); error.className = "login-error"; error.textContent = entry.error; state.append(error); }
    const action = document.createElement("button"); action.type = "button"; action.className = "secondary";
    action.textContent = busy ? "取消" : connected ? "重新登录" : "登录";
    action.setAttribute("aria-label", `${action.textContent} ${name}`);
    action.disabled = !caps.browser_login || (busy && !entry.id);
    if (!caps.browser_login) action.title = "请在本机打开";
    action.onclick = () => busy ? cancelCookieLogin(platform, entry) : startCookieLogin(platform);
    row.append(title, state, action); return row;
  }));
}
function clearCookieSession(platform, entry) {
  clearTimeout(entry.timer);
  sessionStorage.removeItem(loginStorageKey(platform));
}
async function applyCookieLogin(platform, entry, result) {
  if (cookieLogins.get(platform) !== entry) return;
  Object.assign(entry, result);
  if (["opening", "waiting"].includes(entry.status)) {
    sessionStorage.setItem(loginStorageKey(platform), entry.id);
    entry.timer = setTimeout(() => pollCookieLogin(platform, entry), 1200);
  } else {
    clearCookieSession(platform, entry);
    if (entry.status === "connected") {
      cookieSettings = await json("/api/settings");
      if (cookieLogins.get(platform) !== entry) return;
    }
  }
  renderCookiePlatforms();
}
async function pollCookieLogin(platform, entry) {
  const revision = entry.revision || 0;
  try {
    const result = await json(`/api/settings/cookie-logins/${encodeURIComponent(entry.id)}`);
    if (revision === (entry.revision || 0)) await applyCookieLogin(platform, entry, result);
  }
  catch (error) {
    if (cookieLogins.get(platform) !== entry || revision !== (entry.revision || 0)) return;
    entry.status = "failed"; entry.error = error.message; clearCookieSession(platform, entry); renderCookiePlatforms();
  }
}
async function startCookieLogin(platform) {
  const entry = {status: "opening"}; cookieLogins.set(platform, entry); renderCookiePlatforms();
  try { await applyCookieLogin(platform, entry, await json(`/api/settings/cookies/${platform}/login`, {method: "POST"})); }
  catch (error) {
    if (cookieLogins.get(platform) !== entry) return;
    entry.status = "failed"; entry.error = error.message; renderCookiePlatforms();
  }
}
async function cancelCookieLogin(platform, entry) {
  entry.revision = (entry.revision || 0) + 1;
  clearTimeout(entry.timer);
  try { await applyCookieLogin(platform, entry, await json(`/api/settings/cookie-logins/${encodeURIComponent(entry.id)}`, {method: "DELETE"})); }
  catch (error) {
    if (cookieLogins.get(platform) !== entry) return;
    entry.error = error.message; entry.timer = setTimeout(() => pollCookieLogin(platform, entry), 1200); renderCookiePlatforms();
  }
}
async function loadSettings() {
  cookieSettings = await json("/api/settings");
  $("proxy").value = cookieSettings.proxy_youtube || "";
  for (const platform of Object.keys(COOKIE_PLATFORMS)) {
    const id = sessionStorage.getItem(loginStorageKey(platform));
    if (caps.browser_login && id && !cookieLogins.has(platform)) {
      const entry = {id, status: "waiting"}; cookieLogins.set(platform, entry); pollCookieLogin(platform, entry);
    }
  }
  renderCookiePlatforms();
}
async function loadDiagnostics() {
  if (!caps.diagnostics) return;
  const data = await json("/api/diagnostics");
  $("project-paths").replaceChildren(...Object.entries({config: "配置", work: "运行记录", logs: "日志"}).map(([key, label]) => {
    const row = document.createElement("div"); const title = document.createElement("dt"); const path = document.createElement("dd");
    title.textContent = label; path.textContent = data.paths[key]; row.append(title, path); return row;
  }));
  $("project-logs").textContent = (data.log_files || []).join(" · ");
}
$("save-proxy").onclick = async () => {
  try { await send("/api/settings", "POST", {proxy_youtube:$("proxy").value}); $("settings-msg").textContent = "已保存"; }
  catch (err) { $("settings-msg").textContent = err.message; }
};
async function loadSystemPrompt() { $("system-prompt").value = (await json("/api/llm/settings")).system_prompt; }
$("system-form").onsubmit = async ev => {
  ev.preventDefault();
  try { await send("/api/llm/settings", "PUT", {system_prompt:$("system-prompt").value}); $("system-msg").textContent = "已保存"; }
  catch (err) { $("system-msg").textContent = err.message; }
};

// New and edit have explicit entry points; closing discards the editor state.
function openEditor(id) {
  const editor = $(id).closest("dialog, details");
  if (editor.tagName === "DIALOG") editor.showModal();
  else editor.open = true;
}
function closeEditor(id) {
  const editor = $(id).closest("dialog, details");
  if (editor.tagName === "DIALOG") editor.close();
  else editor.open = false;
}
for (const [id, title, heading, message, reset] of [
  ["out-form", "新建输出风格", "out-form-title", "out-msg", fillOutputForm],
  ["tpl-form", "新建版式", "tpl-form-title", "tpl-msg", fillTemplateForm],
]) {
  const form = $(id); const section = form.closest("section");
  const create = document.createElement("button");
  create.type = "button"; create.className = "secondary editor-new"; create.textContent = "＋ 新建";
  create.dataset.newEditor = id; create.setAttribute("aria-label", title);
  (section.querySelector(".section-tools") || section.querySelector(".section-heading")).append(create);
  const dialog = document.createElement("dialog"); dialog.className = "editor-dialog";
  dialog.setAttribute("aria-labelledby", heading);
  document.body.append(dialog); dialog.append(form); form.append($(message));
  const close = document.createElement("button"); close.type = "button"; close.className = "editor-close";
  close.textContent = "×"; close.setAttribute("aria-label", "关闭"); close.onclick = () => dialog.close();
  form.prepend(close);
  dialog.addEventListener("close", () => { reset(); dialog.scrollTop = 0; });
  create.onclick = () => { reset(); openEditor(id); $(id === "out-form" ? "of-name" : "tf-name").focus(); };
}
for (const [id, title, reset] of [
  ["engine-form", "添加识别服务", resetEngineForm], ["llm-form", "添加大模型", resetLLMForm],
]) {
  const form = $(id); const details = document.createElement("details"); details.className = "setting-editor";
  const summary = document.createElement("summary"); summary.textContent = "＋ " + title;
  const section = form.closest("section");
  section.firstElementChild.after(details); details.append(summary, form);
  summary.onclick = ev => {
    if (details.open && form.querySelector('input[type="hidden"]').value) { ev.preventDefault(); reset(); details.open = true; }
    else if (!details.open) reset();
  };
}
for (const [id, prefix] of [["engine-form", "ef"]]) {
  $(id).addEventListener("input", () => {
    if (!$(prefix + "-id").value) return;
    $(prefix + "-test").disabled = true; $(prefix + "-test").textContent = "保存后测试";
  });
}
for (const help of document.querySelectorAll("[data-tip]")) help.setAttribute("aria-description", help.dataset.tip);
(async () => {
  caps = await json("/api/capabilities").catch(() => caps);
  $("tab-project").hidden = !caps.diagnostics;
  if (location.hash === "#project" && !caps.diagnostics) selectTab("asr");
  const results = await Promise.allSettled([loadEngines(), loadLLMs(),
    loadTemplates().then(loadOutputs).then(() => { fillTemplateForm(); fillOutputForm(); }), loadFrame(), loadSettings(), loadSystemPrompt(), loadDiagnostics()]);
  for (let i = 0; i < results.length; i++) if (results[i].status === "rejected") {
    const messages = ["engine-list-msg", "llm-list-msg", "out-list-msg", "frame-msg", "settings-msg", "system-msg", "project-msg"];
    $(messages[i]).textContent = results[i].reason.message;
  }
  document.body.dataset.ready = "1";
})();
