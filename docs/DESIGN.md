# KarvyLoop — Design System

> The visual + interaction spec for the console and `/m`. One system, tokenized,
> theme-aware. Components consume tokens; never hardcode a color, radius, duration.
> Authority for craft rules: the anti-slop bans below are **refuse-and-rewrite**.

## Direction v3 (2026-07-14 — "green is the only anchor")

Hardy released every constraint except one: **green stays the identity; everything
else follows what looks best.** After two dark iterations both read as oppressive
(the root cause was darkness itself, not which dark), the call:

**Light-first "Morning Green" (晨绿)** — mint-tinted paper (`#F2F7F3`), white cards,
deep forest ink text (`#1C2B22`), one emerald action color (`#0E8A50`, deep enough
to carry white text). A workbench you read and decide in all day breathes in light.
The warm capybara pops naturally on a light field. Dark demotes to a one-click
night mirror — **moss night** (`#142420` → `#223B34` ladder, the airiest of the dark
candidates), never the near-black again. High-stakes marker: light uses deep
chartreuse `#7FA000` (fluorescent yellow fails on paper); dark keeps `#DFFF45`.
Semantic separations unchanged (success=teal, warning=amber, danger=rose,
green budget <10%, hairlines over glow). Full token tables live in `styles.css`
`:root` (light) and `[data-theme="dark"]` (moss) — styles.css is the source of
truth for values from v3 on; the v2 tables below are historical.

## Direction (2026-07-13, v2 — Hardy's four corrections applied) 〔superseded by v3〕

**Deep near-black + dark-emerald base (deeper, blue-shifted), one restrained
blue-green signal accent, hairline borders instead of glow.** The capybara mark
(小卡, chest ∞) is used **as-is** — no engineered warm zone, no halo; its natural
warmth doesn't fight the palette (Hardy: "影响你主色调吗?" — it doesn't). Boldness
is spent in exactly one place at a time (a decision card's arrival / the
high-stakes fluorescent marker), everything else is quiet.

Hardy's corrections (2026-07-13, binding):
1. **Pure red reads cheap (俗气) inside a green system** → danger is **rose** (玫红).
2. **Accent green shifts blue-ward** (蓝绿 / teal-ward), not yellow-green.
3. **The dark emerald goes deeper and slightly blue.**
4. **High-stakes marker: commit to fluorescent or omit** — muddy dark gold on a
   border reads 土气. Fluorescent yellow, hairline + badge only, tiny area. This is
   a deliberate, Hardy-directed exception to the no-neon rule, scoped to exactly
   this one semantic ("high-stakes / hits your hard standard") and nothing else.

### The one trap we must not fall into
Official design guidance names **"a near-black background with a single bright
acid-green accent"** as one of the three saturated AI-slop defaults. Our direction
is deliberately adjacent to it, so the escape is non-negotiable:
- Green is mostly **base** (a 4–5 step dark→emerald surface ladder), not a neon face.
- The accent green is **blue-green and low-chroma** (`#27D3A5`), never
  lime/chartreuse/neon (`#39FF14`/`#7FFF00`/`#ADFF2F`). The one sanctioned neon is
  the high-stakes fluorescent-yellow marker — scoped to that single semantic.
- Depth comes from **hairline borders (0.5–1px, low contrast)**, never green glow.
- The memorable thing is a **signature element elsewhere**, not the green light.
- Accent covers **< 10%** of any screen. No large green fields, no green glow.

## Layout philosophy — the workbench (Hardy 2026-07-13)

This is a **workbench, an AI-era Jarvis** — not a form app, not a dashboard of
equal-weight widgets. Two entries, one logic:

- **Web = the workbench.** The decision ("待你拍板") sits center-stage; the
  surrounding modules — who's working, scheduled runs, token burn, crystallizing
  skills, Karvy's observations — spread across the desk as the decision-maker's
  instrument surround. Chat floats bottom-right (the global Karvy button, his
  one home — never duplicate him elsewhere).
- **Mobile = the pocket AI entry.** Chat-first; the decision card hangs as a
  floating pill/chip (fluorescent marker when high-stakes), tap to decide,
  return to chat.
- **Tablet/foldables**: the same `@container`-query fluid components transition
  between the two shapes by container width — no third layout.

## Tokens

> Ranges given; exact hex is picked against live WCAG contrast (body text ≥ 4.5:1,
> large ≥ 3:1). Dark is the default theme; a light theme mirrors every token.

### Surface ladder (near-black → emerald, deeper + blue-shifted; black is a *substrate with depth*, not one flat plane)
```
--surface-0     #060B0B   /* app floor — near-black, faint blue-green cast */
--surface-1     #0A1414   /* panels */
--surface-2     #0E1C1A   /* cards (reads deep blue-emerald) */
--surface-3     #132522   /* raised / hover — one step up */
--surface-brand #07302A   /* hero / brand zone — deep emerald, blue-leaning */
--border-hair   #1B2B29   /* 0.5–1px hairline; replaces glow for depth */
--border-strong #223833   /* emphasis divider */
```

### Text (verify AA on the surface it sits on)
```
--text-1  #E5EDEB   /* primary — near-white, faintly cool; never pure #FFF */
--text-2  #93A7A2   /* muted sage-grey — secondary */
--text-3  #5F736D   /* labels / tertiary */
```

### Accent — one, spent sparingly (blue-green per Hardy)
```
--accent       #27D3A5   /* signal green, blue-green/teal-ward — NOT lime, NOT yellow-green */
--accent-hover #3EDDB3   /* +~8% L */
--accent-soft  rgba(39,211,165,0.13)   /* tint background / focus wash */
/* budget: accent < 10% of surface. No green glow, no large green fills. */
```

### Semantic — separated from the brand accent (accent can't be both brand and "check-mark green")
```
--success #52C46C   /* classic green — pulled AWAY from the teal accent (separation flipped
                       direction when the accent moved blue-ward) */
--warning #D9A441   /* amber — ordinary warnings only, never the high-stakes marker */
--danger  #E0487F   /* rose (玫红) — pure red reads cheap on a green system; small area only */
```

### High-stakes marker — fluorescent, committed (Hardy-directed exception)
```
--hv      #DFFF45              /* fluorescent yellow — hairline border + badge only */
--hv-soft rgba(223,255,69,0.10)
/* Scope: exactly one semantic — "high-stakes / hits your hard standard".
   Never appears anywhere else. Muddy dark gold on borders is banned (土气);
   fluorescent is commit-or-omit. Light theme uses a darker chartreuse (#8FA800). */
```

### Radius vocabulary (pick by role; not "everything 16px", not 0 = broadsheet slop)
```
--r-sm 6px    /* inputs / chips */
--r-md 10px   /* cards / buttons */
--r-lg 16px   /* panels / modals */
--r-full 999px /* pills / avatars */
/* HARD CEILING: cards ≤ 16px. 24/28/32px on a card is the AI tell. */
```

### Type (double-bind: anti-slop fonts AND bilingual en/zh)
- **Avoid**: Inter, Roboto, Open Sans, Lato, system defaults — and the "alternative
  default" Space Grotesk + Instrument Serif pairing.
- **Display/UI**: an engineering grotesk with character (e.g. a Söhne/Neue-Haas/Untitled
  class); **data/code**: a mono (Berkeley/Commit-class). **CJK is a hard requirement** —
  pair a weight-matched Chinese face (Source Han Sans / Noto Sans SC class).
- **Features**: display tight tracking (-0.01…-0.022em, floor -0.04em), body/UI low
  weights (400–560), heavy weight reserved for one signature moment;
  `font-feature-settings: "tnum"` on token counts / metrics + one stylistic set.

### Motion tokens (unified durations = a "real system" anti-slop signal)
```
--dur-fast  150ms   /* hover / toggle / press */
--dur-base  240ms   /* state entrance */
--dur-slow  320ms   /* the one orchestrated moment (decision card arrival) */
--ease-out  cubic-bezier(0.4, 0, 0.2, 1)   /* default entrance */
--ease-in   cubic-bezier(0.4, 0, 1, 1)     /* exits */
/* spring/overshoot ONLY on success/celebration. No bounce on UI chrome. */
```

## Motion rules
- Animate **only `transform` + `opacity`** (GPU). Never width/height/top/left/margin.
- Fade-in always carries a small rise: `opacity 0 + translateY(6–8px) → 1 + 0`.
- Motion binds to a **state change / cause** — it moves because something happened,
  never idle decoration. **No infinite pulses/glows, period** (the mascot is a
  static mark, not an animation slot).
- **The one orchestrated moment** = a decision card's arrival (H2A "your call"):
  ~320ms fade + rise + micro-scale 0.98→1, plus a **single** accent focus-ring pulse
  (once, never looping) to pull the eye. Boldness is spent here.
- `page-load` stagger: fade+rise, 30–50ms offset, cap ~6 items, first reveal only —
  never on every re-render. Reveals must enhance an **already-visible** default
  (never gate content visibility on a class transition — it ships blank on hidden tabs).
- **Reduced motion is mandatory**: every animation needs a
  `@media (prefers-reduced-motion: reduce)` fallback (crossfade or instant).
- innerHTML re-render breaks running animations + loses focus/scroll. Use the
  **View Transitions API** (`document.startViewTransition`, feature-detected) for
  view/panel swaps, and **morphdom (~3KB)** for keyed list/card regions so elements
  keep identity (enter/exit/FLIP survive, focus/scroll preserved). No framework.

## Absolute bans (refuse-and-rewrite — the AI-slop tells)
- Side-stripe borders (colored `border-left/right` > 1px as accent). Full borders / bg tint / leading icon instead.
- Gradient text (`background-clip:text` + gradient). Solid color; emphasis via weight/size.
- Glassmorphism as default. Rare + purposeful or nothing.
- Hero-metric template; identical icon+heading+text card grids.
- Tiny uppercase tracked eyebrow above every section; `01/02/03` numbered section markers as scaffolding.
- `1px border` + `box-shadow ≥16px blur` on the same element (ghost-card). Pick one.
- `border-radius ≥ 24px` on cards. Hand-drawn/sketchy SVG. `repeating-linear-gradient` stripe bg. Decorative CSS grid-line backgrounds.
- Emoji as UI icons where a real icon or text label belongs. **Green glow / neon-lime accents.**

## The AI-slop test (run before shipping a surface)
Could someone say "AI made that" without doubt? Two altitudes:
1. **First-order**: can you guess theme+palette from the category alone? Rework.
2. **Second-order**: can you guess it from category + anti-reference ("agent tool that's
   not warm-cream → black terminal-green")? That's our exact trap — the escape above is
   what takes us out of it. Verify with Playwright screenshots + deterministic CSS/DOM
   checks, not a subjective self-score.

## Rollout
Tokens first (this file → `:root` in styles.css, both themes) → one proof surface
(decision card, the product's heart) → sign-off on the concrete look → then the 12
panels + desktop + `/m`, one surface at a time, each screenshot-verified.
