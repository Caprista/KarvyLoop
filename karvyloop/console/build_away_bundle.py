"""build_away_bundle — karvy.chat 托管接入页的**可复现构建**(docs/43 P2 裁决:独立可信静态源)。

从**开源仓已提交的** static/{e2e,tunnel,i18n,away}.js + away.html 产出可上线的 away bundle:
- index.html:away.html 的 /static/ 绝对引用改相对 + 注入 **SRI integrity**(sha384;换包→浏览器拒载);
- 四个 .js 原样(二进制拷贝);
- MANIFEST.sha256:逐文件 sha256(部署可追溯——任何人对开源仓构建产物复核字节)。

**确定性**:输入全是已提交文件,输出只做字节级变换(LF 归一 + 二进制写,不吃平台换行),
所以"同一份源 → 同一份 bundle + 同一份 MANIFEST"可复现。CI 据此比对已提交的发布态 MANIFEST
(tests/test_away_bundle.py):committed static → 这个 bundle 是**机器守着**的,不再靠人工 clone 比对。

用法:`python -m karvyloop.console.build_away_bundle <out_dir>`(缺省 static/away-dist)。
部署侧(把 out_dir 传上 karvy.chat 静态托管、on-box sha256 -c 校验、软链切 current)不在本模块——
本模块只管"从源可复现地造出 bundle",不碰任何机器/密钥。
"""
from __future__ import annotations

import base64
import hashlib
import pathlib
import sys

_STATIC = pathlib.Path(__file__).resolve().parent / "static"
JS_FILES = ("e2e.js", "tunnel.js", "i18n.js", "away.js")
DEFAULT_OUT = _STATIC / "away-dist"


def _sri(data: bytes) -> str:
    return "sha384-" + base64.b64encode(hashlib.sha384(data).digest()).decode("ascii")


def build(out_dir: pathlib.Path | None = None, static_dir: pathlib.Path | None = None) -> dict:
    """构建到 out_dir;返回 {file: sha256} 清单。纯函数式:只读 static、只写 out_dir。"""
    static = static_dir or _STATIC
    out = out_dir or DEFAULT_OUT
    out.mkdir(parents=True, exist_ok=True)

    manifest: list[str] = []
    sri: dict[str, str] = {}
    for name in JS_FILES:
        # LF 归一(不是原样 read_bytes):git autocrlf 在 Windows 检出会把 .js 变 CRLF,
        # 原样哈希 → Windows 与 Linux 产出不同字节 = 构建不可复现(CI Windows 腿实捕)。
        # 统一 LF 后,哈希/SRI/落盘字节在三平台一致 —— 这才是"可复现构建"的地基。
        data = (static / name).read_bytes().replace(b"\r\n", b"\n")
        (out / name).write_bytes(data)
        sri[name] = _sri(data)
        manifest.append(f"{hashlib.sha256(data).hexdigest()}  {name}")

    html = (static / "away.html").read_text(encoding="utf-8")
    for name in JS_FILES:
        html = html.replace(
            f'<script src="/static/{name}"></script>',
            f'<script src="./{name}" integrity="{sri[name]}" crossorigin="anonymous"></script>')
    if html.count("integrity=") != len(JS_FILES):
        raise RuntimeError("SRI 注入不全 —— away.html 的 <script src> 形态变了?")
    if "/static/" in html:
        raise RuntimeError("仍有绝对 /static/ 引用未改写")
    # LF 归一 + 二进制写:Windows 文本模式会把 \n 转 \r\n → 写盘字节≠记账哈希(可复现构建的地基)。
    html_bytes = html.replace("\r\n", "\n").encode("utf-8")
    (out / "index.html").write_bytes(html_bytes)
    manifest.append(f"{hashlib.sha256(html_bytes).hexdigest()}  index.html")

    (out / "MANIFEST.sha256").write_bytes(("\n".join(manifest) + "\n").encode("utf-8"))
    return {line.split("  ", 1)[1]: line.split("  ", 1)[0] for line in manifest}


def main(argv: list[str]) -> int:
    out = pathlib.Path(argv[1]) if len(argv) > 1 else DEFAULT_OUT
    m = build(out)
    print(f"built away bundle -> {out}")
    for fname, h in m.items():
        print(f"  {h}  {fname}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
