"use strict";

/* 状态徽标与说明 */
const STATUS_META = {
  shifted:       { label: "平移",       desc: "完整落在匹配块内，直接平移" },
  matched:       { label: "唯一命中",   desc: "新版中唯一命中批注原文" },
  disambiguated: { label: "上下文胜出", desc: "多处命中，上下文距离最小" },
  manual:        { label: "人工选择",   desc: "人工选定候选位置" },
  abandoned:     { label: "已废弃",     desc: "人工标记废弃" },
  pending:       { label: "待确认",     desc: "需要人工处理" },
};
const STATUS_ORDER = ["shifted", "matched", "disambiguated", "manual", "abandoned", "pending"];

/*
state = {
  oldText, newText,
  annotations,   // 原始批注输入（导出时原样回传，保留 id 的类型）
  results,       // /api/migrate 返回（id 保留原类型：整数 1 与字符串 "1" 是两条）
  decisions,     // [{id:<原id>, action:"choose",start,end} | {id,action:"abandon"}]，
                 // 按 id 的**类型与值**定位，插入顺序即提交顺序
  selectedIdx,   // 当前选中批注在 results 中的下标
  previewCand,   // 当前选中项正在预览的候选下标
}
*/
let state = null;

const $ = (id) => document.getElementById(id);
const cps = (s) => Array.from(s);          // 按码点切分，避免 UTF-16 代理对错位
const escapeHtml = (s) =>
  String(s).replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;");

/* 以 id 的类型与值共同定位：整数 1 与字符串 "1" 不得混同 */
const idKey = (id) => (typeof id === "number" ? `n:${String(id)}` : `s:${id}`);
/* 页面展示时给出类型线索：数字原样显示，字符串加引号 */
const formatId = (id) => (typeof id === "number" ? String(id) : `"${id}"`);

document.addEventListener("DOMContentLoaded", () => {
  $("demoBtn").addEventListener("click", loadDemo);
  $("migrateBtn").addEventListener("click", runMigration);
  $("exportBtn").addEventListener("click", runExport);
  $("annList").addEventListener("click", onListClick);
  $("oldView").addEventListener("click", onPaneClick);
  $("newView").addEventListener("click", onPaneClick);
  syncScroll($("oldView"), $("newView"));
  syncScroll($("newView"), $("oldView"));
});

/* ---------- 与后端通信 ---------- */

async function api(path, body) {
  const res = await fetch(path, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) {
    const err = new Error(typeof data.detail === "string" ? data.detail
      : (data.detail && data.detail.message) || `请求失败（${res.status}）`);
    err.status = res.status;
    err.data = data;
    throw err;
  }
  return data;
}

/* ---------- 决策（按 id 的类型与值精确定位） ---------- */

function decisionIndex(r) {
  const key = idKey(r.id);
  return state.decisions.findIndex((d) => idKey(d.id) === key);
}

function findDecision(r) {
  const i = decisionIndex(r);
  return i === -1 ? null : state.decisions[i];
}

function putDecision(r, d) {
  const i = decisionIndex(r);
  if (i === -1) state.decisions.push(d);
  else state.decisions[i] = d;
}

function dropDecision(r) {
  const i = decisionIndex(r);
  if (i !== -1) state.decisions.splice(i, 1);
}

/* ---------- 迁移 ---------- */

async function runMigration() {
  $("inputError").textContent = "";
  let annotations;
  try {
    annotations = JSON.parse($("annJson").value.trim() || "[]");
    if (!Array.isArray(annotations)) throw new Error("批注 JSON 必须是数组");
  } catch (e) {
    $("inputError").textContent = "批注 JSON 解析失败：" + e.message;
    return;
  }
  const payload = { old_text: $("oldText").value, new_text: $("newText").value, annotations };
  try {
    const data = await api("/api/migrate", payload);
    state = {
      oldText: payload.old_text,
      newText: payload.new_text,
      annotations,
      results: data.results,
      decisions: [],
      selectedIdx: data.results.length ? 0 : null,
      previewCand: 0,
    };
    $("resultPanel").hidden = false;
    renderAll();
    $("resultPanel").scrollIntoView({ behavior: "smooth" });
  } catch (e) {
    $("inputError").textContent = "迁移失败：" + e.message;
  }
}

/* ---------- 导出 ---------- */

function exportDecisions() {
  // 按 results 的输入顺序提交决策（与原批注同序），id 保留原类型
  return state.results
    .map((r) => findDecision(r))
    .filter((d) => d !== null);
}

async function runExport() {
  if (!state) return;
  try {
    const data = await api("/api/export", {
      old_text: state.oldText,
      new_text: state.newText,
      annotations: state.annotations,
      // 新格式决策列表：id 原样保留类型，整数 1 与字符串 "1" 分别定位
      decisions: exportDecisions(),
    });
    download("annotations_migrated.json", JSON.stringify(data.annotations, null, 2));
    toast(`已导出 ${data.annotations.length} 条批注`);
  } catch (e) {
    if (e.status === 409 && e.data && e.data.detail) {
      const ids = e.data.detail.pending_ids || [];
      toast(`存在 ${ids.length} 条未处理的待确认批注，禁止导出：${ids.map(formatId).join("、")}`);
      // pending_ids 保留原 id 与类型，按 (类型, 值) 共同定位
      const firstKey = ids.length ? idKey(ids[0]) : null;
      const idx = state.results.findIndex((r) => idKey(r.id) === firstKey);
      state.selectedIdx = idx === -1 ? state.selectedIdx : idx;
      state.previewCand = 0;
      renderAll();
    } else {
      toast("导出失败：" + e.message);
    }
  }
}

function download(name, text) {
  const blob = new Blob([text], { type: "application/json" });
  const a = document.createElement("a");
  a.href = URL.createObjectURL(blob);
  a.download = name;
  a.click();
  URL.revokeObjectURL(a.href);
}

let toastTimer = null;
function toast(msg) {
  const el = $("toast");
  el.textContent = msg;
  el.hidden = false;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => { el.hidden = true; }, 4000);
}

/* ---------- 状态推导 ---------- */

function effStatus(r) {
  const d = findDecision(r);
  if (d) return d.action === "abandon" ? "abandoned" : "manual";
  return r.status;
}

function effRange(r) {
  const d = findDecision(r);
  if (d && d.action === "choose") return { start: d.start, end: d.end };
  if (d && d.action === "abandon") return null;
  if (r.status !== "pending") return { start: r.new_start, end: r.new_end };
  return null;
}

function pendingLeft() {
  return state.results.filter((r) => r.status === "pending" && !findDecision(r)).length;
}

/* ---------- 渲染 ---------- */

function renderAll() {
  renderSummary();
  renderList();
  renderPanes();
  renderExportBar();
}

function renderSummary() {
  const counts = {};
  for (const r of state.results) {
    const st = effStatus(r);
    counts[st] = (counts[st] || 0) + 1;
  }
  const left = pendingLeft();
  let html = STATUS_ORDER.filter((s) => counts[s])
    .map((s) => `<span class="chip st-${s}" style="background:var(--c-${s})">${STATUS_META[s].label} ${counts[s]}</span>`)
    .join("");
  html += `<span class="chip" style="background:#334152">共 ${state.results.length} 条</span>`;
  if (left > 0) html += `<span class="chip" style="background:#dc2626">未处理待确认 ${left} 条</span>`;
  $("summary").innerHTML = html;
}

function renderList() {
  const html = state.results.map((r, idx) => {
    // 所有控件以结果下标 data-idx 定位，绝不以 String(id) 串联整数与字符串 id
    const st = effStatus(r);
    const meta = STATUS_META[st];
    const sel = idx === state.selectedIdx ? " selected" : "";
    const range = effRange(r);
    const decided = findDecision(r);

    let body = `<div class="ann-meta">旧版 [${r.start}, ${r.end})`;
    body += range ? ` → 新版 [${range.start}, ${range.end})` : (st === "abandoned" ? " → 已废弃" : " → 待确认");
    body += `</div><div class="ann-detail">${escapeHtml(r.detail || meta.desc)}</div>`;

    if (r.status === "pending" && !decided) {
      if (r.candidates.length) {
        body += `<div class="cand-box">` + r.candidates.map((c, i) =>
          `<div class="cand-row${i === state.previewCand && idx === state.selectedIdx ? " preview" : ""}" data-action="preview" data-idx="${idx}" data-cand="${i}">
             <span class="cand-range">候选${i + 1}：[${c.start}, ${c.end})</span>
             <span class="cand-dist">上下文距离 ${c.distance}</span>
             <span class="cand-actions">
               <button class="btn small choose" data-action="choose" data-idx="${idx}" data-start="${c.start}" data-end="${c.end}">选此候选</button>
             </span>
           </div>`).join("") + `</div>`;
      } else {
        body += `<div class="ann-detail">无候选位置，可标记废弃。</div>`;
      }
      body += `<div class="cand-box"><button class="btn small abandon" data-action="abandon" data-idx="${idx}">标记废弃</button></div>`;
    } else if (decided) {
      const txt = decided.action === "abandon" ? "已标记废弃" : `已选择候选 [${decided.start}, ${decided.end})`;
      body += `<div class="decided${decided.action === "abandon" ? " abandoned" : ""}">${txt}
        <button class="btn small undo" data-action="undo" data-idx="${idx}">撤销</button></div>`;
    }

    return `<div class="ann-item${sel}" data-idx="${idx}">
      <div class="ann-head">
        <span class="ann-id">${escapeHtml(formatId(r.id))}</span>
        <span class="badge st-${st}" style="background:var(--c-${st})">${meta.label}</span>
      </div>
      <div class="ann-excerpt">${escapeHtml(r.text)}</div>
      ${body}
    </div>`;
  }).join("");
  $("annList").innerHTML = html || `<div class="ann-item">无批注</div>`;
}

function renderPanes() {
  const oldRanges = [];
  const newRanges = [];
  state.results.forEach((r, idx) => {
    const st = effStatus(r);
    const sel = idx === state.selectedIdx ? " sel" : "";
    oldRanges.push({ start: r.start, end: r.end, cls: `st-${st}${sel}`, idx });
    const range = effRange(r);
    if (range) newRanges.push({ start: range.start, end: range.end, cls: `st-${st}${sel}`, idx });
  });
  // 选中的待确认项：在新版视图中叠加全部候选，预览候选高亮
  const selAnn = state.results[state.selectedIdx];
  if (selAnn && selAnn.status === "pending" && !findDecision(selAnn)) {
    selAnn.candidates.forEach((c, i) => {
      newRanges.push({
        start: c.start, end: c.end,
        cls: i === state.previewCand ? "cand-sel" : "cand",
        idx: state.selectedIdx,
      });
    });
  }
  $("oldView").innerHTML = renderText(state.oldText, oldRanges);
  $("newView").innerHTML = renderText(state.newText, newRanges);
  scrollToSelected();
}

function renderText(text, ranges) {
  const chars = cps(text);
  const clean = [];
  let last = 0;
  for (const r of ranges.slice().sort((x, y) => x.start - y.start || x.end - y.end)) {
    if (r.start < last || r.start >= r.end || r.end > chars.length) continue; // 跳过重叠/非法区间
    clean.push(r);
    last = r.end;
  }
  let html = "", pos = 0;
  for (const r of clean) {
    html += escapeHtml(chars.slice(pos, r.start).join(""));
    html += `<mark class="${r.cls}" data-idx="${r.idx}">` +
            escapeHtml(chars.slice(r.start, r.end).join("")) + `</mark>`;
    pos = r.end;
  }
  return html + escapeHtml(chars.slice(pos).join(""));
}

function renderExportBar() {
  const left = pendingLeft();
  $("exportBtn").disabled = left > 0;
  $("exportHint").textContent = left > 0
    ? `还有 ${left} 条待确认未处理，处理后方可导出`
    : `将导出 ${state.results.length} 条批注（含 id、新偏移与状态）`;
}

function scrollToSelected() {
  if (state.selectedIdx == null) return;
  // 仅选中批注自身的 mark 带 sel；候选高亮 mark.cand-sel 由 CSS 区分
  const sel = `mark[data-idx="${state.selectedIdx}"].sel, mark.cand-sel`;
  for (const view of [$("oldView"), $("newView")]) {
    const m = view.querySelector(sel);
    if (m) m.scrollIntoView({ block: "center" });
  }
}

/* ---------- 交互 ---------- */

function onListClick(e) {
  const btn = e.target.closest("[data-action]");
  if (btn) {
    const idx = Number(btn.dataset.idx);
    const r = state.results[idx];
    switch (btn.dataset.action) {
      case "choose":
        // 决策携带原 id（保留类型），只作用于下标 idx 这一条
        putDecision(r, { id: r.id, action: "choose",
                         start: Number(btn.dataset.start), end: Number(btn.dataset.end) });
        state.selectedIdx = idx;
        break;
      case "abandon":
        putDecision(r, { id: r.id, action: "abandon" });
        state.selectedIdx = idx;
        break;
      case "undo":
        dropDecision(r);
        state.selectedIdx = idx;
        state.previewCand = 0;
        break;
      case "preview":
        state.selectedIdx = idx;
        state.previewCand = Number(btn.dataset.cand);
        break;
    }
    renderAll();
    return;
  }
  const item = e.target.closest(".ann-item");
  if (item) {
    state.selectedIdx = Number(item.dataset.idx);
    state.previewCand = 0;
    renderAll();
  }
}

function onPaneClick(e) {
  const m = e.target.closest("mark");
  if (m && m.dataset.idx != null) {
    state.selectedIdx = Number(m.dataset.idx);
    state.previewCand = 0;
    renderAll();
  }
}

function syncScroll(src, dst) {
  src.addEventListener("scroll", () => {
    if (syncScroll.lock) return;
    syncScroll.lock = true;
    dst.scrollTop = src.scrollTop;
    dst.scrollLeft = src.scrollLeft;
    syncScroll.lock = false;
  });
}

/* ---------- 内置示例（覆盖全部迁移分支） ---------- */

function cpIndex(haystack, needle, n = 1) {
  // needle 第 n 次（从 1 计）出现的码点偏移；未找到返回 -1
  let from = 0, idx = -1;
  for (let i = 0; i < n; i++) {
    idx = haystack.indexOf(needle, from);
    if (idx === -1) return -1;
    from = idx + 1;
  }
  return cps(haystack.slice(0, idx)).length;
}

function loadDemo() {
  const oldText = [
    "第一章 总则",
    "本规范适用于所有实验。重要提示：操作前请阅读安全须知。",
    "第二章 设备",
    "设备清单：天平、烧杯、量筒。",
    "第三章 口号",
    "口号：安全第一。车间标语：安全第一。",
    "第四章 记录",
    "备注：暂无。",
    "第五章 甲组",
    "备注：暂无。",
    "第六章 乙组",
    "备注：暂无。",
    "第七章 模板",
    "模板开始·附录模板·请勿修改·模板开始·",
    "提示：详见附录。",
    "模板结束·附录模板·请勿修改·模板结束·下一行",
    "模板开始·附录模板·请勿修改·模板开始·",
    "提示：详见附录。",
    "模板结束·附录模板·请勿修改·模板结束·下一行",
    "第八章 提示",
    "原位置提示：详见附录。",
    "第九章 其他",
    "旧版专属段落，新版已删除。",
  ].join("\n");

  const newText = [
    "第一章 总则",
    "本规范适用于全部类型的实验。重要提示：操作前请阅读安全须知。",
    "第二章 设备",
    "设备清单：天平、烧杯、量筒、滴管。",
    "第三章 口号",
    "口号：安全至上。车间标语：安全第一。",
    "第四章 记录",
    "备注：待补充。",
    "第五章 甲组",
    "备注：暂无。",
    "第六章 乙组",
    "备注：暂无。",
    "第七章 模板",
    "模板开始·附录模板·请勿修改·模板开始·",
    "提示：详见附录。",
    "模板结束·附录模板·请勿修改·模板结束·下一行",
    "模板开始·附录模板·请勿修改·模板开始·",
    "提示：详见附录。",
    "模板结束·附录模板·请勿修改·模板结束·下一行",
    "第八章 提示",
    "原位置提示：已移除。",
    "第九章 其他",
    "（本章内容已重写）",
  ].join("\n");

  const specs = [
    ["a-shift",  "重要提示：操作前请阅读安全须知。", 1],  // 匹配块内 → 平移
    ["a-match",  "安全第一", 1],                          // 原句被改，唯一命中 → 迁移
    ["a-disamb", "备注：暂无。", 1],                      // 两处命中，上下文距离定胜负
    ["a-tie",    "提示：详见附录。", 3],                  // 两处命中且上下文并列 → 待确认
    ["a-nohit",  "旧版专属段落，新版已删除。", 1],        // 新版无此原文 → 待确认
  ];
  const annotations = specs.map(([id, text, n]) => {
    const start = cpIndex(oldText, text, n);
    return { id, start, end: start + cps(text).length };
  });

  $("oldText").value = oldText;
  $("newText").value = newText;
  $("annJson").value = JSON.stringify(annotations, null, 2);
  $("inputError").textContent = "";
  toast("示例已载入，点击「开始迁移」");
}
