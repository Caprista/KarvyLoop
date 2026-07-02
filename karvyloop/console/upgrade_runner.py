"""一键升级的 detached 执行器(被 /api/update/apply 拉起,脱离 console 进程跑)。

流程(Hardy 2026-06-27:"点一下跑完整套"):**停**(等旧 console 进程退出 + 端口释放)→ **装**(跑升级命令)
→ **起**(同参数重启 console)。读 `~/.karvyloop/_upgrade.json`,写 `~/.karvyloop/upgrade.log` +
`_upgrade_status.json`(供重启后的 console / 前端显示成败),完事删锁 `_upgrade.lock`。

不变量(独立对抗验收后加固):
- runner 启动即把自身代码载入内存 → 升级改文件不影响它跑完(单次读 spec)。
- 与 console **非父子绑定**(start_new_session/DETACHED_PROCESS)→ console os._exit 后照活。
- 装失败也 best-effort 重启(让用户有东西用);**但端口仍被占时不盲目重启**(否则新 console 撞"已在跑"
  直接退、反而什么都没起)→ 改为留旧 console + 醒目记失败状态(D4)。
"""
from __future__ import annotations

import errno
import json
import os
import socket
import subprocess
import sys
import time
from pathlib import Path

# 启动即载入内存(与本模块同一不变量):升级中途改盘上文件不影响本次跑完。
from karvyloop import update as _update

_KL = Path.home() / ".karvyloop"
_SPEC = _KL / "_upgrade.json"
_LOG = _KL / "upgrade.log"
_STATUS = _KL / "_upgrade_status.json"
_LOCK = _KL / "_upgrade.lock"


def _port_free(host: str, port: int) -> bool:
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    if os.name != "nt":
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    try:
        s.bind(("0.0.0.0" if host in ("", "::") else host, int(port)))
        return True
    except OSError:
        return False
    except Exception:
        return True
    finally:
        s.close()


def _wait_free(host: str, port: int, secs: float = 15.0) -> bool:
    for _ in range(int(secs * 2)):
        if _port_free(host, port):
            return True
        time.sleep(0.5)
    return False


def _pid_gone(pid) -> bool:
    """旧 console 进程是否已退出。判断不了(Windows / 无权限)→ 当 gone,交给端口检查兜底。"""
    if not pid or os.name == "nt":
        return True
    try:
        os.kill(int(pid), 0)
        return False                       # 还活着
    except OSError as e:
        return e.errno != errno.EPERM      # ESRCH=没了(True);EPERM=活着但没权限(False)
    except Exception:
        return True


def _write_status(d: dict) -> None:
    try:
        d["ts"] = time.time()
        _STATUS.write_text(json.dumps(d, ensure_ascii=False), encoding="utf-8")
    except Exception:
        pass


def main() -> int:
    try:
        spec = json.loads(_SPEC.read_text(encoding="utf-8"))
    except Exception:
        return 1
    _KL.mkdir(parents=True, exist_ok=True)
    log = open(_LOG, "a", encoding="utf-8")

    def L(m: str) -> None:
        log.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')} {m}\n")
        log.flush()

    host, port = spec.get("host", "127.0.0.1"), int(spec.get("port", 8766))
    frm, to, old_pid = spec.get("from", "?"), spec.get("to", "?"), spec.get("old_pid")
    L(f"=== 升级开始 {frm} → {to}  cmd={spec.get('upgrade_cmd')!r} (cwd={spec.get('cwd')}) ===")
    try:
        _SPEC.unlink()                      # 单次用,读完即删(防陈旧误读)
    except Exception:
        pass

    try:
        # 1) 停:等旧 console 进程退出 + 端口释放
        L("等旧 console 退出...")
        for _ in range(50):                 # PID 最多等 ~25s
            if _pid_gone(old_pid):
                break
            time.sleep(0.5)
        _wait_free(host, port, 15)

        # 2) 装
        try:
            rc = subprocess.call(spec["upgrade_cmd"], shell=True, cwd=spec.get("cwd") or None,
                                 stdout=log, stderr=log)
        except Exception as e:
            rc = -1
            L(f"升级命令异常: {e}")
        L(f"升级命令结束 rc={rc}")

        # 2.5) 验 + 必要时自动回滚(装出坏版本绝不静默带病重启:smoke 导入自检失败 / 装挂 →
        #      git reset --hard 回 preflight 记的已知好 commit + 重装;回滚 spec 不带 prev_commit,
        #      天然不会递归回滚)。全部经 karvyloop.update(启动时已载入内存)。
        verb = "回滚" if spec.get("kind") == "rollback" else "升级"
        py = spec.get("python") or sys.executable
        fin = _update.finalize_install(rc, spec.get("prev_commit") or "",
                                       root=spec.get("cwd") or None, python=py, log=L)
        ok_final, rolled_back, fail_reason = fin["ok"], fin["rolled_back"], fin["reason"]
        if ok_final:
            msg = ""
        elif rolled_back:
            # 诚实 UX:重启后前端读到的 msg 必须说清"回滚了 + 为什么"(fail-loud,不静默)
            msg = (f"{verb}到 {to} 失败({fail_reason});已自动回滚到 {frm} 并重启,"
                   f"你的数据没动。详见 upgrade.log")
        else:
            msg = f"{verb}失败且未能自动回滚({fail_reason});已用当前盘上版本尽力重启,详见 upgrade.log"

        # 3) 起:端口空了才重启(否则盲目重启会撞"已在跑"直接退,反而没起)
        if _wait_free(host, port, 12):
            try:
                subprocess.Popen(spec["restart_argv"], stdout=log, stderr=log,
                                 start_new_session=(os.name != "nt"),
                                 creationflags=(subprocess.DETACHED_PROCESS if os.name == "nt" else 0))
                L(f"已重启 console: {' '.join(spec['restart_argv'])}")
                _write_status({"ok": ok_final, "rc": rc, "from": frm, "to": to, "restarted": True,
                               "rolled_back": rolled_back, "rollback_reason": fail_reason,
                               "msg": msg})
            except Exception as e:
                L(f"⚠ 重启失败: {e} —— 请手动 `karvyloop console`")
                _write_status({"ok": False, "rc": rc, "from": frm, "to": to, "restarted": False,
                               "rolled_back": rolled_back, "rollback_reason": fail_reason,
                               "msg": (f"{msg};" if msg else "") + f"重启失败: {e};请手动 karvyloop console"})
        else:
            L("⚠ 端口仍被占,未重启(旧 console 可能没退干净)。请手动停掉它再 `karvyloop console`")
            _write_status({"ok": False, "rc": rc, "from": frm, "to": to, "restarted": False,
                           "rolled_back": rolled_back, "rollback_reason": fail_reason,
                           "msg": (f"{msg};" if msg else "") + "端口仍被占,未重启;请手动停掉旧 console 再启动"})
        L("=== 升级结束 ===")
    finally:
        log.close()
        try:
            _LOCK.unlink()                  # 释放并发锁
        except Exception:
            pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
