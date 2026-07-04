/* pixelpet.ts — 卡皮巴拉 sprite 引擎(docs/53 P1.5:灵魂的"身体")。
 *
 * v2(原图版):手绘 32×24 像素帧已废 —— 直接用官方 IP 原图
 * (assets/karvy-capybara.png,透明底,Q 版卡皮巴拉+浅蓝连体衣+胸口发光∞+叼绿叶)
 * 当 sprite;状态全部交给 CSS 动画类表达(desktop.css 按 data-state 匹配):
 *   idle=缓慢呼吸浮动 / working=快节奏打字颠动+键盘微光条 / carry=左右摇摆+叼小白卡
 *   / sleep=变暗+Zzz / happy=轻快一跳。prefers-reduced-motion → CSS 动画全停(静止首帧)。
 * 角色区分:图不换,accent 变成胸前彩色工牌(CSS 变量 --pet-accent);
 * colorForRole 的确定性映射原样保留(同一角色永远同色,位置/颜色不断裂)。
 *
 * 红线(docs/53 §0,"假戏 idle 被用户嘲讽"的行业教训)不变:**引擎自己绝不换状态**。
 * setState 只由真实事件的消费方调用(task_status/role_presence/h2a_*);引擎内部
 * 没有任何定时器 —— idle 的"呼吸"是 CSS 动画级的在场感,不是戏。没有闲逛、没有假装干活。
 *
 * 对外契约保形:window.KarvyPixelPet = { createPet, validateFrames, buildPalette,
 *   colorForRole, STATES, FRAMES, WIDTH, HEIGHT, KARVY_ACCENT }(desktop.ts import
 *   消费;smoke 测试直查)。FRAMES/validateFrames 是兼容桩(帧数据已废);
 *   createPet 仍收 { canvas, accent }:传入的 canvas 是挂载占位,进来即被 sprite 根
 *   元素原位替换(id 随根元素保留,#desk-karvy-pixel 等选择器照常命中)。
 */

/** 官方 IP 原图(console 静态服务路径;透明底,原图文件不动)。 */
export const SPRITE_URL = "/static/assets/karvy-capybara.png";

/** 原图自然尺寸(px)。历史上是 32×24 像素帧的逻辑分辨率,现指原图,仅契约兼容保留。 */
export const WIDTH = 441;
export const HEIGHT = 512;

/** 亮度缩放(工牌暗部/描边从 accent 推导,不用单配第二个色)。 */
function shade(hex: string, f: number): string {
  const m = /^#?([0-9a-f]{6})$/i.exec(hex || "");
  if (!m) return "#777777";
  const n = parseInt(m[1], 16);
  const r = Math.max(0, Math.min(255, Math.round(((n >> 16) & 255) * f)));
  const g = Math.max(0, Math.min(255, Math.round(((n >> 8) & 255) * f)));
  const b = Math.max(0, Math.min(255, Math.round((n & 255) * f)));
  return "#" + ((1 << 24) | (r << 16) | (g << 8) | b).toString(16).slice(1);
}

/** accent 归一化:非法输入回退品牌浅蓝(和旧 palette 换色同一语义)。 */
function normalizeAccent(accent?: string): string {
  const a = accent || "";
  if (!/^#?[0-9a-f]{6}$/i.test(a)) return KARVY_ACCENT;
  return a[0] === "#" ? a : "#" + a;
}

/** 兼容桩:旧像素 palette 的对外形状(A=accent 主色 / a=暗部推导)。 */
export function buildPalette(accent: string): Record<string, string> {
  const A = normalizeAccent(accent);
  return { A: A, a: shade(A, 0.72) };
}

/* 角色配色:小卡 = 品牌浅蓝(IP 基调);其余角色从固定色环按 role_id 确定性取色
 * (同一角色永远同色 —— 位置/颜色不断裂,docs/53 原则四)。 */
export const KARVY_ACCENT = "#8fc7e8";
const ROLE_ACCENTS = [
  "#e07a5f", "#8e7cc3", "#6a994e", "#d4a373",
  "#457b9d", "#bc6c25", "#c76b8e", "#5f9ea0",
];

export function colorForRole(roleId: string): string {
  if (!roleId || roleId === "karvy") return KARVY_ACCENT;
  let h = 0;
  for (let i = 0; i < roleId.length; i++) h = ((h << 5) - h + roleId.charCodeAt(i)) | 0;
  return ROLE_ACCENTS[Math.abs(h) % ROLE_ACCENTS.length];
}

/** 兼容桩:手绘帧数据已废(原图 + CSS 动画),恒为空。 */
export const FRAMES: Record<string, string[][]> = {};

/** 可对外 setState 的状态(语义不变,docs/53 冻结:5 个真实驱动态,0 戏剧态)。 */
export const STATES = ["idle", "working", "carry", "sleep", "happy"] as const;
export type PetState = (typeof STATES)[number];

/** 兼容桩:帧数据已废,永远合法(smoke 断言其为 [])。 */
export function validateFrames(): string[] {
  return [];
}

export interface Pet {
  setState(s: string): boolean;
  state(): PetState;
  destroy(): void;
  /** 重刷 data-state(reduced-motion / 测试用;无副作用可重入)。 */
  render(): void;
}

/* sprite DOM:
 *   <span class="karvy-sprite" data-state="idle" style="--pet-accent:…">
 *     <img class="karvy-sprite-img" src="…karvy-capybara.png">   原图本体
 *     <span class="karvy-sprite-badge">   彩色工牌(accent 区分角色)
 *     <span class="karvy-sprite-keys">    working:键盘微光条
 *     <span class="karvy-sprite-card">    carry:叼的小白卡
 *     <span class="karvy-sprite-zzz">     sleep:Zzz(文案在 CSS content,纯装饰)
 *   </span>
 * 全部 overlay 都 aria-hidden(工位/按钮自带 aria-label,sprite 是纯装饰)。 */
const OVERLAY_PARTS = ["badge", "keys", "card", "zzz"] as const;

export function createPet(opts: { canvas: HTMLCanvasElement; accent?: string }): Pet {
  const mount = opts.canvas as unknown as HTMLElement;   // 挂载占位(契约保形:调用方仍传 canvas)
  const accent = normalizeAccent(opts.accent);

  const root = document.createElement("span");
  root.className = "karvy-sprite";
  if (mount.id) root.id = mount.id;                      // id 随根元素走(#desk-karvy-pixel 等照常命中)
  root.style.setProperty("--pet-accent", accent);
  root.style.setProperty("--pet-accent-dim", shade(accent, 0.72));

  const img = document.createElement("img");
  img.className = "karvy-sprite-img";
  img.src = SPRITE_URL;
  img.alt = "";                                          // 纯装饰;可达名称由宿主(工位按钮等)提供
  img.setAttribute("aria-hidden", "true");
  img.draggable = false;
  root.appendChild(img);

  OVERLAY_PARTS.forEach((part) => {
    const el = document.createElement("span");
    el.className = "karvy-sprite-" + part;
    el.setAttribute("aria-hidden", "true");
    root.appendChild(el);
  });

  // 原位替换占位 canvas(未挂载的占位 → sprite 同样不挂载,状态机照常工作,测试靠这个跑)
  if (mount.parentNode) mount.parentNode.replaceChild(root, mount);

  let state: PetState = "idle";
  let destroyed = false;

  function render(): void {
    root.setAttribute("data-state", state);
  }

  function setState(s: string): boolean {
    if (destroyed) return false;
    if ((STATES as readonly string[]).indexOf(s) < 0) return false;   // 未知状态 = 拒绝,不瞎演
    if (s === state) return true;
    state = s as PetState;
    render();
    return true;
  }

  render();

  return {
    setState,
    state: () => state,
    render,
    // destroy 只封状态机(和旧引擎"只停表不拆 DOM"同语义);DOM 的去留归调用方管
    destroy: () => { destroyed = true; },
  };
}

const KarvyPixelPet = {
  createPet, validateFrames, buildPalette, colorForRole,
  STATES, FRAMES, WIDTH, HEIGHT, KARVY_ACCENT, SPRITE_URL,
};
if (typeof window !== "undefined") {
  (window as unknown as { KarvyPixelPet: typeof KarvyPixelPet }).KarvyPixelPet = KarvyPixelPet;
}

export default KarvyPixelPet;
