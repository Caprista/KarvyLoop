/* build.mjs — 增量迁移的多模块构建(dev-report #4)。
 * 每个迁好的模块各 build 成一个**固定名 IIFE** 落到 ../static(IIFE 不支持多入口 code-split,
 * 所以一模块一次 build)。保持 window.Karvy* 全局契约 → 未迁的 app.js 照常用。不 minify、不上框架。
 */
import { build } from "vite";
import { resolve, dirname } from "node:path";
import { fileURLToPath } from "node:url";

const here = dirname(fileURLToPath(import.meta.url));
const root = resolve(here, "..");              // frontend/
const outDir = resolve(root, "../static");      // console 原样服务的目录

const MODULES = [
  { entry: "src/dom.ts", name: "KarvyDomBundle", file: "dom.js" },
  { entry: "src/modal.ts", name: "KarvyModalBundle", file: "modal.js" },
  { entry: "src/ui_widgets.ts", name: "KarvyWidgetsBundle", file: "ui_widgets.js" },
  { entry: "src/files_panel.ts", name: "KarvyFilesPanelBundle", file: "files_panel.js" },
  { entry: "src/schedules_panel.ts", name: "KarvySchedulesPanelBundle", file: "schedules_panel.js" },
  { entry: "src/diagnose_panel.ts", name: "KarvyDiagnosePanelBundle", file: "diagnose_panel.js" },
  { entry: "src/atoms_panel.ts", name: "KarvyAtomsPanelBundle", file: "atoms_panel.js" },
  { entry: "src/roles_panel.ts", name: "KarvyRolesPanelBundle", file: "roles_panel.js" },
  { entry: "src/domains_panel.ts", name: "KarvyDomainsPanelBundle", file: "domains_panel.js" },
  { entry: "src/agents_panel.ts", name: "KarvyAgentsPanelBundle", file: "agents_panel.js" },
  { entry: "src/external_panel.ts", name: "KarvyExternalPanelBundle", file: "external_panel.js" },
  { entry: "src/devices_panel.ts", name: "KarvyDevicesPanelBundle", file: "devices_panel.js" },
  { entry: "src/skills_panel.ts", name: "KarvySkillsPanelBundle", file: "skills_panel.js" },
  { entry: "src/unlock_panel.ts", name: "KarvyUnlockPanelBundle", file: "unlock_panel.js" },
  { entry: "src/demo_panel.ts", name: "KarvyDemoPanelBundle", file: "demo_panel.js" },
  { entry: "src/memory_panel.ts", name: "KarvyMemoryPanelBundle", file: "memory_panel.js" },
  { entry: "src/models_panel.ts", name: "KarvyModelsPanelBundle", file: "models_panel.js" },
  { entry: "src/decision_prefs_panel.ts", name: "KarvyDecisionPrefsBundle", file: "decision_prefs_panel.js" },
  { entry: "src/tokens_panel.ts", name: "KarvyTokensBundle", file: "tokens_panel.js" },
  { entry: "src/render.ts", name: "KarvyRenderBundle", file: "render.js" },
  { entry: "src/i18n.ts", name: "KarvyI18nBundle", file: "i18n.js" },
  { entry: "src/desktop.ts", name: "KarvyDesktopBundle", file: "desktop.js" },
  { entry: "src/workflow_canvas.ts", name: "KarvyWorkflowCanvasBundle", file: "workflow_canvas.js" },
  { entry: "src/m.ts", name: "KarvyMobileBundle", file: "m.js" },
];

for (const m of MODULES) {
  await build({
    root,
    configFile: false,
    logLevel: "warn",
    build: {
      outDir,
      emptyOutDir: false,   // 只覆盖自己产出的文件,别清掉 app.js/styles.css/vendor
      minify: false,        // 保留可读 + 让现有静态测试仍能 grep
      lib: {
        entry: resolve(root, m.entry),
        formats: ["iife"],
        name: m.name,       // 仅满足 iife 需要;真正契约是模块里 window.Karvy*=...
        fileName: () => m.file,
      },
      rollupOptions: { output: { entryFileNames: m.file } },
    },
  });
  console.log(`✓ built ${m.file}`);
}
