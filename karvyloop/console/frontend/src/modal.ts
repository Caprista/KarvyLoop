/* modal.ts — 管理面模态 + 表单消息基建(从 app.js 抽出,dev-report #4 slice 4)。
 * 模态开/关 + body 取用 + 表单消息(formMsg/setMsg);含"无 Key 强制引导"锁
 * (setSetupLocked:锁住时模态关不掉,直到配好可用模型)。各面板都依赖它 → 抽出来当共享基建。
 * 暴露 window.KarvyModal;用 window.KarvyDom.el(slice 3)。
 */
type Attrs = Record<string, unknown>;
type Child = Node | string | null | undefined;
interface Dom { el: (tag: string, attrs?: Attrs | null, ...children: Child[]) => HTMLElement }

function dom(): Dom {
  return (window as unknown as { KarvyDom: Dom }).KarvyDom;
}

let _setupLocked = false;   // 无 Key 强制引导:锁住时模态不可关(直到配好可用模型)
// CFG-01①(内测):模型设置窗要禁"点空白关闭"(防切页误关),其余面板维持原交互。
// 每次 openMgmtModal 按 opts 重置 —— 面板切换(roles→models→…)各自声明,互不残留。
let _backdropClose = true;
let _escClose = false;      // Esc 关闭默认关(与既有全局行为一致),要的窗显式开

interface OpenOpts { backdropClose?: boolean; escClose?: boolean }

function openMgmtModal(title: string, opts?: OpenOpts): void {
  _backdropClose = !opts || opts.backdropClose !== false;
  _escClose = !!(opts && opts.escClose);
  const ttl = document.getElementById("mgmt-title");
  if (ttl) ttl.textContent = title;
  document.getElementById("mgmt-modal")?.classList.remove("hidden");
}
function closeMgmtModal(): void {
  if (_setupLocked) return;  // 强制引导期间:关不掉(没 Key 用不了)
  document.getElementById("mgmt-modal")?.classList.add("hidden");
}
function mgmtBody(): HTMLElement | null { return document.getElementById("mgmt-body"); }
function setSetupLocked(locked: boolean): void { _setupLocked = locked; }
function backdropCloseEnabled(): boolean { return _backdropClose; }  // app.js 蒙层点击处查它

// Esc 关闭:只对声明了 escClose 的窗生效(CFG-01① 模型设置窗禁蒙层后仍留 ✕/Esc 两条出路)。
document.addEventListener("keydown", (e: KeyboardEvent) => {
  if (e.key !== "Escape" || e.defaultPrevented || !_escClose) return;
  const m = document.getElementById("mgmt-modal");
  if (!m || m.classList.contains("hidden")) return;
  e.preventDefault();
  closeMgmtModal();
});

function formMsg(): HTMLElement { return dom().el("div", { class: "mgmt-msg" }); }
function setMsg(msg: HTMLElement, ok: boolean, text: string): void {
  msg.className = "mgmt-msg " + (ok ? "ok" : "err");
  msg.textContent = text;
}

const KarvyModal = { openMgmtModal, closeMgmtModal, mgmtBody, setSetupLocked,
  backdropCloseEnabled, formMsg, setMsg };
(window as unknown as { KarvyModal: typeof KarvyModal }).KarvyModal = KarvyModal;
export { KarvyModal };
