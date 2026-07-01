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

function openMgmtModal(title: string): void {
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

function formMsg(): HTMLElement { return dom().el("div", { class: "mgmt-msg" }); }
function setMsg(msg: HTMLElement, ok: boolean, text: string): void {
  msg.className = "mgmt-msg " + (ok ? "ok" : "err");
  msg.textContent = text;
}

const KarvyModal = { openMgmtModal, closeMgmtModal, mgmtBody, setSetupLocked, formMsg, setMsg };
(window as unknown as { KarvyModal: typeof KarvyModal }).KarvyModal = KarvyModal;
export { KarvyModal };
