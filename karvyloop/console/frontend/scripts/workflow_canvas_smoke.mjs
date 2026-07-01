/* workflow_canvas_smoke.mjs — 验证画布的**数据往返保真**(可视化拖拽要 Hardy 肉眼看;这里锁纯逻辑)。
 * plan → planToExport(Drawflow 格式)→ graphToPlan → plan:goal / 步骤 / 依赖 / on_fail / when 不丢不错。
 */
import { JSDOM } from "jsdom";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, resolve } from "node:path";
import assert from "node:assert/strict";

const dom = new JSDOM("<!doctype html><body></body>");
globalThis.window = dom.window;
globalThis.document = dom.window.document;

const here = dirname(fileURLToPath(import.meta.url));
(0, eval)(readFileSync(resolve(here, "../../static/workflow_canvas.js"), "utf8"));
const C = dom.window.KarvyWorkflowCanvas;
assert.ok(C && typeof C.planToExport === "function" && typeof C.graphToPlan === "function",
  "window.KarvyWorkflowCanvas 契约缺失");

const roles = [{ agent_id: "a", domain_id: "", display: "分析师" }];
// 一个有分支/并行/合并/容错的 plan(覆盖各字段)
const plan = {
  goal: "写一份 AI 时代生存守则 PPT",
  steps: [
    { id: "s1", agent_id: "a", domain_id: "", display: "人类学家", task: "人类学视角", depends_on: [] },
    { id: "s2", agent_id: "a", domain_id: "", display: "地理学家", task: "地理学视角", depends_on: [] },
    { id: "s3", agent_id: "a", domain_id: "", display: "叙事学家", task: "梳理叙事", depends_on: ["s1", "s2"] },
    { id: "s4", agent_id: "a", domain_id: "", display: "整理", task: "出大纲", depends_on: ["s3"], on_fail: "retry" },
    { id: "s5", agent_id: "a", domain_id: "", display: "发布", task: "发布", depends_on: ["s4"], when: { step: "s4", status: "done" } },
  ],
};

const out = C.graphToPlan(C.planToExport(plan, roles), plan.goal);
assert.equal(out.goal, plan.goal, "goal 丢了");
assert.equal(out.steps.length, plan.steps.length, "步骤数变了");

const byId = {};
out.steps.forEach((s) => { byId[s.id] = s; });
for (const orig of plan.steps) {
  const got = byId[orig.id];
  assert.ok(got, `步骤 ${orig.id} 丢了`);
  assert.equal(got.task, orig.task, `${orig.id} 任务变了`);
  assert.deepEqual([...(got.depends_on || [])].sort(), [...(orig.depends_on || [])].sort(),
    `${orig.id} 依赖往返不保真`);
  assert.equal(got.on_fail || "", orig.on_fail || "", `${orig.id} on_fail 丢了`);
  assert.deepEqual(got.when ?? null, orig.when ?? null, `${orig.id} when 丢了`);
}
console.log(`✓ workflow canvas smoke OK — plan↔Drawflow 往返保真(${plan.steps.length} 步、依赖/分支/容错都不丢)`);
