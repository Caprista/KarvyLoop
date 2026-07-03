"""Phone-friendly decision surface (responsive pass) — static assertions.

Context: the console is reachable from a phone via the LAN token link
(`karvyloop url`), but the desktop 2×2 cockpit was unusable on a small screen.
The fix is an ADDITIVE `@media (max-width: 720px)` block in styles.css
(zero desktop regression by construction) — these tests lock its contract:

- index.html ships the viewport meta (without it no media query ever fires).
- styles.css has the 720px media query.
- Inside the block: the cockpit quadrant grid collapses to ONE column,
  the decision column is forced first (`order: -1`), the ACCEPT/DEFER/REJECT
  buttons and the edit-then-accept textarea are real touch targets (>=40px),
  and no fixed pixel width wider than a phone (>720px) sneaks in.

Q5-style: no JS engine, no browser — file reads + string/regex checks only.
Visual polish is verified by eyeball on a real phone; this locks the wiring.
"""
from __future__ import annotations

import re
from pathlib import Path

STATIC_DIR = Path(__file__).resolve().parents[1] / "karvyloop" / "console" / "static"
INDEX_HTML = STATIC_DIR / "index.html"
STYLES_CSS = STATIC_DIR / "styles.css"

MEDIA_QUERY = "@media (max-width: 720px)"


def _mobile_block() -> str:
    """Extract the body of the 720px media query (balanced-brace scan)."""
    css = STYLES_CSS.read_text(encoding="utf-8")
    start = css.find(MEDIA_QUERY)
    assert start != -1, f"styles.css missing `{MEDIA_QUERY}`"
    brace = css.find("{", start)
    depth = 0
    for i in range(brace, len(css)):
        if css[i] == "{":
            depth += 1
        elif css[i] == "}":
            depth -= 1
            if depth == 0:
                return css[brace + 1 : i]
    raise AssertionError("unbalanced braces in the mobile media query block")


def _rule(block: str, selector_re: str) -> str:
    """Return the (merged) declarations of every rule whose selector matches."""
    bodies = []
    for m in re.finditer(r"([^{}]+)\{([^{}]*)\}", block):
        if re.search(selector_re, m.group(1)):
            bodies.append(m.group(2))
    assert bodies, f"mobile block has no rule matching selector /{selector_re}/"
    return "\n".join(bodies)


# ---- viewport meta: without it, phones render at ~980px and no query fires --

def test_index_html_has_viewport_meta():
    html = INDEX_HTML.read_text(encoding="utf-8")
    m = re.search(r'<meta\s+name="viewport"\s+content="([^"]*)"', html)
    assert m, "index.html missing <meta name=viewport>"
    assert "width=device-width" in m.group(1)
    assert "initial-scale=1" in m.group(1)


# ---- the media query exists and is a single additive block ------------------

def test_styles_css_has_mobile_media_query():
    css = STYLES_CSS.read_text(encoding="utf-8")
    assert MEDIA_QUERY in css
    block = _mobile_block()
    assert block.strip(), "mobile media query block is empty"


# ---- decision-first single-column stacking ----------------------------------

def test_cockpit_grid_collapses_to_single_column():
    """The 2×2 quadrant grid (.cockpit-grid) becomes one column on phones."""
    body = _rule(_mobile_block(), r"\.cockpit-grid")
    assert re.search(r"grid-template-columns:\s*1fr\s*;", body), (
        ".cockpit-grid not collapsed to a single column in the mobile block"
    )


def test_decision_column_ordered_first():
    """.col-decide (h2a-list lives here) is forced to the top via CSS order."""
    body = _rule(_mobile_block(), r"\.col-decide")
    assert re.search(r"order:\s*-1\s*;", body), (
        ".col-decide missing `order: -1` — decisions must stack first on phones"
    )


def test_sidebar_demoted_below_cockpit():
    """Nav management entries stack BELOW the cockpit (steer first, manage second)."""
    block = _mobile_block()
    cockpit_order = re.search(r"\.cockpit\s*\{[^}]*order:\s*(\d+)", block)
    sidebar_order = re.search(r"\.sidebar\s*\{[^}]*order:\s*(\d+)", block)
    assert cockpit_order and sidebar_order, "mobile block missing .cockpit/.sidebar order rules"
    assert int(cockpit_order.group(1)) < int(sidebar_order.group(1))


# ---- touch targets on the H2A decision cards --------------------------------

def test_h2a_buttons_are_touch_targets():
    """ACCEPT/DEFER/REJECT ≥40px tall on phones (base rule keeps them a
    full-width flex row: each button is flex:1)."""
    body = _rule(_mobile_block(), r"\.h2a-buttons button")
    m = re.search(r"min-height:\s*(\d+)px", body)
    assert m, "h2a decision buttons missing a min-height touch-target rule"
    assert int(m.group(1)) >= 40, f"h2a button touch target {m.group(1)}px < 40px"


def test_h2a_edit_and_reason_areas_typeable():
    """Edit-then-accept textarea + optional reject-reason: ≥40px and ≥16px font
    (16px is the iOS threshold below which focusing an input force-zooms)."""
    block = _mobile_block()
    edit = _rule(block, r"\.h2a-edit-area")
    assert re.search(r"min-height:\s*(\d{2,})px", edit)
    assert int(re.search(r"min-height:\s*(\d+)px", edit).group(1)) >= 40
    assert re.search(r"font-size:\s*16px", edit), ".h2a-edit-area needs 16px font (iOS zoom)"
    reason = _rule(block, r"\.h2a-reason")
    assert int(re.search(r"min-height:\s*(\d+)px", reason).group(1)) >= 40


# ---- no desktop-sized fixed widths inside the mobile block -------------------

def test_no_fixed_width_wider_than_phone_in_mobile_block():
    """Nothing inside the 720px block may pin a fixed pixel width >720px
    (that would reintroduce the horizontal scroll the block exists to kill)."""
    block = _mobile_block()
    for prop, px in re.findall(r"(?:^|;|\{)\s*((?:min-|max-)?width)\s*:\s*(\d+(?:\.\d+)?)px", block):
        assert float(px) <= 720, f"mobile block sets `{prop}: {px}px` (> 720px viewport)"


# ---- additive-only: the block is appended, not spliced into desktop rules ----

def test_mobile_rules_live_only_inside_the_media_query():
    """The mobile-only order overrides must not leak outside the media query
    (desktop layout is grid — a stray `order` there would be dead-or-worse)."""
    css = STYLES_CSS.read_text(encoding="utf-8")
    before = css[: css.find(MEDIA_QUERY)]
    assert not re.search(r"\.col-decide\s*\{[^}]*\border\s*:", before), (
        "`order:` on .col-decide leaked outside the mobile media query"
    )
