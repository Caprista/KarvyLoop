"""platform.linux — Linux 适配器（bubblewrap + Landlock 深度防御）。

  bubblewrap.py  BubblewrapSandbox —— mount namespace 隔离(--ro-bind/--bind + --unshare-net)。
  landlock.py    Landlock LSM 内核路径门(纯 syscall,免特权)——以前置 wrapper 叠在 bwrap 之上:
                 workspace 可写、系统 bin 只读、其余内核默认拒。旧核不支持 → 优雅降级纯 bwrap。
"""
