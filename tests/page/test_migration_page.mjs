/**
 * 迁移页面（app/static/app.js）的真实 DOM 回归：用 jsdom 加载真实 index.html
 * 与 app.js，拦截 fetch，模拟点击，验证「整数 1 与字符串 "1" 同批待确认」时：
 *
 * - 页面以 id 的类型和值共同定位（data-key 不同），列表 / 高亮不串用；
 * - 候选预览、「选此候选」、「标记废弃」、「撤销」只作用于被点击的那一条；
 * - 导出提交 decision_items，项内 id 保留 number/string 原类型；
 * - 只处理一条时导出 409、另一条仍待确认，分别处理后才 200。
 *
 * 运行：node tests/page/test_migration_page.mjs
 * （依赖 jsdom，由同目录 package.json 固定；不影响容器与其他模块）
 */
import { readFile } from "node:fs/promises";
import { fileURLToPath } from "node:url";
import path from "node:path";
import { JSDOM } from "jsdom";

const here = path.dirname(fileURLToPath(import.meta.url));
const STATIC = path.join(here, "..", "..", "app", "static");

const OLD = "甲：完全删除。乙：也删除。";
const NEW = "全新的内容。";
// 两条均无命中 → pending；id 分别为整数 1、字符串 "1"
const ANNS_MIXED = [
  { id: 1, start: 0, end: 8 },
  { id: "1", start: 8, end: 13 },
];

const intKey = 'number:1';
const strKey = 'string:"1"';

function assert(cond, msg) {
  if (!cond) throw new Error(`断言失败：${msg}`);
}

async function makePage() {
  const indexHtml = await readFile(path.join(STATIC, "index.html"), "utf8");
  const appJs = await readFile(path.join(STATIC, "app.js"), "utf8");

  const requests = [];
  // 每个用例可替换的响应桩：route -> (body) => ({status, json})
  const routes = {};

  const dom = new JSDOM(indexHtml.replace(/<script[\s\S]*?<\/script>/, ""), {
    url: "http://localhost/",
    runScripts: "outside-only",
    pretendToBeVisual: true,
  });
  const { window } = dom;
  const { document } = window;

  // ---- app.js 用到、jsdom 未提供的 API ----
  window.CSS = window.CSS || {};
  window.CSS.escape = (s) => String(s).replace(/["\\]/g, "\\$&");
  window.URL.createObjectURL = () => "blob:mock";
  window.URL.revokeObjectURL = () => {};
  window.HTMLAnchorElement.prototype.click = function click() {
    downloads.push(this.download);
  };
  window.scrollTo = () => {};
  window.Element.prototype.scrollIntoView = () => {};

  window.fetch = async (url, init) => {
    const body = JSON.parse(init.body);
    requests.push({ url, body });
    const handler = routes[url];
    const payload = handler ? handler(body) : { status: 200, json: {} };
    return {
      status: payload.status,
      ok: payload.status >= 200 && payload.status < 300,
      async json() { return payload.json; },
    };
  };

  const downloads = [];

  // 在页面上下文里执行真实 app.js
  window.eval(appJs);
  document.dispatchEvent(new window.Event("DOMContentLoaded"));

  // app.js 用 const/let 声明的状态拿不到；通过暴露在 window 上的函数驱动。
  const w = window;

  function fillInputs() {
    document.getElementById("oldText").value = OLD;
    document.getElementById("newText").value = NEW;
    document.getElementById("annJson").value = JSON.stringify(ANNS_MIXED);
  }

  function click(el) {
    el.dispatchEvent(new window.MouseEvent("click", { bubbles: true }));
  }

  function itemOf(key) {
    return document.querySelector(`.ann-item[data-key="${key.replace(/"/g, '\\"')}"]`);
  }

  async function flush() {
    await new Promise((r) => setTimeout(r, 0));
  }

  return {
    window, document, requests, routes, downloads, w,
    fillInputs, click, itemOf, flush,
  };
}

async function runMigration(page) {
  page.fillInputs();
  page.routes["/api/migrate"] = () => ({
    status: 200,
    json: {
      results: [
        { id: 1, start: 0, end: 8, text: OLD.slice(0, 8), status: "pending",
          new_start: null, new_end: null, candidates: [],
          detail: "新版中未找到批注原文，待确认" },
        { id: "1", start: 8, end: 13, text: OLD.slice(8, 13), status: "pending",
          new_start: null, new_end: null, candidates: [],
          detail: "新版中未找到批注原文，待确认" },
      ],
    },
  });
  page.document.getElementById("migrateBtn").click();
  await page.flush();
}

// ---------- 用例 ----------

async function test_migrate_renders_two_distinct_keys() {
  const page = await makePage();
  await runMigration(page);
  const intItem = page.itemOf(intKey);
  const strItem = page.itemOf(strKey);
  assert(intItem && strItem, "整数 1 与字符串 \"1\" 各渲染一条，且 data-key 不同");
  assert(intItem.querySelector(".ann-id").textContent.includes("1（数字）"),
    "整数 id 带（数字）类型标记");
  assert(strItem.querySelector(".ann-id").textContent === '"1"',
    "字符串 id 显示为 \"1\"");
  // 旧版视图两个高亮 data-key 也不串用
  assert(page.document.querySelector(`#oldView mark[data-key="${intKey}"]`), "旧版高亮整数 1");
  assert(page.document.querySelector(`#oldView mark[data-key='${strKey}']`), "旧版高亮字符串 \"1\"");
  // 迁移请求原样回传两种类型的 id
  const sent = page.requests[0].body.annotations;
  assert(sent[0].id === 1 && typeof sent[0].id === "number", "迁移请求保留整数 id");
  assert(sent[1].id === "1" && typeof sent[1].id === "string", "迁移请求保留字符串 id");
}

async function test_abandon_one_leaves_other_pending() {
  const page = await makePage();
  await runMigration(page);
  // 只点击整数 1 的「标记废弃」
  const btn = page.itemOf(intKey).querySelector('button[data-action="abandon"]');
  page.click(btn);

  // 整数 1 显示已废弃；字符串 "1" 仍显示「标记废弃」按钮（待确认）
  assert(page.itemOf(intKey).querySelector(".decided.abandoned"), "整数 1 已废弃");
  assert(!page.itemOf(strKey).querySelector(".decided"), "字符串 \"1\" 未被一起废弃");
  assert(page.itemOf(strKey).querySelector('button[data-action="abandon"]'),
    "字符串 \"1\" 仍可标记废弃（仍待确认）");
  // 徽标：仅一个 abandoned、一个 pending
  assert(page.document.querySelectorAll('.ann-item .badge.st-abandoned').length === 1,
    "已废弃徽标恰好 1 个");
  assert(page.document.querySelectorAll('.ann-item .badge.st-pending').length === 1,
    "待确认徽标恰好 1 个");
}

async function test_undo_does_not_cross() {
  const page = await makePage();
  await runMigration(page);
  page.click(page.itemOf(strKey).querySelector('button[data-action="abandon"]'));
  assert(page.itemOf(strKey).querySelector(".decided.abandoned"), "字符串 \"1\" 已废弃");
  assert(!page.itemOf(intKey).querySelector(".decided"), "整数 1 不受影响");
  // 撤销字符串 "1"
  page.click(page.itemOf(strKey).querySelector('button[data-action="undo"]'));
  assert(!page.itemOf(strKey).querySelector(".decided"), "字符串 \"1\" 撤销回待确认");
  assert(page.itemOf(intKey).querySelector('button[data-action="abandon"]'),
    "整数 1 仍待确认（撤销未串用）");
}

async function test_preview_and_choose_isolated_per_type() {
  const page = await makePage();
  // 两条均有两个并列候选
  const cands = [{ start: 0, end: 4, distance: 3 }, { start: 10, end: 14, distance: 3 }];
  page.fillInputs();
  page.routes["/api/migrate"] = () => ({
    status: 200,
    json: {
      results: [
        { id: 1, start: 0, end: 4, text: "目标文本", status: "pending",
          new_start: null, new_end: null, candidates: cands, detail: "并列待确认" },
        { id: "1", start: 5, end: 9, text: "目标文本", status: "pending",
          new_start: null, new_end: null, candidates: cands, detail: "并列待确认" },
      ],
    },
  });
  page.document.getElementById("migrateBtn").click();
  await page.flush();

  // 选中字符串 "1" 并预览其候选 2
  page.click(page.itemOf(strKey));
  const previewBtns = page.itemOf(strKey)
    .querySelectorAll('button[data-action="choose"]');
  assert(previewBtns.length === 2, "字符串 \"1\" 有两个候选按钮");
  page.click(previewBtns[1]); // 选候选 2

  assert(page.itemOf(strKey).querySelector(".decided:not(.abandoned)"),
    "字符串 \"1\" 已人工选择候选");
  assert(page.itemOf(intKey).querySelector('button[data-action="abandon"]'),
    "整数 1 仍待确认，选择未串用");
  // 选择记录的候选下标属于字符串那条
  const decidedText = page.itemOf(strKey).querySelector(".decided").textContent;
  assert(decidedText.includes("[10, 14)"), "记录的是候选 2 [10,14)");
}

async function test_export_uses_decision_items_with_typed_ids() {
  const page = await makePage();
  await runMigration(page);
  // 处理整数 1
  page.click(page.itemOf(intKey).querySelector('button[data-action="abandon"]'));

  // 第一次导出：后端 409，剩字符串 "1"。尚有一条待确认时按钮是禁用的，
  // 这里直接调用页面导出函数验证 409 分支的定位逻辑。
  page.routes["/api/export"] = (body) => {
    assert(Array.isArray(body.decision_items), "页面以 decision_items 列表传决策");
    assert(!("decisions" in body), "不再使用旧式 decisions 对象");
    const item = body.decision_items[0];
    assert(item.id === 1 && typeof item.id === "number",
      "导出项 id 保留整数类型");
    return { status: 409, json: { detail: {
      message: "存在未处理的待确认批注，禁止导出", pending_ids: ["1"] } } };
  };
  // 尚有一条待确认时导出按钮是禁用的；临时去掉禁用属性后点击真实按钮，
  // 走页面真实事件处理验证 409 分支的定位逻辑。
  const exportBtn = page.document.getElementById("exportBtn");
  exportBtn.disabled = false;
  exportBtn.click();
  await page.flush();
  // 409 后页面应把选中项切到字符串 "1"
  assert(page.itemOf(strKey).classList.contains("selected"),
    "409 后自动选中剩余的字符串 \"1\"");

  // 再处理字符串 "1"，第二次导出 200
  page.click(page.itemOf(strKey).querySelector('button[data-action="abandon"]'));
  page.routes["/api/export"] = (body) => {
    assert(body.decision_items.length === 2, "两条决策都提交");
    const [a, b] = body.decision_items;
    assert(a.id === 1 && typeof a.id === "number", "第一项是整数 1");
    assert(b.id === "1" && typeof b.id === "string", "第二项是字符串 \"1\"");
    return { status: 200, json: { annotations: [
      { id: 1, start: null, end: null, status: "abandoned" },
      { id: "1", start: null, end: null, status: "abandoned" },
    ] } };
  };
  page.document.getElementById("exportBtn").click();
  await page.flush();
  assert(page.downloads.includes("annotations_migrated.json"), "200 后触发下载");
}

async function test_export_button_disabled_until_both_handled() {
  const page = await makePage();
  await runMigration(page);
  const btn = () => page.document.getElementById("exportBtn");
  assert(btn().disabled, "两条待确认时导出按钮禁用");
  page.click(page.itemOf(intKey).querySelector('button[data-action="abandon"]'));
  assert(btn().disabled, "只处理一条时仍禁用");
  page.click(page.itemOf(strKey).querySelector('button[data-action="abandon"]'));
  assert(!btn().disabled, "两条都处理后才允许导出");
}

const tests = [
  ["迁移渲染：1 与 \"1\" 键不串用", test_migrate_renders_two_distinct_keys],
  ["废弃一条不影响另一条", test_abandon_one_leaves_other_pending],
  ["撤销不串用", test_undo_does_not_cross],
  ["候选预览与选择按类型隔离", test_preview_and_choose_isolated_per_type],
  ["导出 decision_items 保留 id 类型 / 409→200", test_export_uses_decision_items_with_typed_ids],
  ["两条都处理后导出按钮才可用", test_export_button_disabled_until_both_handled],
];

let failed = 0;
for (const [name, fn] of tests) {
  try {
    await fn();
    console.log(`  PASS  ${name}`);
  } catch (e) {
    failed += 1;
    console.error(`  FAIL  ${name}\n        ${e.stack || e.message}`);
  }
}
if (failed) {
  console.error(`\n${failed}/${tests.length} 个页面回归失败`);
  process.exit(1);
}
console.log(`\n全部 ${tests.length} 个页面回归通过`);
