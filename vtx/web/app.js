"use strict";
// Main page: extract, workflow choice, results, AI results, history. Helpers in common.js.
const STAGES = [["resolve", "解析链接"], ["fetch", "获取音频"], ["asr", "云端识别"], ["qc", "自动质检"], ["output", "整理文本"]];
const ERRORS = {
  dead: "链接失效或视频不可用",
  login_required: "需要登录，请在设置中更新 Cookie",
  risk_control: "平台验证未通过，请在设置中更新 Cookie",
  region: "视频有地区限制，当前网络无法访问。",
  parse: "无法识别链接",
  api_failed: "获取音频失败，请重试",
  asr: "识别失败，请检查服务配置与额度",
  internal: "处理时出现内部错误。",
};
const ISSUE_LABELS = {
  gap: "这里有人声但没有文字", ends_early: "结尾可能漏了内容", repeat: "句子重复", loop: "文字循环重复",
  text_in_silence: "几乎没有人声的地方出了文字", speech_rate: "语速异常", time_order: "时间顺序异常",
  out_of_range: "时间超出视频长度", source_mismatch: "识别和平台字幕不一致，可以听一下",
  number_mismatch: "识别和平台字幕的数字不一致，可以听一下", second_source_unreliable: "平台字幕整体差异大，未逐段比对",
};
let current = null;   // {job, lines, edits, issues, srt, restored:Set, meta}
let viewRevision = 0;

const text = async path => (await api(path)).text();
function showStages(stage, failed = false, complete = false) {
  const idx = STAGES.findIndex(s => s[0] === stage);
  $("status").setAttribute("aria-busy", String(!failed && !complete));
  $("progress-fill").style.width = `${Math.max(8, (idx + 1) / STAGES.length * 100)}%`;
  $("stages").innerHTML = STAGES.map(([k, label], i) => {
    const cls = complete ? "done" : failed ? (i < idx ? "done" : "") : (i < idx ? "done" : i === idx ? "current" : "");
    const state = cls === "done" ? "（完成）" : cls === "current" ? "（进行中）" : "";
    return `<li class="${cls}"${cls === "current" ? ' aria-current="step"' : ""}>${label}<span class="sr-only">${state}</span></li>`;
  }).join("");
}
function showInfo(info) {
  if (!info) { $("info").innerHTML = '<span class="muted">正在解析链接…</span>'; return; }
  const part = info.part ? ` · P${info.part}` : "";
  const plat = {bilibili: "B站", douyin: "抖音", youtube: "YouTube"}[info.platform] || info.platform;
  $("info").innerHTML = `<strong id="v-title"></strong><div class="muted" id="v-meta"></div>`;
  $("v-title").textContent = info.title;
  $("v-meta").textContent = `${plat}${part} · 时长 ${fmt(info.duration)}`;
}
function showError(err) {
  $("status").setAttribute("aria-busy", "false");
  const active = $("stages").querySelector(".current");
  if (active) { active.classList.remove("current"); active.removeAttribute("aria-current"); }
  const box = $("error");
  box.hidden = false;
  box.innerHTML = '<strong id="err-title"></strong><div class="muted" id="err-detail" style="color:inherit"></div>';
  $("err-title").textContent = ERRORS[err.category] || ERRORS.internal;
  $("err-detail").textContent = err.message || "";
  box.dataset.category = err.category;
  $("back-home").hidden = false;
}
function reset() {
  viewRevision++;
  current = null;
  aiRuns = [];
  setPage("proc");
  closeDrawer();
  $("back-home").hidden = true;
  shownRun = null;
  ["error", "result"].forEach(id => $(id).hidden = true);
  $("error").className = "card error";
  $("status").hidden = false;
  showInfo(null); showStages("resolve");
}

async function start(url) {
  reset();
  const revision = viewRevision;
  $("go").disabled = true;
  try {
    const ready = engineData.services.filter(s => s.configured);
    const engine = ready.find(s => s.id === engineData.default)?.id || ready[0]?.id;
    if (!engine) throw new Error("请在设置中添加识别服务");
    const nodes = chosenNodes();
    const llm_service = $("wf-service").value || null;
    if (nodes.length && !llm_service) throw new Error("请在设置中添加大模型");
    const job = await send("/api/jobs", "POST", {url, engine, workflow: nodes, llm_service});
    await poll(job.id, revision);
  } catch (e) {
    if (revision === viewRevision) showError({category: "internal", message: e.message});
  } finally {
    $("go").disabled = false;
  }
}
async function poll(id, revision) {
  while (revision === viewRevision) {
    const job = await json(`/api/jobs/${id}`);
    if (revision !== viewRevision) return;
    if (job.info) showInfo(job.info);
    if (job.status === "failed") { showStages(job.stage || "resolve", true); showError(job.error); return; }
    if (job.status === "done") {
      showStages("output", false, true);
      showInfo({...job.result.meta});
      loadHistory().catch(() => {});
      return showResult(job, revision);
    }
    showStages(job.stage || "resolve");
    await new Promise(r => setTimeout(r, 1000));
  }
}

async function showResult(job, revision = viewRevision) {
  const base = `/api/jobs/${job.id}/files/`;
  const [reading, original, edits, issues, srt] = await Promise.all([
    text(base + "reading"), text(base + "original"), json(base + "edits"), json(base + "issues"), text(base + "srt")]);
  if (revision !== viewRevision) return;
  const lines = original.split(/\r?\n/).filter(Boolean).map(l => {
    const m = l.match(/^\[(\d\d:\d\d:\d\d)\] (.*)$/);
    return m ? {clock: m[1], text: m[2]} : {clock: "", text: l};
  });
  current = {job, lines, edits, issues, srt, restored: new Set(), meta: job.result.meta};
  const m = current.meta;
  $("doc-title").textContent = m.title;
  const sourceUrl = new URL(job.url, location.origin);
  if (["http:", "https:"].includes(sourceUrl.protocol)) $("doc-title").href = sourceUrl.href;
  else $("doc-title").removeAttribute("href");
  $("doc-meta").textContent = [PLATFORM_LABELS[m.platform] || m.platform, m.author,
    m.duration != null ? fmt(m.duration) : "", `${reading.replace(/\s/g, "").length.toLocaleString("zh-CN")} 字`].filter(Boolean).join(" · ");
  const billed = job.result.cached_asr ? "命中缓存，未重复计费" : `计费 ${m.billed_seconds} 秒（${(m.billed_seconds / 60).toFixed(1)} 分钟）`;
  $("summary").innerHTML = '<div id="s1"></div><div class="muted" id="s2"></div>';
  const billedText = job.result.cached_asr ? billed
    : (m.billed_seconds == null ? "计费时长未知（以服务商账单为准）" : billed);
  const service = m.service_label ? `${m.service_label}（${m.engine}）` : m.engine;
  const notes = [m.timestamps_mode === "approx" ? "时间戳为分段近似" : "", m.verbatim === false ? "该服务可能顺句或吞语气词" : ""]
    .filter(Boolean).join("；");
  $("s1").textContent = `云端识别：${service}，${billedText}` + (notes ? `。注意：${notes}` : "");
  $("s2").textContent = `删除口语 ${edits.length} 处 · 疑点 ${issues.length} 处`;
  renderReading(); renderOriginal(); renderIssues();
  $("ai-output").hidden = true; setAIStatus("");   // AI output belongs to the previous video
  aiRuns = job.ai || []; shownRun = null; renderAINodes();
  if (aiRuns.some(r => r.status === "queued" || r.status === "running")) watchAI(job.id);
  setPage("done");
  if (!lines.length) {
    const box = $("error");
    box.hidden = false; box.className = "card notice"; box.dataset.category = "no_speech";
    box.innerHTML = '<strong id="err-title"></strong>';
    $("err-title").textContent = "未识别到人声";
  }
  if (aiRuns.length) await chooseResultStyle(aiRuns[0].output || aiRuns[0].kind);
  else await chooseResultStyle(outputList[0]?.id || "raw");
}

function cleanedLine(i) {
  // apply every non-restored edit of line i, right to left
  let t = current.lines[i].text;
  current.edits.map((e, k) => ({...e, k})).filter(e => e.line === i && !current.restored.has(e.k))
    .sort((a, b) => b.start - a.start)
    .forEach(e => { t = t.slice(0, e.start) + e.new + t.slice(e.end); });
  return t;
}
function readingText() {
  const out = current.lines.map((_, i) => cleanedLine(i)).filter(t => t.trim());
  const paras = [];
  for (let i = 0; i < out.length; i += 6) paras.push(out.slice(i, i + 6).join(""));
  return paras;
}
function renderReading() {
  const view = $("view-reading");
  view.replaceChildren(...readingText().map(p => { const el = document.createElement("p"); el.textContent = p; return el; }));
}
function renderOriginal() {
  const view = $("view-original");
  view.replaceChildren(...current.lines.map((line, i) => {
    const p = document.createElement("p");
    const t = document.createElement("span"); t.className = "t"; t.textContent = line.clock; p.append(t);
    let pos = 0;
    current.edits.map((e, k) => ({...e, k})).filter(e => e.line === i).sort((a, b) => a.start - b.start).forEach(e => {
      p.append(line.text.slice(pos, e.start));
      const d = document.createElement("del");
      d.textContent = e.old; d.title = "删除原因：" + e.reason; d.dataset.k = e.k;
      const restored = current.restored.has(e.k);
      if (restored) d.className = "restored";
      const b = document.createElement("button");
      b.type = "button"; b.className = "secondary restore"; b.dataset.k = e.k;
      b.textContent = restored ? "撤销恢复" : "恢复";
      b.setAttribute("aria-label", (restored ? "撤销恢复：" : "恢复被删除的：") + e.old);
      p.append(d, b);
      pos = e.end;
    });
    p.append(line.text.slice(pos));
    return p;
  }));
}
function renderIssues() {
  const ul = $("issues");
  $("drawer-issues-count").textContent = current.issues.length;
  if (!current.issues.length) { ul.innerHTML = '<li class="muted">没有发现疑点。</li>'; return; }
  ul.replaceChildren(...current.issues.map(it => {
    const li = document.createElement("li");
    const a = document.createElement("a");
    a.href = it.jump_url; a.target = "_blank"; a.rel = "noopener";
    a.textContent = `${fmt(it.start)} – ${fmt(it.end)}`;
    a.setAttribute("aria-label", `在原视频 ${fmt(it.start)} 处打开`);
    li.append(a, " " + (ISSUE_LABELS[it.kind] || it.kind) + "：" + it.detail);
    return li;
  }));
}
const TABS = ["reading", "original", "issues"];
function selectTab(which) {
  if (which === "ai") return;
  for (const name of TABS) {
    const tab = $(name === "issues" ? "drawer-issues-tab" : "tab-" + name);
    tab.setAttribute("aria-selected", String(name === which));
    tab.tabIndex = name === which ? 0 : -1;
  }
  $("drawer-reading").hidden = which === "issues";
  $("drawer-issues").hidden = which !== "issues";
  $("source-tools").hidden = which === "issues";
  $("view-reading").hidden = which !== "reading";
  $("view-original").hidden = which !== "original";
}
function download(name, body, type) {
  const a = document.createElement("a");
  a.href = URL.createObjectURL(new Blob([body], {type: type + ";charset=utf-8"}));
  a.download = name; document.body.append(a); a.click(); a.remove();
}
const baseName = () => `${current.meta.video_id}${current.meta.part ? "-p" + current.meta.part : ""}`;
const headerText = () => current.job.frame.header;
const footerText = () => current.job.frame.footer;

$("go-form").addEventListener("submit", async ev => {
  ev.preventDefault();
  const url = $("url").value.trim();
  if (!url) return;
  reset();   // clear the previous result/notice immediately, before any network round trip
  const revision = viewRevision;
  $("go").disabled = true;
  try {
    await initialization;
    if (revision !== viewRevision) return;
    const p = await json("/api/parts?url=" + encodeURIComponent(url));
    if (revision !== viewRevision) return;
    if (p.needs_choice) return choosePart(url, p);
    await start(url);
  } catch (e) { if (revision === viewRevision) showError({category: "parse", message: e.message}); }
  finally { $("go").disabled = false; }
});
function choosePart(url, p) {
  const sel = $("part-select");
  sel.replaceChildren(...p.parts.map(x => {
    const o = document.createElement("option"); o.value = x.page; o.textContent = `P${x.page} ${x.title}（${fmt(x.duration)}）`; return o;
  }));
  const dlg = $("part-dialog");
  $("part-ok").onclick = () => {
    dlg.close();
    const u = new URL(`https://www.bilibili.com/video/${p.video_id}`); u.searchParams.set("p", sel.value);
    $("url").value = u.toString(); start(u.toString());
  };
  $("part-cancel").onclick = () => { dlg.close(); setPage("home"); };
  dlg.oncancel = () => setPage("home");
  dlg.showModal(); sel.focus();
}
$("tab-reading").onclick = () => selectTab("reading");
$("tab-original").onclick = () => selectTab("original");
$("drawer-issues-tab").onclick = () => selectTab("issues");
document.querySelector(".drawer-tabs").addEventListener("keydown", e => {
  if (e.key === "ArrowRight" || e.key === "ArrowLeft") {
    e.preventDefault();
    const cur = TABS.findIndex(w => $(w === "issues" ? "drawer-issues-tab" : "tab-" + w).getAttribute("aria-selected") === "true");
    const next = TABS[(cur + (e.key === "ArrowRight" ? 1 : TABS.length - 1)) % TABS.length];
    selectTab(next); $(next === "issues" ? "drawer-issues-tab" : "tab-" + next).focus();
  }
});
$("view-original").addEventListener("click", e => {
  const b = e.target.closest("button.restore, del[data-k]");
  if (!b) return;
  const k = Number(b.dataset.k);
  current.restored.has(k) ? current.restored.delete(k) : current.restored.add(k);
  renderOriginal(); renderReading();
  document.querySelector(`#view-original button.restore[data-k="${k}"]`).focus();
});
const copyTimers = new Map();
async function copyText(button, body, label) {
  clearTimeout(copyTimers.get(button));
  try {
    await navigator.clipboard.writeText(body);
    button.textContent = "已复制"; button.classList.add("copied");
  } catch { button.textContent = "复制失败"; button.classList.remove("copied"); }
  copyTimers.set(button, setTimeout(() => { button.textContent = label; button.classList.remove("copied"); copyTimers.delete(button); }, 2000));
}
$("copy").onclick = () => copyText($("copy"), readingText().join("\n\n"), "复制");
$("export-txt").onclick = () => download(baseName() + ".txt", headerText() + "\n\n" + readingText().join("\n\n") + "\n", "text/plain");
$("export-md").onclick = () => download(baseName() + ".md",
  `# ${current.meta.title}\n\n` + headerText().split("\n").map(l => `> ${l}  `).join("\n") + "\n\n" + readingText().join("\n\n") + "\n", "text/markdown");
$("export-srt").onclick = () => download(baseName() + ".srt", current.srt, "application/x-subrip");


// ---- recognition services
let engineData = {services: [], presets: [], default: null};
const verbatimNote = v => v === false ? "可能顺句或吞语气词" : v == null ? "未确认能否逐字" : "";

function renderEngineSelect() {
  const sel = $("engine");
  const ready = engineData.services.filter(s => s.configured);
  if (!ready.length) {
    sel.value = "";
    return;
  }
  sel.value = ready.find(s => s.id === engineData.default)?.id || ready[0].id;
}
// Engine note removed from home page

// ---- output templates: each one is a workflow node
let llmData = {services: [], default: null};
let outputList = [];      // [{id, name, kind, kind_label, layouts, prompt}]
let layoutNames = {};     // layout id -> name
let aiRuns = [];          // runs of the current job (from job.ai / manual runs)
let shownRun = null;      // full record of the run on screen

async function loadNodes() {
  const [outs, lays] = await Promise.all([json("/api/outputs"), json("/api/layouts")]);
  outputList = outs.outputs;
  layoutNames = Object.fromEntries(lays.layouts.map(l => [l.id, l.name]));
}
function serviceOptions(sel, preferred) {
  const ready = llmData.services.filter(s => s.configured);
  sel.replaceChildren(...(ready.length ? ready.map(s => new Option(`${s.label} · ${s.model}`, s.id))
                                       : [new Option("未配置大模型服务", "")]));
  sel.disabled = !ready.length;
  const want = preferred || llmData.default;
  if (ready.some(s => s.id === want)) sel.value = want;
  return ready.length;
}

// ---- workflow choice above the result: remembered on the server
const KIND_GROUPS = [["原文类", ["structure", "source"]], ["要点类", ["points"]], ["自由类", ["free"]]];
async function loadWorkflow() {
  const [wf, llm] = await Promise.all([json("/api/workflow"), json("/api/llm/services")]);
  llmData = llm;
  await loadNodes();
  const chosen = new Set(wf.nodes);
  $("wf-nodes").replaceChildren(...KIND_GROUPS.map(([label, kinds]) => {
    const div = document.createElement("div"); div.className = "wf-group";
    const span = document.createElement("span"); span.textContent = label + "：";
    div.append(span);
    const items = outputList.filter(o => kinds.includes(o.kind));
    if (!items.length) {
      const a = document.createElement("a"); a.href = "/settings#outputs"; a.textContent = "去设置里新建";
      div.append(a);
    }
    for (const o of items) {
      const l = document.createElement("label");
      const cb = document.createElement("input");
      cb.type = "checkbox"; cb.value = o.id; cb.checked = chosen.has(o.id);
      cb.addEventListener("change", saveWorkflow);
      l.append(cb, " " + o.name);
      div.append(l);
    }
    return div;
  }));
  const has = serviceOptions($("wf-service"));
  $("wf-service").onchange = saveWorkflow;
  $("wf-note").textContent = has ? "勾选的输出会在识别完成后自动把全文发给所选模型处理（文字 + 默认版式图片），并按用量计费。"
                                 : "还没有配置大模型服务，勾选的输出无法运行。请先到设置页“大模型”里添加。";
  serviceOptions($("ai-service"));
  $("ai-run").disabled = !has;
  fillManualNodes();
  fillStyles();
}
const chosenNodes = () => {
  const style = $("style").value;
  return style === "raw" ? [] : outputList.some(o => o.id === style) ? [style] : [];
};
function saveWorkflow() {
  send("/api/workflow", "PUT", {nodes: chosenNodes(), service: $("wf-service").value || null}).catch(() => {});
}

// ---- AI results of the current job
const STATUS_LABELS = {queued: "排队中", running: "处理中", done: "", failed: "失败"};
function runProgress(run) {
  if (run.status === "queued") return "排队中…";
  const p = run.progress || {};
  if (p.phase === "merge") return "合并中…";
  if (p.phase === "proofread") return "校对中…";
  if (p.phase === "render") return "排版中…";
  return p.total > 1 ? `生成中 · ${p.index}/${p.total}` : "生成中…";
}
function setAIStatus(message, run = null) {
  const busy = !!run && ["queued", "running"].includes(run.status);
  $("ai-status").textContent = message;
  $("ai-progress").hidden = !message;
  $("ai-progress").setAttribute("aria-busy", String(busy));
  $("ai-stages").hidden = !busy;
  $("source-ready").hidden = !busy || !current;
  if (!busy) return;
  const phase = run.progress?.phase;
  const stages = ["structure", "source"].includes(run.kind) ? ["整理内容", "生成图片"] : ["整理内容", "核对原文", "生成图片"];
  const index = phase === "render" ? stages.length - 1 : phase === "proofread" ? 1 : 0;
  $("ai-stages").innerHTML = stages.map((label, i) => {
    const state = i < index ? "done" : i === index ? "current" : "";
    return `<li class="${state}"${i === index ? ' aria-current="step"' : ""}>${label}</li>`;
  }).join("");
  if (run.status === "queued") {
    $("ai-stages").querySelector(".current").classList.remove("current");
    $("ai-stages").querySelector("[aria-current]").removeAttribute("aria-current");
  }
}
function renderAINodes() {
  const ul = $("ai-nodes");
  $("ai-results-box").hidden = !aiRuns.length;
  fillResultStyles();
  ul.replaceChildren(...aiRuns.map(r => {
    const li = document.createElement("li");
    const b = document.createElement("button");
    b.type = "button"; b.className = "secondary" + (r.status === "failed" ? " failed" : "");
    b.textContent = r.name + (STATUS_LABELS[r.status] ? `（${STATUS_LABELS[r.status]}）` : "");
    b.setAttribute("aria-pressed", String(!!shownRun && shownRun.id === r.id));
    b.disabled = r.status === "queued" || r.status === "running";
    b.onclick = () => openRun(r.id);
    li.append(b);
    return li;
  }));
}
let watching = null;
async function watchAI(jobId) {
  if (watching === jobId) return;
  watching = jobId;
  try {
    while (current && current.job.id === jobId) {
      await new Promise(r => setTimeout(r, 1500));
      if (!current || current.job.id !== jobId) break;
      const job = await json(`/api/jobs/${jobId}`);
      if (!current || current.job.id !== jobId) break;
      const manual = aiRuns.filter(r => !job.ai.some(a => a.id === r.id));
      const before = new Map(aiRuns.map(r => [r.id, r.status]));
      aiRuns = [...job.ai, ...manual];
      renderAINodes();
      const pending = latestRun($("result-style").value);
      if (pending && !shownRun && ["queued", "running"].includes(pending.status) && document.body.dataset.state === "done") {
        setAIStatus(runProgress(pending), pending);
      }
      const finished = aiRuns.find(r => ["done", "failed"].includes(r.status) && before.get(r.id) !== r.status
        && $("result-style").value === (r.output || r.kind));
      if (finished && !shownRun && document.body.dataset.state === "done") openRun(finished.id);
      if (!job.ai.some(r => r.status === "queued" || r.status === "running")) { loadHistory().catch(() => {}); break; }
    }
  } catch (e) {
    if (current?.job.id === jobId && !shownRun) setAIStatus("进度获取失败，请刷新");
  } finally { watching = null; }
}
async function openRun(rid) {
  const jobId = current?.job.id;
  const style = $("result-style").value;
  const run = await json(`/api/llm/runs/${rid}`);
  if (current?.job.id !== jobId || $("result-style").value !== style) return;
  showAIResult(run);
  renderAINodes();
}

// ---- show one AI result: image pane (layouts, pages) and text pane
function selectAIPane(which) {
  for (const p of ["img", "text"]) {
    $("ai-tab-" + p).setAttribute("aria-selected", String(p === which));
    $("ai-tab-" + p).tabIndex = p === which ? 0 : -1;
    $("ai-pane-" + p).hidden = p !== which;
  }
}
$("ai-tab-img").onclick = () => selectAIPane("img");
$("ai-tab-text").onclick = () => selectAIPane("text");

document.querySelector(".view-switch").onkeydown = e => {
  if (!["ArrowLeft", "ArrowRight"].includes(e.key)) return;
  e.preventDefault();
  const next = $("ai-tab-img").getAttribute("aria-selected") === "true" ? "text" : "img";
  selectAIPane(next); $("ai-tab-" + next).focus();
};
function renderArticle(run) {
  const box = $("ai-text");
  box.style.whiteSpace = "normal";
  if (run.html != null) box.innerHTML = run.html;
  else box.textContent = run.text || run.markdown || "";
  for (const table of box.querySelectorAll("table")) {
    const wrap = document.createElement("div"); wrap.className = "table-scroll";
    table.replaceWith(wrap); wrap.append(table);
  }
}
function renderRefine(showDiff) {
  if (!showDiff) { renderArticle(shownRun); return; }
  const box = $("ai-text");
  box.style.whiteSpace = "normal";
  box.innerHTML = shownRun.content.map(p => {
    const flag = p.status === "reverted"
      ? `<div class="muted" style="font-size:.85rem">⚠ ${esc(p.reason)}</div>` : "";
    if (!showDiff) return `<p style="margin:.6rem 0">${esc(p.text)}</p>${flag}`;
    const html = p.diff.map(([tag, now, was]) =>
      tag === "equal" ? esc(now)
      : (was ? `<del style="background:#fde2e1;color:#8a1c14">${esc(was)}</del>` : "")
        + (now ? `<ins style="background:#dff3e3;text-decoration:none">${esc(now)}</ins>` : "")).join("");
    return `<p style="margin:.6rem 0">${html}</p>${flag}`;
  }).join("");
}
$("ai-show-diff").addEventListener("change", e => renderRefine(e.target.checked));

function statsNotes(run) {
  const s = run.stats || {}, notes = [];
  if (run.kind === "source") {
    if (s.reverted) notes.push(`${s.reverted} 段改动过大，已保留原文`);
    if (s.restored) notes.push(`模型漏掉的 ${s.restored} 处内容已按原文补回`);
    if ((s.removed || []).length) notes.push(`删除了与主题无关的句子：${s.removed.map(x => `“${x}”`).join("、")}`);
  } else if (run.kind === "structure") {
    if ((s.removed || []).length) notes.push(`删掉的客套话：${s.removed.map(x => `“${x}”`).join("、")}`);
    if ((s.fixed || []).length) notes.push(`改正错字 ${s.fixed.length} 处：` + s.fixed.map(f => `“${f.old}”→“${f.new}”`).join("、"));
    if (s.moved) notes.push(`${s.moved} 处调整了句子顺序`);
    if (s.restored) notes.push(`模型漏排的 ${s.restored} 句已放回原位`);
    if (s.rejected_fixes) notes.push(`${s.rejected_fixes} 处改动超出错字范围，已保留原句`);
    if (s.removed_ignored) notes.push("模型想删的句子太多，已全部保留");
  } else if (run.kind === "points" && s.quotes && (s.quotes.dropped || s.quotes.replaced)) {
    notes.push(`原话校验：删去 ${s.quotes.dropped} 句、换回原句 ${s.quotes.replaced} 句`);
  }
  if (s.chunks > 1) notes.push(`文本较长，已分段处理（${s.chunks} 段）后合并`);
  return notes.join("；");
}

function showAIResult(run) {
  shownRun = run;
  $("page-count").textContent = "";
  selectTab("ai");
  $("ai-generate").hidden = true;
  setAIStatus("");
  $("result-style").value = run.output || run.kind;
  const done = run.status === "done";
  $("ai-layout").disabled = !done || !run.layouts.length;
  $("ai-png").disabled = $("ai-html").disabled = true;
  $("ai-title").textContent = run.name;
  $("ai-failed").hidden = done;
  $("ai-error").textContent = done ? "" : LLM_ERRORS[(run.error || {}).code] || "生成失败，请重试";
  $("ai-retry").hidden = !run.service_id;
  if (run.error?.code === "LLM_BAD_SOURCE") {
    $("ai-error").textContent = LLM_ERRORS.LLM_BAD_SOURCE;
    $("ai-retry").hidden = true;
    $("error").hidden = true;
  }
  $("ai-done").hidden = !done;
  if (done) {
    $("ai-refine-bar").hidden = run.kind !== "source";
    $("ai-show-diff").checked = false;
    if (run.kind === "source") renderRefine(false);
    else renderArticle(run);
    $("ai-refine-stats").textContent = statsNotes(run);
    $("ai-change-details").hidden = !$("ai-refine-stats").textContent;
    $("ai-change-details").open = false;
    const sel = $("ai-layout");
    sel.replaceChildren(...run.layouts.map(id => new Option(layoutNames[id] || id, id)));
    const ready = Object.keys(run.renders || {});
    sel.value = ready.includes(run.layouts[0]) ? run.layouts[0] : (ready[0] || run.layouts[0]);
    selectAIPane(ready.length ? "img" : "text");
    if (run.layouts.length) showLayout();
  }
  const usage = run.cached ? "复用同一份结果，未再计费" : `用量 ${(run.usage && run.usage.total_tokens) || 0} tokens`;
  $("ai-meta").textContent = done ? `${run.service} · ${run.model} · ${usage}`
                                    + (run.render_error ? `；出图失败：${run.render_error}` : "") : "";
  $("ai-output").hidden = false;
}
async function showLayout() {
  const run = shownRun, lid = $("ai-layout").value;
  if (!run || !lid) return;
  const box = $("ai-pages");
  $("page-count").textContent = "";
  $("ai-layout-note").textContent = "";
  $("ai-png").disabled = $("ai-html").disabled = true;
  let info = (run.renders || {})[lid];
  if (!info) {
    $("ai-layout-note").textContent = "正在排版…";
    box.replaceChildren();
    try {
      info = await json(`/api/llm/runs/${run.id}/render/${lid}`, {method: "POST"});
      run.renders = {...(run.renders || {}), [lid]: info};
    } catch (e) {
      if (shownRun !== run || $("ai-layout").value !== lid) return;
      $("ai-layout-note").textContent = "出图失败：" + e.message;
      $("ai-png").disabled = $("ai-html").disabled = true;
      return;
    }
  }
  if (shownRun !== run || $("ai-layout").value !== lid) return;     // user moved on meanwhile
  $("ai-png").disabled = $("ai-html").disabled = false;
  $("ai-layout-note").textContent = "";
  $("page-count").textContent = ` · ${info.pages} 页`;
  box.replaceChildren(...Array.from({length: info.pages}, (_, k) => {
    const img = document.createElement("img");
    img.alt = `${layoutNames[lid] || lid} 第 ${k + 1} 页`;
    img.tabIndex = 0;
    img.setAttribute("role", "button");
    img.setAttribute("aria-label", `放大${img.alt}`);
    const open = () => {
      if (!img.src) return;
      $("image-large").src = img.src;
      $("image-large").alt = img.alt;
      $("image-viewer").classList.remove("zoomed");
      $("image-viewer").showModal();
    };
    img.onclick = open;
    img.onkeydown = e => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); open(); } };
    img.style.cssText = "max-width:100%;border-radius:8px;box-shadow:0 1px 6px rgba(0,0,0,.12)";
    api(`/api/llm/runs/${run.id}/files/${lid}/${k + 1}`).then(r => r.blob()).then(b => { img.src = URL.createObjectURL(b); });
    return img;
  }));
  $("ai-png").textContent = "下载图片";
}
$("ai-layout").addEventListener("change", showLayout);
$("ai-retry").onclick = async () => {
  const jobId = current?.job.id, revision = viewRevision;
  try {
    const rec = await json(`/api/llm/runs/${shownRun.id}/retry`, {method: "POST"});
    loadHistory().catch(() => {});
    if (revision !== viewRevision || current?.job.id !== jobId) return;
    const i = aiRuns.findIndex(r => r.id === rec.id);
    if (i >= 0) aiRuns[i] = rec;
    shownRun = null; $("ai-output").hidden = true; renderAINodes();
    setAIStatus(runProgress(rec), rec);
    await waitRun(rec.id, jobId, revision);
  } catch (e) { if (revision === viewRevision && current?.job.id === jobId) $("ai-error").textContent = "重试失败：" + e.message; }
};
async function waitRun(rid, jobId, revision) {
  let run;
  for (;;) {
    run = await json(`/api/llm/runs/${rid}`);
    if (revision !== viewRevision || current?.job.id !== jobId) return null;
    if (run.status !== "running" && run.status !== "queued") break;
    if (current?.job.id === jobId && $("result-style").value === (run.output || run.kind)) setAIStatus(runProgress(run), run);
    await new Promise(r => setTimeout(r, 1000));
  }
  const i = aiRuns.findIndex(r => r.id === rid);
  if (i >= 0) aiRuns[i] = run; else aiRuns.push(run);
  if ($("result-style").value === (run.output || run.kind)) showAIResult(run);
  renderAINodes(); loadHistory().catch(() => {});
  return run;
}

// ---- manual run
function fillManualNodes() {
  const sel = $("ai-node"), keep = sel.value;
  sel.replaceChildren(...outputList.map(o => new Option(`${o.name}（${o.kind_label}）`, o.id)));
  if ([...sel.options].some(o => o.value === keep)) sel.value = keep;
  onManualNodeChange();
}
function onManualNodeChange() {
  const o = outputList.find(x => x.id === $("ai-node").value);
  $("ai-custom").hidden = !o || o.kind !== "free";
  $("ai-template").value = o ? o.prompt : "";
}
$("ai-node").addEventListener("change", onManualNodeChange);
$("ai-run").onclick = async () => {
  if (!current) return;
  const jobId = current.job.id, revision = viewRevision;
  const service = $("ai-service").value;
  const svc = llmData.services.find(s => s.id === service);
  const o = outputList.find(x => x.id === $("ai-node").value);
  if (!o || !svc) return;
  closeDrawer();
  $("result-style").value = o.id;
  selectTab("ai");
  $("ai-run").disabled = true;
  shownRun = null; $("ai-output").hidden = true;
  setAIStatus("准备生成…", {status: "queued"});
  const body = {output: o.id, service};
  if ($("ai-template").value !== o.prompt) body.prompt = $("ai-template").value;
  if (o.kind === "free") body.source = $("ai-source").value;
  if (o.kind === "points" || (o.kind === "free" && body.source === "reading")) body.text = readingText().join("\n\n");
  try {
    const rec = await send(`/api/jobs/${jobId}/ai`, "POST", body);
    loadHistory().catch(() => {});
    if (revision !== viewRevision || current?.job.id !== jobId) return;
    aiRuns.push(rec); renderAINodes();
    const run = await waitRun(rec.id, jobId, revision);
    if (run) setAIStatus("");
  } catch (e) { if (revision === viewRevision && current?.job.id === jobId) setAIStatus("处理失败：" + e.message); }
  finally { $("ai-run").disabled = !$("ai-service").value; }
};
$("ai-copy").onclick = () => copyText($("ai-copy"), shownRun.text ?? $("ai-text").innerText, "复制文本");
$("ai-export").onclick = () => download(`${baseName()}-${safeName(shownRun.name)}.md`, shownRun.markdown || "", "text/markdown");
const safeName = s => (s || "AI").replace(/[\\/:*?"<>|]/g, "_");
async function downloadRunFile(path, filename) {
  const blob = await (await api(`/api/llm/runs/${shownRun.id}/files/${path}`)).blob();
  const a = document.createElement("a");
  a.href = URL.createObjectURL(blob); a.download = filename; document.body.append(a); a.click(); a.remove();
}
$("ai-png").onclick = async () => {
  const lid = $("ai-layout").value, info = shownRun.renders[lid];
  const stem = `${baseName()}-${safeName(shownRun.name)}-${safeName(layoutNames[lid] || lid)}`;
  await downloadRunFile(info.pages > 1 ? `${lid}/zip` : `${lid}/1`, `${stem}.${info.pages > 1 ? "zip" : "png"}`);
};
$("ai-html").onclick = () => {
  const lid = $("ai-layout").value;
  downloadRunFile(`${lid}/html`, `${baseName()}-${safeName(shownRun.name)}-${safeName(layoutNames[lid] || lid)}.html`);
};

// ---- history includes active work, so leaving the reader never hides a task.
const PLATFORM_LABELS = {bilibili: "B站", douyin: "抖音", youtube: "YouTube"};
const when = t => t ? new Date(t * 1000).toLocaleString("zh-CN", {hour12: false}).replace(/:\d\d$/, "") : "";
let historyItems = [];
let historyRefresh = null;
const isPending = status => status === "queued" || status === "running";
const historyBusy = item => isPending(item.status) || item.ai.some(run => isPending(run.status));
function historyState(item) {
  if (item.status === "failed") return "识别失败";
  if (item.status === "queued") return "排队中";
  if (item.status === "running") return (STAGES.find(([id]) => id === item.stage)?.[1] || "识别") + "中";
  const run = item.ai.find(run => isPending(run.status));
  return run ? "AI " + runProgress(run) : "";
}
async function loadHistory() {
  historyItems = (await json("/api/history")).items;
  $("history-count").textContent = historyItems.length ? `（${historyItems.length}）` : "";
  renderHistory();
  const active = historyItems.filter(historyBusy);
  $("active-jobs").hidden = !active.length;
  $("active-jobs").replaceChildren(...active.map(item => {
    const button = document.createElement("button"); button.type = "button"; button.className = "active-job";
    button.dataset.jobId = item.id;
    const title = document.createElement("span"); title.textContent = item.title;
    const state = document.createElement("span"); state.className = "history-state busy"; state.textContent = historyState(item);
    button.append(title, state); button.onclick = () => openHistory(item.id);
    return button;
  }));
  clearTimeout(historyRefresh);
  if (active.length && ["home", "hist"].includes(document.body.dataset.state)) {
    historyRefresh = setTimeout(() => loadHistory().catch(() => {}), 1500);
  }
}
function renderHistory() {
  const query = $("history-search").value.trim().toLocaleLowerCase();
  const items = historyItems.filter(it => it.title.toLocaleLowerCase().includes(query));
  const ul = $("history-list");
  if (!items.length) {
    ul.innerHTML = `<li class="empty">${query ? "无匹配记录" : "暂无记录"}</li>`;
    return;
  }
  ul.replaceChildren(...items.map(it => {
    const li = document.createElement("li"); li.className = "hrow"; li.dataset.jobId = it.id;
    const main = document.createElement("div");
    const open = document.createElement("button");
    open.type = "button"; open.className = "history-title"; open.textContent = it.title;
    open.setAttribute("aria-label", "打开：" + it.title);
    open.onclick = () => openHistory(it.id);
    const tags = document.createElement("div"); tags.className = "tags";
    tags.textContent = [PLATFORM_LABELS[it.platform] || it.platform, it.duration ? fmt(it.duration) : "",
      ...new Set(it.ai.map(r => r.name || r.kind)), it.status === "done" && !it.ai.length ? "只有原文" : ""].filter(Boolean).join(" · ");
    main.append(open);
    const label = historyState(it);
    if (label) {
      const state = document.createElement("span"); state.className = "history-state" + (historyBusy(it) ? " busy" : " failed"); state.textContent = label;
      main.append(state);
    }
    main.append(tags);
    if (it.ai.length) {
      const runs = document.createElement("div"); runs.className = "ai-runs";
      runs.append(...it.ai.map(r => {
        const b = document.createElement("button"); b.type = "button";
        b.textContent = (r.name || r.kind || "AI") + (isPending(r.status) ? " · " + runProgress(r) : r.status === "failed" ? " · 失败" : "");
        b.onclick = () => openHistory(it.id, r.id); return b;
      }));
      main.append(runs);
    }
    const side = document.createElement("div"); side.className = "history-side";
    const date = document.createElement("small"); date.textContent = when(it.created_at);
    const del = document.createElement("button");
    del.type = "button"; del.className = "history-delete"; del.textContent = "删除";
    del.setAttribute("aria-label", "删除记录：" + it.title); del.onclick = () => deleteHistory(it);
    del.disabled = historyBusy(it);
    side.append(date, del); li.append(main, side); return li;
  }));
}
async function deleteHistory(it) {
  if (!confirm(`删除“${it.title}”的记录？识别结果和 AI 结果文件都会从本机删除，不能恢复。`)) return;
  try {
    await json(`/api/history/${it.id}`, {method: "DELETE"});
    if (current && current.job.id === it.id) { current = null; setPage("hist"); }
    await loadHistory();
  } catch (e) { alert("删除失败：" + e.message); }
}
async function openHistory(id, rid) {
  reset();
  const revision = viewRevision;
  try {
    await initialization;
    if (revision !== viewRevision) return;
    const job = await json(`/api/jobs/${id}`);
    if (revision !== viewRevision) return;
    $("url").value = job.url;
    if (job.status !== "done") { await poll(id, revision); return; }
    showStages("output", false, true);
    showInfo({...job.result.meta});
    $("url").value = job.url;
    await showResult(job, revision);
    if (revision !== viewRevision) return;
    const runs = (await json("/api/history")).items.find(i => i.id === id)?.ai || [];
    if (revision !== viewRevision) return;
    for (const r of runs) if (!aiRuns.some(a => a.id === r.id)) aiRuns.push({...r, status: r.status || "done"});
    renderAINodes();
    if (aiRuns.some(run => isPending(run.status))) watchAI(job.id);
    if (rid) {
      const selected = aiRuns.find(run => run.id === rid);
      if (selected && isPending(selected.status)) await chooseResultStyle(selected.output || selected.kind);
      else {
        selectTab("ai"); await openRun(rid);
        $("ai-meta").textContent = "历史结果 · " + $("ai-meta").textContent;
      }
    } else if (runs.length) await chooseResultStyle(runs[0].output || runs[0].kind);
    window.scrollTo({top: 0});
  } catch (e) { if (revision === viewRevision) showError({category: "internal", message: e.message}); }
}

async function loadEngines() {
  engineData = await json("/api/engines");
  renderEngineSelect();
}
// Reader navigation uses separate screens, matching the reference layout.
function setPage(state) {
  if (state === "home" || state === "hist") viewRevision++;
  document.body.dataset.state = state;
  $("history").hidden = state !== "hist";
  $("home-back").hidden = state === "home";
  $("status").hidden = state !== "proc";
  $("result").hidden = state !== "done";
  $("error").hidden = true;
  $("back-home").hidden = true;
  $("history-btn").setAttribute("aria-current", state === "hist" ? "page" : "false");
  if (state !== "done") closeDrawer();
  if (state === "home" || state === "hist") loadHistory().catch(() => {});
  else clearTimeout(historyRefresh);
  window.scrollTo({top: 0});
}
const STYLE_DESCRIPTIONS = {
  raw: "保留原话，去除口头赘词", structure: "按原话分章节，突出重点", refine: "校正识别错字，保留原意",
  notes: "按主题梳理知识，保留例子与推理", review: "对比表现与优缺点，说明适用人群", structured: "提炼核心结论，分点展开依据",
  steps: "拆解操作顺序，保留参数与提醒", cards: "一张卡片一个概念，便于记忆", chapters: "生成视频简介与分段标题",
  debate: "梳理论点与论据，呈现分歧", news: "逐条速览事件、背景与影响", qa: "把主要内容整理成一问一答",
  actions: "把具体建议整理成待办清单", article: "将口述整理成适合发布的文章", xhs: "短段分点，整理成小红书笔记"
};
const styleDescription = id => STYLE_DESCRIPTIONS[id] || "按自定义提示词整理内容";
function updateStyleDescription() {
  $("style-description").textContent = styleDescription($("style").value);
}
$("style").addEventListener("change", updateStyleDescription);
$("style").addEventListener("input", updateStyleDescription);
function fillStyles() {
  for (const id of ["style", "result-style"]) {
    const select = $(id), keep = select.value;
    const raw = new Option(id === "style" ? "原文" : "选择风格", "raw");
    raw.disabled = id === "result-style";
    select.replaceChildren(raw, ...outputList.map(o => new Option(o.name, o.id)));
    if ([...select.options].some(o => o.value === keep)) select.value = keep;
  }
  updateStyleDescription();
  fillResultStyles();
}
function latestRun(id) {
  return aiRuns.filter(r => (r.output || r.kind) === id)
    .sort((a, b) => (b.created_at || 0) - (a.created_at || 0))[0];
}
function fillResultStyles() {
  for (const option of $("result-style").options) {
    if (option.value === "raw") continue;
    const out = outputList.find(o => o.id === option.value);
    if (!out) continue;
    const run = latestRun(out.id);
    option.textContent = out.name + (run ? ` · ${run.status === "done" ? "已生成" : STATUS_LABELS[run.status] || ""}` : "");
  }
}
async function chooseResultStyle(id) {
  if (!current) return;
  if (id === "raw") { $("drawer-open").click(); selectTab("reading"); return; }
  $("result-style").value = id;
  selectTab("ai");
  const run = latestRun(id);
  $("ai-output").hidden = true;
  $("ai-generate").hidden = true;
  shownRun = null;
  $("page-count").textContent = "";
  $("ai-node").value = id;
  onManualNodeChange();
  if (run) {
    if (["done", "failed"].includes(run.status)) await openRun(run.id);
    else setAIStatus(runProgress(run), run);
  } else {
    setAIStatus("尚未生成");
    $("ai-generate").hidden = false;
    $("ai-generate").disabled = !$("ai-service").value;
  }
}
$("result-style").onchange = () => chooseResultStyle($("result-style").value).catch(e => {
  setAIStatus("加载失败：" + e.message);
});
$("ai-generate").onclick = () => $("ai-run").click();
$("history-search").oninput = renderHistory;
$("history-btn").onclick = () => { location.hash = "history"; setPage("hist"); loadHistory().catch(e => {
  $("history-list").textContent = "历史加载失败：" + e.message;
}); };
function goHome() {
  location.hash = ""; current = null; setPage("home"); $("url").focus();
}
document.querySelector(".logo").onclick = e => { e.preventDefault(); goHome(); };
$("back-home").onclick = goHome;
$("home-back").onclick = goHome;
window.addEventListener("hashchange", () => {
  if (location.hash === "#history") setPage("hist");
  else if (!location.hash && document.body.dataset.state === "hist") setPage("home");
});
function closeDrawer() {
  setDrawerFullscreen(false);
  $("source-drawer").hidden = true;
  $("drawer-open").setAttribute("aria-expanded", "false");
}
$("drawer-open").onclick = () => {
  $("source-drawer").hidden = false;
  $("drawer-open").setAttribute("aria-expanded", "true");
  $("drawer-close").focus();
};
$("drawer-close").onclick = () => { closeDrawer(); $("drawer-open").focus(); };
$("preview-source").onclick = () => { $("drawer-open").click(); selectTab("reading"); };
function setDrawerFullscreen(expanded) {
  $("source-drawer").classList.toggle("fullscreen", expanded);
  document.body.classList.toggle("drawer-fullscreen", expanded);
  $("drawer-expand").setAttribute("aria-pressed", String(expanded));
  $("drawer-expand").setAttribute("aria-label", expanded ? "退出原文全屏" : "全屏原文");
  $("drawer-expand").title = expanded ? "退出全屏" : "全屏";
  $("drawer-expand").textContent = expanded ? "⤡" : "⤢";
}
$("drawer-expand").onclick = () => setDrawerFullscreen(!$("source-drawer").classList.contains("fullscreen"));
$("image-close").onclick = () => $("image-viewer").close();
$("image-zoom").onclick = $("image-large").onclick = () => $("image-viewer").classList.toggle("zoomed");
selectTab("reading");
document.addEventListener("click", e => {
  if (!e.target.closest(".export-menu")) document.querySelector(".export-menu").open = false;
});
for (const b of document.querySelectorAll(".export-options button")) b.addEventListener("click", () => {
  document.querySelector(".export-menu").open = false;
});
document.addEventListener("keydown", e => {
  if (e.key === "Escape" && !$("source-drawer").hidden) {
    if ($("source-drawer").classList.contains("fullscreen")) { setDrawerFullscreen(false); $("drawer-expand").focus(); }
    else $("drawer-close").click();
  }
  if (e.key === "Escape" && document.querySelector(".export-menu").open) {
    document.querySelector(".export-menu").open = false;
    document.querySelector(".export-menu summary").focus();
  }
});
const initialization = Promise.all([loadEngines(), loadWorkflow()]);
initialization.then(() => { document.body.dataset.ready = "1"; }).catch(e => {
  $("home-hint").hidden = false;
  $("home-hint").textContent = "服务加载失败：" + e.message;
});
loadHistory().catch(() => {});
if (location.hash === "#history") setPage("hist");
