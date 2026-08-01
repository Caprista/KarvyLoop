"""computer-use native MCP server —— 非 Wayland 平台(Windows / macOS / Linux-X11)的 plumbing.

docs/99:computer use = **OS-无关核心**(看图进模型 slice-A / 安全同意门+高危扣下 刀1 /
a11y-优先 persona slice-B / 模型无关 tool-use 环)+ **OS-相关 plumbing**(真去截屏+注入鼠标键盘)。
Linux/Wayland 的 plumbing 借 computer-use-linux(门户/ydotool 那套硬活);其余平台的输入/截屏
是薄标准活 → 这个自造的薄 MCP server 就够,**一份覆盖 Windows/macOS/Linux-X11**。

**工具名对齐 computer-use-linux**(screenshot/get_screen_size/move/click/type_text/press_key)→
核心的 persona/安全门(mcp_computer_use_* 前缀)/编排**一行不用改**,只是 preset 按 OS 换成
拉起这个 server。server 名 = computer_use → 工具即 mcp_computer_use_*(与 Linux 同,刀1 gate 认得)。

依赖(可选 extra `karvyloop[computer]`):截屏用 **Pillow**(ImageGrab,Windows/macOS 原生;
Linux 需 X11+scrot);输入用 **pyautogui**(Windows/macOS/Linux-X11)。缺了 fail-loud 指路装 extra。
**权限**:Windows 无需特批(UAC 提权窗截不到,如实);macOS 要在系统设置授**辅助功能 + 屏幕录制**。

安全:这只是 plumbing(手里的真枪),收口全在核心那层(会话同意门 + 高危弹卡 + 来源判定),
不在这里 —— 同 computer-use-linux 一视同仁。
"""
from __future__ import annotations

import io
import sys
from typing import Any

# 模块级 import:FastMCP 用 inspect.signature(eval_str=True) 求值工具的注解字符串(因
# `from __future__ import annotations`),`-> Image` 要在**模块 globals** 里找得到 Image。
from mcp.server.fastmcp import FastMCP, Image

_INSTALL_HINT = ("computer use 的输入/截屏依赖没装 —— `pip install \"karvyloop[computer]\"`"
                 "(Pillow + pyautogui)。macOS 还要在系统设置授辅助功能 + 屏幕录制。")


def _set_dpi_aware() -> None:
    """Windows:进程设 DPI-aware,让 ImageGrab 截的物理像素与 pyautogui 的坐标空间一致
    (否则 DPI 缩放 ≠100% 时截图尺寸和点击坐标对不上 —— 同 Linux 的 scale 坑)。其它平台 no-op。"""
    if sys.platform == "win32":
        try:
            import ctypes
            ctypes.windll.shcore.SetProcessDpiAwareness(2)  # PROCESS_PER_MONITOR_DPI_AWARE
        except Exception:
            try:
                ctypes.windll.user32.SetProcessDPIAware()
            except Exception:
                pass


# ---- 平台原语(纯函数,便于 mock 单测;真调 PIL/pyautogui 延迟 import,缺了 fail-loud)----

def grab_png() -> bytes:
    """截当前屏 → PNG bytes。Pillow ImageGrab(Windows/macOS 原生)。"""
    try:
        from PIL import ImageGrab
    except Exception as e:  # noqa: BLE001
        raise RuntimeError(_INSTALL_HINT) from e
    img = ImageGrab.grab()
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def screen_size() -> tuple[int, int]:
    try:
        from PIL import ImageGrab
    except Exception as e:  # noqa: BLE001
        raise RuntimeError(_INSTALL_HINT) from e
    img = ImageGrab.grab()
    return img.size  # (w, h) 物理像素(DPI-aware 后 = 点击坐标空间)


def _pyautogui():
    try:
        import pyautogui
    except Exception as e:  # noqa: BLE001
        raise RuntimeError(_INSTALL_HINT) from e
    pyautogui.FAILSAFE = False   # 别因鼠标撞角抛异常(agent 控场,失败靠上层 verify)
    return pyautogui


# pyautogui 的按键名映射(对齐常见叫法:super/cmd→win/command,Return→enter…)
_KEY_ALIASES = {"super": "win", "meta": "win", "cmd": "command", "return": "enter",
                "control": "ctrl", "option": "alt", "esc": "escape"}


def _norm_key(k: str) -> str:
    k = str(k or "").strip().lower()
    return _KEY_ALIASES.get(k, k)


def do_click(x: int, y: int, button: str = "left") -> dict:
    pg = _pyautogui()
    pg.click(x=int(x), y=int(y), button=str(button or "left"))
    return {"ok": True, "action": "click", "x": int(x), "y": int(y), "button": button}


def do_move(x: int, y: int) -> dict:
    pg = _pyautogui()
    pg.moveTo(int(x), int(y))
    return {"ok": True, "action": "move", "x": int(x), "y": int(y)}


def do_type(text: str) -> dict:
    pg = _pyautogui()
    pg.typewrite(str(text or ""), interval=0.01)
    return {"ok": True, "action": "type_text", "len": len(str(text or ""))}


def do_press(keys: str) -> dict:
    """单键或组合键:'enter' / 'ctrl+c' / 'super'。'+' 拆成 chord 走 hotkey。"""
    pg = _pyautogui()
    parts = [_norm_key(p) for p in str(keys or "").replace(" ", "").split("+") if p]
    if not parts:
        return {"ok": False, "action": "press_key", "error": "empty keys"}
    if len(parts) == 1:
        pg.press(parts[0])
    else:
        pg.hotkey(*parts)
    return {"ok": True, "action": "press_key", "keys": "+".join(parts)}


# ---- MCP server(FastMCP,stdio)---------------------------------------------------

def build_server():
    mcp = FastMCP("computer_use")

    @mcp.tool(description="Capture the current screen as a PNG image (your main way to see it).")
    def screenshot() -> Image:  # type: ignore[valid-type]
        return Image(data=grab_png(), format="png")

    @mcp.tool(description="Get the screen size in pixels {width,height} — the coordinate space for click/move.")
    def get_screen_size() -> dict:
        w, h = screen_size()
        return {"width": w, "height": h}

    @mcp.tool(description="Move the mouse to absolute pixel (x,y).")
    def move(x: int, y: int) -> dict:
        return do_move(x, y)

    @mcp.tool(description="Click at absolute pixel (x,y). button = left|right|middle.")
    def click(x: int, y: int, button: str = "left") -> dict:
        return do_click(x, y, button)

    @mcp.tool(description="Type literal text at the current focus.")
    def type_text(text: str) -> dict:
        return do_type(text)

    @mcp.tool(description="Press a key or chord: 'enter', 'ctrl+c', 'super' (opens Start/Spotlight).")
    def press_key(keys: str) -> dict:
        return do_press(keys)

    return mcp


def main(argv: Any = None) -> int:
    _set_dpi_aware()
    build_server().run("stdio")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
