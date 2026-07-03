/* pixelpet.ts — 像素卡皮巴拉 sprite 引擎(docs/53 P1.5:灵魂的"身体")。
 *
 * 机制:
 *   - 像素画用 JS 数组定义:每帧 = 行字符串数组,每个字符 = palette 键
 *     (等价于"颜色索引二维数组",写起来紧凑、diff 可读)。
 *   - canvas 逻辑分辨率 32×24,CSS 放大到 64~96px + image-rendering:pixelated
 *     (nearest-neighbor,像素锐利不糊)。
 *   - palette 换色:同一形状,围巾/工牌(accent 键 A/a)按角色换主色 —— 一套帧,全员复用。
 *   - 动作帧:idle(呼吸 2 帧 + 偶尔眨眼)/ working(打字 3 帧)/ carry(叼卡走 2 帧)
 *     / sleep(闭眼 + Zzz 2 帧)/ happy(耳朵动 2 帧,拍板闭环用)。
 *
 * 红线(docs/53 §0,"假戏 idle 被用户嘲讽"的行业教训):**引擎自己绝不换状态**。setState 只由真实事件的
 * 消费方调用(task_status/role_presence/h2a_*);引擎内部唯一的"自主动作"是 idle 的
 * 呼吸帧轮换与低频眨眼 —— 这是"在场",不是戏。没有闲逛、没有假装干活。
 *
 * 对外契约:window.KarvyPixelPet = { createPet, validateFrames, buildPalette,
 *   colorForRole, STATES, FRAMES, WIDTH, HEIGHT }(desktop.ts import 消费;smoke 测试直查)。
 */

export const WIDTH = 32;
export const HEIGHT = 24;

/* ---- palette ----
 * "." 透明;B 身体主色;S 身体暗部;O 轮廓深棕;M 吻部浅色;D 深色(鼻/眼/键盘);
 * A 角色主色(围巾);a 角色主色暗部;W 白(叼的卡);G 屏幕微光;Z 睡觉的 Z 字。
 */
const BASE_COLORS: Record<string, string> = {
  B: "#b98a5e",
  S: "#9c6f47",
  O: "#6b4a2f",
  M: "#d7b58c",
  D: "#3a2b1f",
  W: "#fffdf5",
  G: "#cfe8ff",
  Z: "#8fa8c0",
};

/** 亮度缩放(围巾暗部从 accent 推导,不用单配第二个色)。 */
function shade(hex: string, f: number): string {
  const m = /^#?([0-9a-f]{6})$/i.exec(hex || "");
  if (!m) return "#777777";
  const n = parseInt(m[1], 16);
  const r = Math.max(0, Math.min(255, Math.round(((n >> 16) & 255) * f)));
  const g = Math.max(0, Math.min(255, Math.round(((n >> 8) & 255) * f)));
  const b = Math.max(0, Math.min(255, Math.round((n & 255) * f)));
  return "#" + ((1 << 24) | (r << 16) | (g << 8) | b).toString(16).slice(1);
}

export function buildPalette(accent: string): Record<string, string> {
  const p: Record<string, string> = {};
  Object.keys(BASE_COLORS).forEach((k) => { p[k] = BASE_COLORS[k]; });
  p["A"] = /^#?[0-9a-f]{6}$/i.test(accent || "") ? (accent[0] === "#" ? accent : "#" + accent) : "#8fc7e8";
  p["a"] = shade(p["A"], 0.72);
  return p;
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

/* ---- 基础帧(idle 吸气帧):侧视朝左的卡皮巴拉 —— 圆矮身、小耳、方鼻头、围巾 ---- */
const BASE: string[] = [
  "................................",
  "................................",
  "................................",
  "................................",
  ".....OO....OO...................",
  "....OSSO..OSSO..................",
  "...OBBBBBBBBBBOO................",
  "..OBBBBBBBBBBBBBOOOOOOOOO.......",
  ".ODDMBBBBBBBBBBBBBBBBBBBBOOO....",
  ".ODDMBBBBDDBBBBBBBBBBBBBBBBOO...",
  ".OMMMBBBBBBBBBBBBBBBBBBBBBBBO...",
  ".ODMMBBBBBBBBBBBBBBBBBBBBBBBO...",
  ".OMMMBBBBBAAAAAABBBBBBBBBBBBO...",
  "..OBBBBBBBaAAAAaBBBBBBBBBBBBO...",
  "..OBBBBBBBBAAAaBBBBBBBBBBBBBO...",
  "...OBBBBBBBAAaBBBBBBBBBBBBBO....",
  "...OBBBBBBBAAaBBBBBBBBBBBBBO....",
  "...OBBBBBBBaaBBSBBBBBBBBBBBO....",
  "...OBSBBBBBBBBSSBBBBBBBBBSBO....",
  "...OBSSBBBBBBSSSBBBBBBBBSSO.....",
  "....OBBBBBBBBBBBBBBBBBBBBO......",
  "......OBBO.........OBBO.........",
  "......OBBO.........OBBO.........",
  "................................",
];

/* ---- 帧派生工具(在基础帧上做像素操作,避免 11 份手抄的漂移)---- */
function norm(rows: string[]): string[] {
  const out: string[] = [];
  for (let y = 0; y < HEIGHT; y++) {
    let r = rows[y] || "";
    if (r.length < WIDTH) r = r + ".".repeat(WIDTH - r.length);
    out.push(r.slice(0, WIDTH));
  }
  return out;
}
function clone(rows: string[]): string[] { return rows.slice(); }
function put(rows: string[], x: number, y: number, ch: string): void {
  if (x < 0 || y < 0 || x >= WIDTH || y >= HEIGHT) return;
  rows[y] = rows[y].slice(0, x) + ch + rows[y].slice(x + 1);
}
function putRun(rows: string[], x: number, y: number, run: string): void {
  for (let i = 0; i < run.length; i++) put(rows, x + i, y, run[i]);
}
/** 列区间 [x0..x1] 在 y∈[1..yMax] 内整体下移 1px(顶行补透明)——呼吸/趴下的"压扁"。 */
function sag(rows: string[], x0: number, x1: number, yMax: number): void {
  for (let y = yMax; y >= 1; y--) {
    for (let x = x0; x <= x1; x++) {
      const above = rows[y - 1][x];
      put(rows, x, y, above);
    }
  }
  for (let x = x0; x <= x1; x++) put(rows, x, 0, ".");
}

function buildFrames(): Record<string, string[][]> {
  const base = norm(BASE);

  // idle:吸气(base)/ 呼气(后背下沉 1px)
  const idleB = clone(base);
  sag(idleB, 15, 29, 20);

  // blink:闭眼(眼睛 DD 抹掉,下移一条细线)—— idle 状态低频插播一帧
  const blink = clone(base);
  putRun(blink, 9, 9, "BB");
  putRun(blink, 9, 10, "DD");

  // working:面前一台笔记本(屏幕 D 框 + G 微光,底座键盘),前爪敲键盘交替
  function laptop(rows: string[], glowRows: number[]): string[] {
    const r = clone(rows);
    for (let y = 13; y <= 19; y++) putRun(r, 0, y, "DDG");           // 屏背 + 朝里的屏缘微光
    glowRows.forEach((y) => put(r, 3, y, "G"));                       // 微光闪(打字的屏在动)
    putRun(r, 0, 20, "DDDDDDDD");                                     // 底座/键盘
    return r;
  }
  const workA = laptop(base, [14, 16, 18]);
  putRun(workA, 5, 19, "OBO");   // 前爪落键
  const workB = laptop(base, [15, 17]);
  putRun(workB, 5, 18, "OBO");   // 前爪抬起
  const workC = laptop(base, [14, 15, 16, 17, 18]);
  putRun(workC, 7, 19, "OBO");   // 换另一只爪

  // carry:嘴里叼一张小白卡(W,D 描边),四腿交替走
  function withCard(rows: string[]): string[] {
    const r = clone(rows);
    putRun(r, 0, 10, "DDDDD");
    for (let y = 11; y <= 15; y++) putRun(r, 0, y, "DWWWD");
    putRun(r, 0, 16, "DDDDD");
    return r;
  }
  const carryA = withCard(base);
  const carryB = withCard(base);
  putRun(carryB, 6, 21, "....");                 // 换步:前腿前移、后腿后移
  putRun(carryB, 6, 22, "....");
  putRun(carryB, 4, 21, "OBO");
  putRun(carryB, 4, 22, "OBO");
  putRun(carryB, 19, 21, "....");
  putRun(carryB, 19, 22, "....");
  putRun(carryB, 22, 21, "OBO");
  putRun(carryB, 22, 22, "OBO");

  // sleep:闭眼 + 全身趴下 1px + Zzz(两帧 Z 字位置交替 = 缓慢的睡息)
  const sleepBase = clone(blink);
  sag(sleepBase, 2, 29, 20);
  const sleepA = clone(sleepBase);
  putRun(sleepA, 25, 2, "ZZZ");
  put(sleepA, 26, 3, "Z");
  putRun(sleepA, 25, 4, "ZZZ");
  const sleepZ = clone(sleepBase);
  putRun(sleepZ, 22, 5, "ZZ");
  putRun(sleepZ, 22, 6, "ZZ");

  // happy:耳朵外摆动一动 + 咧嘴(拍板闭环的一瞬,h2a_envelope 真实事件驱动)
  const happyA = clone(base);
  happyA[4] = "....OO......OO..................";
  happyA[5] = "...OSSO....OSSO.................";
  putRun(happyA, 2, 11, "DD");   // 咧嘴
  const happyB = clone(base);
  putRun(happyB, 2, 11, "DD");

  return {
    idle: [base, idleB],
    blink: [blink],
    working: [workA, workB, workC],
    carry: [carryA, carryB],
    sleep: [sleepA, sleepZ],
    happy: [happyA, happyB],
  };
}

export const FRAMES: Record<string, string[][]> = buildFrames();

/** 可对外 setState 的状态(blink 是 idle 的内部插帧,不是状态)。 */
export const STATES = ["idle", "working", "carry", "sleep", "happy"] as const;
export type PetState = (typeof STATES)[number];

/* ---- 帧数据合法性(smoke 用):尺寸一致 + 字符都在 palette 里 ---- */
export function validateFrames(): string[] {
  const errs: string[] = [];
  const legal = new Set(Object.keys(BASE_COLORS).concat(["A", "a", "."]));
  Object.keys(FRAMES).forEach((state) => {
    FRAMES[state].forEach((frame, fi) => {
      if (frame.length !== HEIGHT) errs.push(`${state}[${fi}]: ${frame.length} rows (want ${HEIGHT})`);
      frame.forEach((row, y) => {
        if (row.length !== WIDTH) errs.push(`${state}[${fi}] row${y}: ${row.length} cols (want ${WIDTH})`);
        for (let x = 0; x < row.length; x++) {
          if (!legal.has(row[x])) errs.push(`${state}[${fi}] (${x},${y}): bad char "${row[x]}"`);
        }
      });
    });
  });
  return errs;
}

/* ---- 渲染 + 状态机 ---- */
const TICK_MS: Record<PetState, number> = {
  idle: 900, working: 280, carry: 300, sleep: 1200, happy: 260,
};
const BLINK_MIN_GAP_MS = 4000;   // 眨眼最短间隔(低频,"在场"不是抽搐)
const BLINK_CHANCE = 0.3;

function drawFrame(ctx: CanvasRenderingContext2D, frame: string[], palette: Record<string, string>): void {
  ctx.clearRect(0, 0, WIDTH, HEIGHT);
  for (let y = 0; y < frame.length; y++) {
    const row = frame[y];
    for (let x = 0; x < row.length; x++) {
      const ch = row[x];
      if (ch === ".") continue;
      ctx.fillStyle = palette[ch] || "#f0f";
      ctx.fillRect(x, y, 1, 1);
    }
  }
}

export interface Pet {
  setState(s: string): boolean;
  state(): PetState;
  destroy(): void;
  /** 静态渲染一次(reduced-motion / 测试用)。 */
  render(): void;
}

export function createPet(opts: { canvas: HTMLCanvasElement; accent?: string }): Pet {
  const canvas = opts.canvas;
  canvas.width = WIDTH;
  canvas.height = HEIGHT;
  canvas.classList.add("pixelpet-canvas");
  // jsdom 无 2d context → 状态机照常工作,只是不画(smoke 靠这个跑)
  let ctx: CanvasRenderingContext2D | null = null;
  try { ctx = canvas.getContext("2d"); } catch { ctx = null; }
  if (ctx) ctx.imageSmoothingEnabled = false;
  const palette = buildPalette(opts.accent || KARVY_ACCENT);

  let state: PetState = "idle";
  let frameIdx = 0;
  let timer: ReturnType<typeof setInterval> | null = null;
  let lastBlink = 0;
  let destroyed = false;

  function reducedMotion(): boolean {
    try {
      return !!(window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches);
    } catch { return false; }
  }

  function currentFrame(): string[] {
    const frames = FRAMES[state];
    return frames[frameIdx % frames.length];
  }

  function render(frame?: string[]): void {
    if (ctx) drawFrame(ctx, frame || currentFrame(), palette);
  }

  function tick(): void {
    frameIdx = (frameIdx + 1) % FRAMES[state].length;
    // idle 的低频眨眼:插播 blink 一帧(下一 tick 自动回 idle 帧)
    if (state === "idle" && Date.now() - lastBlink > BLINK_MIN_GAP_MS && Math.random() < BLINK_CHANCE) {
      lastBlink = Date.now();
      render(FRAMES.blink[0]);
      return;
    }
    render();
  }

  function schedule(): void {
    if (timer !== null) { clearInterval(timer); timer = null; }
    if (reducedMotion()) { render(FRAMES[state][0]); return; }   // 降级:静止首帧,无轮换
    timer = setInterval(tick, TICK_MS[state]);
  }

  function setState(s: string): boolean {
    if (destroyed) return false;
    if ((STATES as readonly string[]).indexOf(s) < 0) return false;   // 未知状态 = 拒绝,不瞎演
    if (s === state) return true;
    state = s as PetState;
    frameIdx = 0;
    render();
    schedule();
    return true;
  }

  render();
  schedule();

  return {
    setState,
    state: () => state,
    render: () => render(),
    destroy: () => {
      destroyed = true;
      if (timer !== null) { clearInterval(timer); timer = null; }
    },
  };
}

const KarvyPixelPet = {
  createPet, validateFrames, buildPalette, colorForRole,
  STATES, FRAMES, WIDTH, HEIGHT, KARVY_ACCENT,
};
if (typeof window !== "undefined") {
  (window as unknown as { KarvyPixelPet: typeof KarvyPixelPet }).KarvyPixelPet = KarvyPixelPet;
}

export default KarvyPixelPet;
