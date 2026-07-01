/* workflow_canvas.ts — 工作流**全屏拖拽画布**(dev-report,Hardy:模态里编依赖是灾难,要画布)。
 *
 * 复用开源 Drawflow(纯 JS,零依赖,MIT)—— 不引框架(贴合我们 vanilla TS + Vite 栈)。点"编辑画布"
 * → 全屏 overlay 起 Drawflow → 拖节点/连依赖线 → 存(回写 plan)/ 取消(丢弃)返回。
 *
 * 设计边界(Hardy /btw):画布是**人**的创作/编辑面;**小卡自己唤起的 workflow 直接 DAG→执行**,
 * 不走画布确认(避免编排卡顿)。所以本模块只在人点"编辑画布"时用。
 *
 * 核心是两个**纯函数**(可单测往返保真):plan ↔ Drawflow export 格式。可视化壳只是包它们。
 */
import Drawflow from "drawflow";

// ---- 形状 ----
interface Role { agent_id: string; domain_id: string; display: string }
interface Step {
  id: string; agent_id?: string; domain_id?: string; display?: string;
  task?: string; depends_on?: string[]; on_fail?: string; when?: unknown; inputs?: string[];
}
interface Plan { goal: string; steps: Step[] }

// ---- 拓扑分层(给自动布局:同层并排,层间右移)----
function _levels(steps: Step[]): Record<string, number> {
  const byId: Record<string, Step> = {};
  steps.forEach((s) => { byId[s.id] = s; });
  const lv: Record<string, number> = {};
  const calc = (id: string, seen: Set<string>): number => {
    if (lv[id] != null) return lv[id];
    if (seen.has(id)) return 0;             // 环兜底
    seen.add(id);
    const deps = (byId[id]?.depends_on || []).filter((d) => byId[d]);
    const v = deps.length ? Math.max(...deps.map((d) => calc(d, seen))) + 1 : 0;
    lv[id] = v;
    return v;
  };
  steps.forEach((s) => calc(s.id, new Set()));
  return lv;
}

// ---- plan → Drawflow export 格式(供 editor.import)----
export function planToExport(plan: Plan, roles: Role[]): Record<string, unknown> {
  const steps = plan.steps || [];
  const nodeOf: Record<string, string> = {};
  steps.forEach((s, i) => { nodeOf[s.id] = String(i + 1); });
  const lv = _levels(steps);
  const perLevel: Record<number, number> = {};
  const data: Record<string, unknown> = {};
  steps.forEach((s) => {
    const nid = nodeOf[s.id];
    const level = lv[s.id] || 0;
    const row = perLevel[level] || 0; perLevel[level] = row + 1;
    const inputs = {
      input_1: {
        connections: (s.depends_on || []).filter((d) => nodeOf[d])
          .map((d) => ({ node: nodeOf[d], input: "output_1" })),
      },
    };
    const outputs = {
      output_1: {
        connections: steps.filter((t) => (t.depends_on || []).includes(s.id))
          .map((t) => ({ node: nodeOf[t.id], output: "input_1" })),
      },
    };
    data[nid] = {
      id: Number(nid), name: "wfstep", class: "wf-node", typenode: false,
      data: {
        step_id: s.id, agent_id: s.agent_id || "", domain_id: s.domain_id || "",
        display: s.display || s.agent_id || "", task: s.task || "",
        on_fail: s.on_fail || "", when_json: s.when ? JSON.stringify(s.when) : "",
      },
      html: _nodeHtml(s, roles),
      inputs, outputs,
      pos_x: 60 + level * 320, pos_y: 50 + row * 170,
    };
  });
  return { drawflow: { Home: { data } } };
}

// ---- Drawflow export → plan(存盘时把画布读回 DAG)----
export function graphToPlan(exp: Record<string, unknown>, goal: string): Plan {
  const home = (((exp || {}) as any).drawflow || {}).Home || {};
  const nodes: Record<string, any> = home.data || {};
  const idToStep: Record<string, string> = {};
  Object.values(nodes).forEach((n: any) => {
    idToStep[String(n.id)] = (n.data && n.data.step_id) || ("s" + n.id);
  });
  const steps: Step[] = [];
  // 稳定顺序:按 pos_x 再 pos_y(左→右、上→下),读起来跟布局一致
  const ordered = Object.values(nodes).sort((a: any, b: any) =>
    (a.pos_x - b.pos_x) || (a.pos_y - b.pos_y));
  ordered.forEach((n: any) => {
    const d = n.data || {};
    const depConns = (((n.inputs || {}).input_1 || {}).connections || []) as any[];
    const depends_on = depConns.map((c) => idToStep[String(c.node)]).filter(Boolean);
    let when: unknown = undefined;
    if (d.when_json) { try { when = JSON.parse(d.when_json); } catch { when = undefined; } }
    const step: Step = {
      id: idToStep[String(n.id)], agent_id: d.agent_id || "", domain_id: d.domain_id || "",
      display: d.display || d.agent_id || "", task: d.task || "", depends_on,
    };
    if (d.on_fail) step.on_fail = d.on_fail;
    if (when !== undefined) step.when = when;
    steps.push(step);
  });
  return { goal: goal || "", steps };
}

function _roleKey(r: { domain_id?: string; agent_id?: string }): string {
  return (r.domain_id || "") + "|" + (r.agent_id || "");
}
function _roleOptions(roles: Role[]): string {
  return (roles || []).map((r) =>
    `<option value="${_roleKey(r).replace(/"/g, "&quot;")}">` +
    `${(r.display || r.agent_id || "").replace(/</g, "&lt;")}</option>`).join("");
}

// ---- 单个节点的 HTML(角色名 + 任务 textarea[df-task] + 容错 select[df-on_fail])----
function _nodeHtml(s: Step, _roles: Role[]): string {
  const disp = (s.display || s.agent_id || "").replace(/</g, "&lt;");
  const task = (s.task || "").replace(/</g, "&lt;");
  const of = s.on_fail || "";
  const opt = (v: string, label: string) =>
    `<option value="${v}"${of === v ? " selected" : ""}>${label}</option>`;
  return (
    `<div class="wfn">` +
    `<div class="wfn-role">${disp}</div>` +
    `<textarea class="wfn-task" df-task rows="2" placeholder="这一步做什么">${task}</textarea>` +
    `<select class="wfn-onfail" df-on_fail>` +
    opt("", "失败:跳过") + opt("retry", "失败:重试") + opt("abort", "失败:中止") +
    `</select>` +
    `</div>`
  );
}

// ---- 全屏画布:打开 → 编辑 → 存(onSave(plan))/ 取消 ----
function open(plan: Plan, roles: Role[], onSave: (p: Plan) => void): void {
  const overlay = document.createElement("div");
  overlay.className = "wf-canvas-overlay";
  const bar = document.createElement("div");
  bar.className = "wf-canvas-bar";
  bar.innerHTML =
    `<span class="wf-canvas-title">🎨 工作流画布 · 拖节点连依赖</span>` +
    `<span class="wf-canvas-hint">左圈=入口(空心)· 右圈=出口(实心)· 从上一步的右出口拖到下一步的左入口 ` +
    `= 后者等前者做完;不连=起步并行 · <b>滚轮缩放</b>(节点多时缩小看全局)</span>` +
    `<span class="wf-canvas-actions">` +
    `<span class="wf-cv-zoombar" title="滚轮也能缩放">` +
    `<button class="wf-cv-zoom" data-z="out">－</button>` +
    `<button class="wf-cv-zoom" data-z="reset">⟲</button>` +
    `<button class="wf-cv-zoom" data-z="in">＋</button></span>` +
    `<select class="wf-cv-role" title="新一步派给哪个角色">${_roleOptions(roles)}</select>` +
    `<button class="wf-cv-add">+ 加一步</button>` +
    `<button class="wf-cv-cancel">取消</button>` +
    `<button class="wf-cv-save">✅ 保存</button></span>`;
  const canvas = document.createElement("div");
  canvas.className = "wf-canvas-area";
  overlay.appendChild(bar); overlay.appendChild(canvas);
  document.body.appendChild(overlay);

  const editor = new Drawflow(canvas);
  editor.reroute = true;
  editor.start();
  try { editor.import(planToExport(plan, roles)); } catch { /* 空/坏 plan → 空画布 */ }

  // 滚轮缩放:Drawflow 原生只认 ctrl+滚轮 → 节点一多很难看全局(Hardy)。让**裸滚轮**也缩放;
  // ctrl+滚轮 仍交给 Drawflow 原生处理,不重复触发。
  canvas.addEventListener("wheel", (e: WheelEvent) => {
    if (e.ctrlKey) return;
    e.preventDefault();
    if (e.deltaY > 0) editor.zoom_out(); else editor.zoom_in();
  }, { passive: false });
  // ＋ / ⟲ / － 缩放按钮(触摸板/不熟滚轮的人也能缩放)
  bar.querySelectorAll(".wf-cv-zoom").forEach((b) => {
    (b as HTMLElement).onclick = () => {
      const z = (b as HTMLElement).getAttribute("data-z");
      if (z === "in") editor.zoom_in();
      else if (z === "out") editor.zoom_out();
      else editor.zoom_reset();
    };
  });

  let _addN = (plan.steps || []).reduce(
    (m, s) => Math.max(m, parseInt((s.id || "s0").slice(1), 10) || 0), 0);
  const close = () => { try { editor.clear(); } catch { /* noop */ } overlay.remove(); };

  (bar.querySelector(".wf-cv-add") as HTMLElement).onclick = () => {
    _addN += 1;
    // 用工具栏选的角色建新节点(不再默认第一个角色 —— Hardy)
    const roleSel = bar.querySelector(".wf-cv-role") as HTMLSelectElement | null;
    const key = (roleSel && roleSel.value) || "";
    const r = roles.find((x) => _roleKey(x) === key)
      || roles[0] || { agent_id: "", domain_id: "", display: "角色" };
    const s: Step = { id: "s" + _addN, agent_id: r.agent_id, domain_id: r.domain_id, display: r.display, task: "", depends_on: [] };
    editor.addNode("wfstep", 1, 1, 80, 80, "wf-node",
      { step_id: s.id, agent_id: r.agent_id, domain_id: r.domain_id, display: r.display, task: "", on_fail: "", when_json: "" },
      _nodeHtml(s, roles), false);
  };
  (bar.querySelector(".wf-cv-cancel") as HTMLElement).onclick = close;
  (bar.querySelector(".wf-cv-save") as HTMLElement).onclick = () => {
    const newPlan = graphToPlan(editor.export(), plan.goal);
    close();
    onSave(newPlan);
  };
}

const KarvyWorkflowCanvas = { open, planToExport, graphToPlan };
(window as unknown as { KarvyWorkflowCanvas: typeof KarvyWorkflowCanvas }).KarvyWorkflowCanvas = KarvyWorkflowCanvas;
export { KarvyWorkflowCanvas };
