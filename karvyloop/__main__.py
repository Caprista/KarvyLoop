"""`python -m karvyloop ...` 入口 —— 等价于 `karvyloop` 命令脚本。

为什么要它:一键升级重启 console 时,**不能**依赖 `sys.argv[0]`(若用 `python -m` 启动,它是 .py 路径、
不可直接 exec → 重启失败 = 把用户装坏)。统一走 `[sys.executable, "-m", "karvyloop", "console", ...]`,
sys.executable 永远是可执行的解释器,与启动方式无关,最稳。
"""
import sys

from karvyloop.cli.main import main

if __name__ == "__main__":
    sys.exit(main())
