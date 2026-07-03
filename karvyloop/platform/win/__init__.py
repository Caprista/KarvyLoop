"""platform/win — Windows 平台沙箱实现(PAL 子目录)。

分层(探测降级链,selector.py 的 win32 分支):
  Tier 3  restricted.py  RestrictedTokenSandbox —— 写隔离(WRITE_RESTRICTED token +
          白名单目录 ACL)+ Job Object 资源上限;网络门做不满 → 带 `net:` 的 token
          fail-closed 拒跑。
  Tier 4  degraded.py    DegradedWindowsSandbox —— 无 OS 级隔离的诚实兜底:
          第一方(agent 对 workspace 的 read/write/exec)直通;第三方技能脚本
          fail-closed 拒跑。

调试可用 env `KARVYLOOP_SANDBOX=restricted|degraded|stub` 强制指定(见 selector)。
"""
