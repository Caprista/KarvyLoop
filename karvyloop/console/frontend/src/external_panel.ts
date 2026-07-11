/* external_panel.ts — 🔌 外部 runtime 管理面(跨 runtime 协作:BYO 第三方 headless CLI）。
 * 后端 /api/external/citizens 给已接入的外部公民(带 tier + 在线状态灯);这里给每个:
 *   - 🔌 醒目外部徽标(异色 external，一眼知道"不透明外部执行体、输出 untrusted"，绝不与原生角色混脸)
 *   - 在线状态灯(online/offline/unreachable，可点刷新单个)
 *   - 删除按钮(POST /api/external/detach，走后端来源门)
 *   - 直聊按钮(复用 directChatRole/peer-switch 路径：外部公民也能 l0 单独会话)
 * ＋添加外部 runtime(认领码配对握手,反向接入 = GitHub-runner-注册那种,不是本机填 bin):
 *   点它 → POST /api/external/create_pending 建 pending 壳 + 发一次性认领秘钥 → 弹出"复制这段到你的
 *   runtime 里跑,等待接入…"(含秘钥 + 回调 URL + 现成命令)→ 用户在自己 runtime 跑连接器 POST 回
 *   /api/external/claim → 校验激活 → 前端轮询到壳翻 active,pending 卡变"在线"正式公民。取消走
 *   /api/external/cancel_pending。秘钥一次性、10 分钟过期、绑单壳;明文只在建壳响应里出现一次。
 * 底部：按需接入引导（/api/external/onboarding）—— 没装给官方安装指引（从官方源装，我们不 bundle 别人家软件）。
 * 跨面板依赖：删/认领成功后 refreshPeers()；直聊经注入的 directChatPeer（外部 peer 寻址）。
 * 暴露 window.KarvyExternalPanel.open(deps)。
 * scope(M2):握手 + 本机驱动;真远程持久通道(runtime 在另一台机器长连回连)= M3 TODO(见 connector.py)。
 */
type Attrs = Record<string, unknown>;
type Child = Node | string | null | undefined;
interface Dom {
  el: (tag: string, attrs?: Attrs | null, ...children: Child[]) => HTMLElement;
  getJSON: (url: string) => Promise<any>;
  postJSON: (url: string, payload: unknown) => Promise<{ ok: boolean; status: number; data: any }>;
}
interface Modal {
  openMgmtModal: (title: string) => void;
  closeMgmtModal: () => void;
  mgmtBody: () => HTMLElement | null;
}
interface I18n { t: (key: string, vars?: Record<string, unknown>) => string }
// 直聊外部公民 = 切到 peer=(域, "external", citizen_id)（后端 EXTERNAL_ROLE，不与原生 role 混脸）。
// 由 app.js 经 open({ refreshPeers, directChatPeer }) 注入；缺注入时静默降级（按钮不动作，不崩）。
interface Deps {
  refreshPeers?: () => void;
  directChatPeer?: (peer: { domain_id: string; role: string; agent_id: string }, label: string) => void;
}

const _KD = (window as unknown as { KarvyDom: Dom }).KarvyDom;
const _KM = (window as unknown as { KarvyModal: Modal }).KarvyModal;
const el = _KD.el, _getJSON = _KD.getJSON, _postJSON = _KD.postJSON;
const openMgmtModal = _KM.openMgmtModal, mgmtBody = _KM.mgmtBody, closeMgmtModal = _KM.closeMgmtModal;
const t = (k: string, vars?: Record<string, unknown>) =>
  (window as unknown as { KarvyI18n: I18n }).KarvyI18n.t(k, vars);

let _deps: Deps = {};

// 官方源接入指引外链（各类 headless CLI agent 的官方文档站；只作 <a> 展示，代码绝不请求/bundle）。
// 中性表述（公开仓）：这些是"从官方装外部 runtime"的去处，不写死任何一个当依赖。
const OFFICIAL_DOCS_HINT_KEY = "external.onboarding.docs_hint";

// 在线状态灯：online=绿 / offline=灰 / unreachable=红。醒目但不喧宾夺主。
function _statusLight(status: string): HTMLElement {
  const s = status === "online" ? "online" : (status === "unreachable" ? "unreachable" : "offline");
  return el("span", { class: "ext-light ext-light-" + s, title: t("external.status_" + s) },
    el("span", { class: "ext-dot" }), " ",
    el("span", { class: "ext-light-label", text: t("external.status_" + s) }));
}

// 🔌 醒目外部徽标 + tier：异色，明说"外部执行体 · 输出 untrusted"，绝不和原生角色混脸。
function _externalBadge(tier: string): HTMLElement {
  const tierKey = tier === "scoped" ? "external.tier_scoped" : "external.tier_guest";
  return el("span", { class: "ext-badge", title: t("external.badge_title") },
    "🔌 ", t("external.badge"), " · ", t(tierKey));
}

// 复制到剪贴板(优先 navigator.clipboard;不可用时退回选中提示)。返回是否成功。
async function _copyText(text: string): Promise<boolean> {
  try {
    const nav = (window.navigator as any);
    if (nav && nav.clipboard && nav.clipboard.writeText) {
      await nav.clipboard.writeText(text);
      return true;
    }
  } catch (e) { /* 降级到手动选中 */ }
  return false;
}

// 等待接入的 pending 壳卡:显示"等待接入…" + 复制指令(含秘钥)+ 取消。认领成功后轮询翻成在线。
function _pendingCard(c: any, host: HTMLElement): HTMLElement {
  const card = el("div", { class: "mgmt-card ext-card ext-card-pending" });
  const main = el("div", { class: "mc-main" },
    el("div", { class: "mc-name" },
      el("span", { text: c.citizen_id || "?" }), " ",
      el("span", { class: "ext-badge ext-badge-pending", title: t("external.pending_title") },
        "🔌 ", t("external.pending_badge"))),
    el("div", { class: "mc-meta ext-meta" },
      el("span", { class: "ext-light ext-light-pending" },
        el("span", { class: "ext-dot" }), " ",
        el("span", { class: "ext-light-label", text: t("external.status_pending") })),
      c.domain_id ? el("span", { text: " · " + t("external.in_domain", { domain: c.domain_id }) }) : null));
  main.appendChild(el("div", { class: "mc-meta ext-pending-hint", text: t("external.pending_waiting") }));
  const actions = el("div", { class: "dpref-actions" });
  // 🗑 取消等待(撤壳 + 作废秘钥)
  actions.appendChild(el("button", { class: "mc-del", text: t("external.cancel_pending"),
    onclick: async () => {
      if (!window.confirm(t("external.confirm_cancel", { name: c.citizen_id }))) return;
      const res = await _postJSON("/api/external/cancel_pending",
        { citizen_id: c.citizen_id, domain_id: c.domain_id || "" });
      if (res.ok && res.data && res.data.ok) { await render(host); }
      else { window.alert(t("external.cancel_failed", { reason: (res.data && res.data.reason) || res.status })); }
    } }));
  card.appendChild(main);
  card.appendChild(actions);
  return card;
}

function _citizenCard(c: any, host: HTMLElement): HTMLElement {
  if (c.pending) return _pendingCard(c, host);
  const card = el("div", { class: "mgmt-card ext-card" });
  const light = _statusLight(c.liveness || "offline");
  const main = el("div", { class: "mc-main" },
    el("div", { class: "mc-name" },
      el("span", { text: c.citizen_id || "?" }), " ", _externalBadge(c.tier || "guest")),
    el("div", { class: "mc-meta ext-meta" },
      light, " · ",
      el("span", { text: t("external.runtime_kind", { kind: c.runtime_kind || "—" }) }),
      c.domain_id ? el("span", { text: " · " + t("external.in_domain", { domain: c.domain_id }) }) : null,
      c.version ? el("span", { text: " · " + c.version }) : null));
  // 每张卡下一行诚实提示：外部执行体输出是 untrusted 数据（不占决策席、不进记忆护城河）。
  main.appendChild(el("div", { class: "mc-meta ext-untrusted", text: t("external.untrusted_note") }));

  const actions = el("div", { class: "dpref-actions" });
  // 💬 直聊：外部公民也能 l0 单独会话（peer role 段固定 external）。
  actions.appendChild(el("button", { class: "dpref-confirm", text: t("external.direct_chat"),
    onclick: () => {
      const peer = c.chat_peer || { domain_id: c.domain_id || "", role: "external", agent_id: c.citizen_id };
      const label = "🔌 " + (c.citizen_id || "external");
      if (_deps.directChatPeer) { closeMgmtModal(); _deps.directChatPeer(peer, label); }
    } }));
  // 🔄 刷新在线灯（单个探活）
  actions.appendChild(el("button", { class: "dpref-edit", text: t("external.refresh_status"),
    onclick: async () => {
      const r = await _getJSON("/api/external/liveness?citizen_id=" + encodeURIComponent(c.citizen_id)
        + "&domain=" + encodeURIComponent(c.domain_id || ""));
      const st = (r && r.status) || "offline";
      const fresh = _statusLight(st);
      light.replaceWith(fresh);
    } }));
  // 🗑 删除（解绑）
  actions.appendChild(el("button", { class: "mc-del", text: t("mgmt.delete"),
    onclick: async () => {
      if (!window.confirm(t("external.confirm_detach", { name: c.citizen_id }))) return;
      const res = await _postJSON("/api/external/detach",
        { citizen_id: c.citizen_id, domain_id: c.domain_id || "" });
      if (res.ok && res.data && res.data.ok) {
        if (_deps.refreshPeers) _deps.refreshPeers();
        await render(host);
      } else {
        window.alert(t("external.detach_failed", { reason: (res.data && res.data.reason) || res.status }));
      }
    } }));
  card.appendChild(main);
  card.appendChild(actions);
  return card;
}

// 一次性状态：正在等待某个 pending 壳被认领 → 轮询 /api/external/citizens,壳消失或翻 active 即停。
let _pollTimer: number | null = null;
function _stopPoll(): void {
  if (_pollTimer !== null) { window.clearInterval(_pollTimer); _pollTimer = null; }
}

// 「＋添加外部 runtime」流:建壳发码 → 弹出"复制这段到你的 runtime 里跑,等待接入…" → 轮询翻在线。
async function _startAddFlow(host: HTMLElement): Promise<void> {
  const citizenId = window.prompt(t("external.add_prompt_name"), "");
  if (!citizenId || !citizenId.trim()) return;
  const res = await _postJSON("/api/external/create_pending", { citizen_id: citizenId.trim() });
  if (!(res.ok && res.data && res.data.ok)) {
    window.alert(t("external.add_failed", { reason: (res.data && res.data.reason) || res.status }));
    return;
  }
  const d = res.data;
  // 复制指令面板:秘钥 + 回调 URL + 现成命令。醒目提示"秘钥一次性、只显示这一次、10 分钟内认领"。
  const box = el("div", { class: "ext-claim-box" });
  box.appendChild(el("div", { class: "ext-claim-title", text: t("external.claim_ready_title", { name: citizenId.trim() }) }));
  box.appendChild(el("div", { class: "mgmt-hint ext-claim-warn", text: t("external.claim_secret_once") }));
  // 连接器命令(推荐)+ 应急 curl,各带复制按钮。
  const mkCopyRow = (labelKey: string, cmd: string) => {
    const row = el("div", { class: "ext-claim-row" });
    row.appendChild(el("div", { class: "ext-claim-label", text: t(labelKey) }));
    const pre = el("pre", { class: "ext-claim-cmd", text: cmd });
    row.appendChild(pre);
    const btn = el("button", { class: "dpref-edit", text: t("external.copy") });
    btn.addEventListener("click", async () => {
      const ok = await _copyText(cmd);
      (btn as HTMLElement).textContent = ok ? t("external.copied") : t("external.copy_manual");
      window.setTimeout(() => { (btn as HTMLElement).textContent = t("external.copy"); }, 1600);
    });
    row.appendChild(btn);
    return row;
  };
  box.appendChild(mkCopyRow("external.claim_connector_label", d.connector_cmd || ""));
  box.appendChild(mkCopyRow("external.claim_curl_label", d.curl_cmd || ""));
  box.appendChild(el("div", { class: "mgmt-hint ext-claim-waiting", text: t("external.claim_waiting") }));
  const doneBtn = el("button", { class: "dpref-confirm", text: t("external.claim_done") });
  doneBtn.addEventListener("click", async () => { _stopPoll(); await render(host); });
  box.appendChild(doneBtn);
  host.innerHTML = "";
  host.appendChild(box);
  // 轮询:壳翻 active(pending=false)→ 认领成功,刷新回列表;壳消失(取消/过期后清)→ 也刷新。
  _stopPoll();
  const dom = d.citizen ? (d.citizen.domain_id || "") : "";
  const cid = d.citizen ? (d.citizen.citizen_id || citizenId.trim()) : citizenId.trim();
  _pollTimer = window.setInterval(async () => {
    let data: any = null;
    try { data = await _getJSON("/api/external/citizens"); } catch (e) { return; }
    const list: any[] = (data && data.citizens) || [];
    const shell = list.find((x) => x.citizen_id === cid && (x.domain_id || "") === dom);
    if (!shell || !shell.pending) {   // 翻在线 or 壳没了 → 停轮询,回列表
      _stopPoll();
      if (shell && !shell.pending && _deps.refreshPeers) _deps.refreshPeers();
      await render(host);
    }
  }, 2500) as unknown as number;
}

// 按需接入引导：没装 → 官方安装指引（从官方源装；我们绝不代托管/不 bundle/不 git clone 他家代码）。
async function _renderOnboarding(host: HTMLElement): Promise<void> {
  const box = el("div", { class: "ext-onboarding" });
  box.appendChild(el("div", { class: "mgmt-section-title", text: t("external.onboarding.title") }));
  let d: any = null;
  try { d = await _getJSON("/api/external/onboarding"); } catch (e) { /* 探测失败：仍给通用引导 */ }
  const present = !!(d && d.present);
  if (present) {
    // 已自带 → 说"检测到，接入向导在派活/圆桌里 @ 它即可"
    box.appendChild(el("div", { class: "mgmt-hint", text: t("external.onboarding.present",
      { bins: (d.found_bins || []).join(", ") || "—" }) }));
  } else {
    // 没自带 → 明确：这是一段单独的、按需的引导；从官方源装一个 headless CLI agent 再来接。
    box.appendChild(el("div", { class: "mgmt-hint", text: t("external.onboarding.absent") }));
  }
  // 红线声明（装没装都给，也是审计事实）：外部 runtime 是你自带的第三方软件，我们不分发它。
  box.appendChild(el("div", { class: "mgmt-hint ext-boundary", text: t("external.onboarding.we_dont_bundle") }));
  box.appendChild(el("div", { class: "mgmt-hint", text: t(OFFICIAL_DOCS_HINT_KEY) }));
  host.appendChild(box);
}

async function render(body: HTMLElement): Promise<void> {
  _stopPoll();   // 回列表视图必停任何进行中的认领轮询(防泄漏定时器)
  body.innerHTML = "";
  body.appendChild(el("div", { class: "mgmt-hint", text: t("external.intro") }));
  // ＋添加外部 runtime:认领码配对握手(反向接入)。点它 → 建壳发码 → 弹复制指令 + 等待接入。
  const addBtn = el("button", { class: "mgmt-add-btn ext-add-btn", text: t("external.add_btn") });
  addBtn.addEventListener("click", () => { _startAddFlow(body); });
  body.appendChild(addBtn);
  let data: any = null;
  try { data = await _getJSON("/api/external/citizens"); } catch (e) { /* 后端不可达：仍渲染引导 */ }
  const citizens: any[] = (data && data.citizens) || [];
  // C1 集成待接的诚实标注（注册表未接线时后端会给 _integration_pending）。
  if (data && data._integration_pending) {
    body.appendChild(el("div", { class: "mgmt-hint ext-pending",
      text: t("external.integration_pending") }));
  }
  if (!citizens.length) {
    body.appendChild(el("div", { class: "mgmt-empty", text: t("external.empty") }));
  } else {
    const list = el("div", { class: "mgmt-list" });
    for (const c of citizens) list.appendChild(_citizenCard(c, body));
    body.appendChild(list);
  }
  await _renderOnboarding(body);
}

async function open(deps?: Deps): Promise<void> {
  if (deps) _deps = deps;   // app.js 注入 refreshPeers + 直聊外部 peer 通道；nav 无参调用保留上次注入
  openMgmtModal(t("external.title"));
  const body = mgmtBody(); if (!body) return;
  await render(body);
}

const KarvyExternalPanel = { open };
(window as unknown as { KarvyExternalPanel: typeof KarvyExternalPanel }).KarvyExternalPanel = KarvyExternalPanel;
export { KarvyExternalPanel };
