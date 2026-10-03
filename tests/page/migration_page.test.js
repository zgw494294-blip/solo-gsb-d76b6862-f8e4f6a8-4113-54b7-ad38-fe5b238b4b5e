// 迁移页「整数 id 与字符串 id 不得串用」页面回归。
//
// 用真实 DOM（jsdom）加载 index.html + app.js，把 fetch 打桩为内存后端
// （模拟 /api/migrate 与 /api/export 的协议形态），断言页面**选择批注 /
// 候选预览 / 确认 / 撤销**全部以 id 的类型与值共同定位，提交的决策是
// 保留 id 类型的数组（decision_list），而不是会混同 1 与 "1" 的对象键。
"use strict";

const fs = require("fs");
const path = require("path");
const { JSDOM } = require("jsdom");

const STATIC = path.resolve(__dirname, "../../app/static");

// 两条待确认：整数 1（两个候选）与字符串 "1"（无候选）
const MIGRATE_RESPONSE = {
  results: [
    {
      id: 1, start: 100, end: 104, text: "目标文本", status: "pending",
      new_start: null, new_end: null,
      candidates: [
        { start: 5, end: 9, distance: 12 },
        { start: 60, end: 64, distance: 12 },
      ],
      detail: "并列待确认",
    },
    {
      id: "1", start: 200, end: 206, text: "删除片段", status: "pending",
      new_start: null, new_end: null, candidates: [],
      detail: "无命中待确认",
    },
  ],
};

function loadPage() {
  const html = fs.readFileSync(path.join(STATIC, "index.html"), "utf8");
  const dom = new JSDOM(html, {
    url: "http://localhost:8000/",
    runScripts: "outside-only",
    pretendToBeVisual: true,
  });
  const { window } = dom;
  global.window = window;
  global.document = window.document;
  global.navigator = window.navigator;
  global.HTMLElement = window.HTMLElement;
  global.CSS = window.CSS || {
    escape: (s) => s.replace(/[^a-zA-Z0-9]/g, "\\$&"),
  };
  window.HTMLElement.prototype.scrollIntoView = function () {};
  window.eval(fs.readFileSync(path.join(STATIC, "app.js"), "utf8"));
  // app.js 在 DOMContentLoaded 时绑定事件；jsdom 此时已加载完成，手动补发
  window.document.dispatchEvent(
    new window.Event("DOMContentLoaded", { bubbles: true }));
  return dom;
}

(async () => {
  const failures = [];
  const check = (name, cond) => { if (!cond) failures.push(name); };

  const dom = loadPage();
  const { window } = dom;
  const document = window.document;
  const exports = [];

  // 最小后端桩：记录每次 /api/export 载荷；未处理待确认 → 409
  window.fetch = async (url, opts) => {
    const body = JSON.parse(opts.body);
    if (url === "/api/migrate") {
      return { ok: true, status: 200, json: async () => MIGRATE_RESPONSE };
    }
    if (url === "/api/export") {
      exports.push(body);
      const pending = MIGRATE_RESPONSE.results.filter((r) =>
        !body.decisions.some((d) =>
          typeof d.id === typeof r.id && d.id === r.id)).map((r) => r.id);
      if (pending.length) {
        return {
          ok: false, status: 409,
          json: async () => ({ detail: { message: "存在未处理", pending_ids: pending } }),
        };
      }
      return {
        ok: true, status: 200,
        json: async () => ({ annotations: body.decisions.map((d) => ({ id: d.id })) }),
      };
    }
    throw new Error("unexpected url " + url);
  };

  // 每次渲染都会重建列表 DOM，所有查询都在点击时即时取最新节点
  const item = (i) => document.querySelectorAll("#annList .ann-item")[i];
  const click = (el) =>
    el.dispatchEvent(new window.Event("click", { bubbles: true, cancelable: true }));
  const tick = () => new Promise((r) => setTimeout(r, 0));

  // 载入并迁移
  document.getElementById("oldText").value = "旧";
  document.getElementById("newText").value = "新";
  document.getElementById("annJson").value = JSON.stringify([
    { id: 1, start: 100, end: 104 },
    { id: "1", start: 200, end: 206 },
  ]);
  document.getElementById("migrateBtn").click();
  await tick();

  // 1) 两条都渲染，展示带类型线索：1 与 "1"
  check("两条批注均渲染", document.querySelectorAll("#annList .ann-item").length === 2);
  check("整数 id 展示为 1", item(0).querySelector(".ann-id").textContent === "1");
  check("字符串 id 展示为 \"1\"", item(1).querySelector(".ann-id").textContent === `"1"`);

  // 2) 默认选中第一条（整数 1），候选只属于整数 1
  check("默认选中整数 1", item(0).classList.contains("selected"));
  check("整数 1 有两个候选行", item(0).querySelectorAll(".cand-row").length === 2);
  check("字符串 \"1\" 无候选只有废弃按钮",
    item(1).querySelectorAll(".cand-row").length === 0 &&
    item(1).querySelectorAll('button[data-action="abandon"]').length === 1);

  // 3) 预览整数 1 的候选 2（即时取最新节点，避免渲染后引用失效）
  click(item(0).querySelectorAll(".cand-row")[1]);
  check("预览候选2高亮在整数 1 行",
    item(0).querySelectorAll(".cand-row")[1].classList.contains("preview"));
  check("预览不选中字符串 \"1\" 行", !item(1).classList.contains("selected"));

  // 4) 确认整数 1 选候选 2；字符串 "1" 仍待确认
  click(item(0).querySelectorAll("button.choose")[1]);
  check("整数 1 显示已选择候选2区间",
    item(0).querySelector(".decided").textContent.includes("[60, 64)"));
  check("字符串 \"1\" 仍可标记废弃（未被串用）",
    !!item(1).querySelector('button[data-action="abandon"]'));

  // 5) 导出按钮在仍有待确认时禁用；解禁触发请求验证 409 回路
  check("导出按钮在有待确认时禁用",
    document.getElementById("exportBtn").disabled === true);
  document.getElementById("exportBtn").disabled = false;
  document.getElementById("exportBtn").click();
  await tick();
  check("发出 1 次导出尝试", exports.length === 1);
  const d1 = exports[0].decisions;
  check("决策为数组形态(decision_list)", Array.isArray(d1) && d1.length === 1);
  check("决策 id 为整数 1（类型保留）",
    typeof d1[0].id === "number" && d1[0].id === 1);
  check("决策内容为选择候选2",
    d1[0].action === "choose" && d1[0].start === 60 && d1[0].end === 64);
  // 409 后按 pending_ids（保留类型）跳到字符串 "1"，而非跳回整数 1
  check("409 后选中字符串 \"1\"", item(1).classList.contains("selected"));

  // 6) 撤销整数 1：恢复候选；字符串 "1" 不受影响
  click(item(0).querySelector("button.undo"));
  check("撤销后整数 1 恢复两个候选", item(0).querySelectorAll(".cand-row").length === 2);
  check("撤销不影响字符串 \"1\"",
    !!item(1).querySelector('button[data-action="abandon"]'));

  // 7) 标记字符串 "1" 废弃：整数 1 不被串用
  click(item(1).querySelector('button[data-action="abandon"]'));
  check("字符串 \"1\" 显示已废弃", !!item(1).querySelector(".decided.abandoned"));
  check("整数 1 仍待确认（废弃未串用）",
    item(0).querySelectorAll(".cand-row").length === 2);

  // 8) 选整数 1 候选 1，两条分别处理后导出成功
  click(item(0).querySelectorAll("button.choose")[0]);
  check("全部处理后导出按钮可用",
    document.getElementById("exportBtn").disabled === false);
  document.getElementById("exportBtn").click();
  await tick();
  check("第二次导出成功", exports.length === 2);
  const d2 = exports[1].decisions;
  check("两条决策均提交", d2.length === 2);
  check("决策顺序与输入一致 [1, \"1\"] 且类型保留",
    typeof d2[0].id === "number" && d2[0].id === 1 &&
    typeof d2[1].id === "string" && d2[1].id === "1");
  check("整数 1 选候选1、字符串 \"1\" 废弃",
    d2[0].action === "choose" && d2[0].start === 5 && d2[0].end === 9 &&
    d2[1].action === "abandon");

  // 9) 原始批注回传保留类型
  check("annotations 原样回传且保留类型",
    typeof exports[1].annotations[0].id === "number" &&
    exports[1].annotations[0].id === 1 &&
    exports[1].annotations[1].id === "1");

  if (failures.length) {
    console.error("页面回归失败：");
    for (const f of failures) console.error("  - " + f);
    process.exit(1);
  }
  console.log("页面回归全部通过（20 项断言）");
})().catch((e) => {
  console.error(e);
  process.exit(1);
});
