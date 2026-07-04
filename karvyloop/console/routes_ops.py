"""routes_ops — /api/update* + /api/ops/* + /api/search/config + /api/doctor/fix 端点
(一键升级/回滚、自愈运维诊断、搜索 key 配置、doctor 确定性自愈)。

从 routes.py 纯搬移(P2-② routes god-module 拆分,零逻辑改动)。自带 APIRouter,
由 app.py include_router;符号在 routes.py re-export 保既有 import/monkeypatch 可达。

升级铁律:**绝不自动升级**——本文件的 /update/apply、/update/rollback 只在用户**点了**才被调,
且带 CSRF 头 + 本机/私网来源 + 并发锁三重门(见各端点)。
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Request
from pydantic import BaseModel, Field

router = APIRouter(prefix="/api")


def _acquire_upgrade_lock(lock) -> bool:
    """O_EXCL 原子建升级锁(D3/D6)。已存在且新鲜(<10min)→ False(拒,防双 runner);陈旧 → 接管。"""
    import os as _os
    import time as _t
    try:
        fd = _os.open(str(lock), _os.O_CREAT | _os.O_EXCL | _os.O_WRONLY, 0o644)
        _os.write(fd, str(_t.time()).encode())
        _os.close(fd)
        return True
    except FileExistsError:
        try:
            if _t.time() - lock.stat().st_mtime > 600:    # 崩溃残留的陈旧锁 → 接管
                lock.unlink()
                return _acquire_upgrade_lock(lock)
        except Exception:
            pass
        return False
    except Exception:
        return True    # 锁机制本身出问题不该挡升级(它是加固不是硬门)


def _is_trusted_upgrade_origin(host: str) -> bool:
    """升级触发的可信来源:**本机 + 私网/LAN**。

    local-first 主权:能从自家网络访问到这台 console 的就是机主(console 本就无鉴权——能开 UI 就能
    建角色/跑任务/读数据,单挡升级是 inconsistent);恶意跨源网页已被 CSRF 头(X-Karvyloop-Upgrade)挡掉。
    只挡**公网**来源(防 console 不慎裸暴公网被陌生人点升级)。host 是传输层对端,不可伪造。
    """
    import ipaddress
    h = (host or "").strip()
    if not h:
        return False
    try:
        ip = ipaddress.ip_address(h)
    except ValueError:
        return False
    mapped = getattr(ip, "ipv4_mapped", None)   # ::ffff:192.168.x.x → 取内嵌 IPv4 判
    if mapped is not None:
        ip = mapped
    return not ip.is_global   # 信任"非全球可路由"来源(本机/私网/LAN/链路本地);只挡真正的公网 IP


def _read_last_upgrade() -> dict:
    """读最近一次一键升级的结果(供重启后的前端显示成败);无 / 过旧(>10min)→ {}。"""
    import json as _json
    import time as _t
    from pathlib import Path as _P
    f = _P.home() / ".karvyloop" / "_upgrade_status.json"
    try:
        d = _json.loads(f.read_text(encoding="utf-8"))
        if isinstance(d, dict) and (_t.time() - float(d.get("ts", 0))) < 600:
            return d
    except Exception:
        pass
    return {}


@router.get("/update_status")
def api_update_status(request: Request) -> dict[str, Any]:
    """版本检测(只读,缓存一天,零遥测)。前端据此显**可关掉**的"有新版"横幅。

    升级铁律:**绝不自动升级** = 系统不自作主张;用户**点了**才升(/update/apply),但点完跑完整套。
    `last_upgrade`:最近一次一键升级的成败(重启后前端据此显示成功刷新 / 失败看日志)。
    """
    try:
        from karvyloop.update import check_update
        res = check_update()        # 缓存优先(不 force),网络不可达 → newer=False,不阻断 UI
    except Exception:
        from karvyloop.update import current_version
        res = {"current": current_version(), "latest": None, "newer": False,
               "command": "", "url": "", "checked": False, "source": "error"}
    res["last_upgrade"] = _read_last_upgrade()
    # 诚实 UX:能不能一键回到上一个已知好版本、那是哪个版本(preflight 记的 update_rollback.json)
    try:
        from karvyloop.update import rollback_status
        res.update(rollback_status())   # {"rollback_available": bool, "prev_version": str|None}
    except Exception:
        res.setdefault("rollback_available", False)
        res.setdefault("prev_version", None)
    return res


@router.post("/update/apply")
def api_update_apply(request: Request) -> dict[str, Any]:
    """一键升级(Hardy 2026-06-27:点了才升=手动,但点完跑完整套,不用敲命令)。

    **绝不自动升级**(本端点只在用户**点了**才被调);流程 = 写升级规格 → detached 拉起升级 runner
    → 1 秒后 console 自退(停服务)→ runner 停→装→起。安全:**只允许 localhost 触发**(自升级是控自己
    机器的事,防 LAN 上别人点)。装失败 best-effort 重启 + upgrade.log 留痕(见 upgrade_runner)。
    """
    import json as _json
    import os as _os
    import subprocess as _sp
    import sys as _sys
    import threading as _th
    import time as _time
    from pathlib import Path as _P

    # D5 防 CSRF:要求自定义头。跨源 fetch 带自定义头会触发 CORS preflight,本端点不处理 OPTIONS/CORS →
    # 被浏览器挡;**控制台界面**调用时显式带这个头。少了它 → 拒(挡掉恶意网页偷偷 POST 触发升级)。
    if (request.headers.get("x-karvyloop-upgrade") or "") != "1":
        return {"ok": False, "reason": "缺升级标记(防 CSRF);请从控制台界面点升级"}
    # 安全门:本机 + 私网/LAN 可触发(你常把 console 跑在一台机器、从局域网浏览器访问 —— 旧的"只本机"
    # 把这条正经用法静默挡了)。只挡公网来源;CSRF 头已挡恶意跨源网页。
    client = (request.client.host if request.client else "") or ""
    if not _is_trusted_upgrade_origin(client):
        return {"ok": False, "reason": f"升级只能从本机或同局域网触发(你的来源 {client} 不在可信网内;"
                                       f"若 console 暴露在公网请在本机 localhost 点,或手动 git pull)"}

    from karvyloop.update import check_update, detect_install_mode
    chk = check_update(force=True)
    if not chk.get("newer"):
        return {"ok": False, "reason": "已是最新,无需升级", "current": chk.get("current")}

    relaunch = getattr(request.app.state, "console_relaunch", None)
    if not relaunch or not relaunch.get("argv"):
        return {"ok": False, "reason": "无法确定如何重启(console_relaunch 未记)"}

    kl = _P.home() / ".karvyloop"
    kl.mkdir(parents=True, exist_ok=True)
    # D3/D6 并发锁:O_EXCL 原子建锁;已有且新鲜(<10min)→ 拒(防双击/双标签起两个 runner 抢端口、撞 pip)
    lock = kl / "_upgrade.lock"
    if not _acquire_upgrade_lock(lock):
        return {"ok": False, "reason": "升级已在进行中(稍候,或删 ~/.karvyloop/_upgrade.lock 重试)"}

    import karvyloop
    py = _sys.executable
    mode = detect_install_mode()
    if mode == "git":
        root = _P(karvyloop.__file__).resolve().parent
        for p in (root, *root.parents):
            if (p / ".git").exists():
                root = p
                break
        # D2:--ff-only 防冲突半完成树;--autostash 容忍脏工作区;失败由 rc 反映(runner 写状态)
        upgrade_cmd = f'git pull --ff-only --autostash && "{py}" -m pip install -e .'
        cwd = str(root)
    else:
        # karvyloop 未发布到 PyPI → `pip install -U karvyloop` 必失败。老实拒(别假装能升),并释放刚拿的锁。
        try:
            lock.unlink()
        except Exception:
            pass
        return {"ok": False, "reason": "当前不是 git 安装、karvyloop 也未发布到 PyPI;"
                                       "请用 git clone 部署后再一键升级,或手动更新。"}

    # 升级前置(动手前留后悔药):记回滚点(update_rollback.json)+ 备份实例状态(backups/,留 3 份)。
    # 失败 → 中止升级(没有回滚点就不动手,fail-loud);prev_commit 交给 runner 做装后自检失败的自动回滚。
    from karvyloop.update import preflight
    pf = preflight(str(chk.get("latest") or ""), root=_P(cwd))
    if not pf.get("ok"):
        try:
            lock.unlink()
        except Exception:
            pass
        return {"ok": False, "reason": f"升级前置检查失败,未动任何东西"
                                       f"({pf.get('stage', '?')}): {pf.get('reason', '')}"}

    spec = {"upgrade_cmd": upgrade_cmd, "cwd": cwd, "restart_argv": relaunch["argv"],
            "host": relaunch["host"], "port": relaunch["port"],
            "old_pid": _os.getpid(), "from": chk.get("current"), "to": chk.get("latest"),
            "python": py, "prev_commit": pf.get("prev_commit", ""), "kind": "upgrade"}
    (kl / "_upgrade.json").write_text(_json.dumps(spec, ensure_ascii=False), encoding="utf-8")
    # 升级开始 → 清掉上次的状态(避免前端读到旧的成败)
    try:
        (kl / "_upgrade_status.json").unlink()
    except Exception:
        pass

    try:
        _sp.Popen([py, "-m", "karvyloop.console.upgrade_runner"],
                  stdout=_sp.DEVNULL, stderr=_sp.DEVNULL,
                  start_new_session=(_os.name != "nt"),
                  creationflags=(_sp.DETACHED_PROCESS if _os.name == "nt" else 0))
    except Exception as e:  # noqa: BLE001
        try:
            lock.unlink()
        except Exception:
            pass
        return {"ok": False, "reason": f"启动升级器失败: {e}"}

    # 响应发出后 1 秒,console 自退 → 停服务,让 runner 装+起(detached 的 runner 不受影响)
    _th.Timer(1.0, lambda: _os._exit(0)).start()
    return {"ok": True, "started": True, "from": chk.get("current"), "to": chk.get("latest"),
            "log": str(kl / "upgrade.log")}


@router.post("/update/rollback")
def api_update_rollback(request: Request) -> dict[str, Any]:
    """一键回滚:回到 update_rollback.json 记的上一个已知好版本("每次更新都比之前更烂"的解药)。

    安全门与 /update/apply 完全同款(CSRF 头 + 本机/私网来源 + 并发锁);流程也同款 —— 写规格 →
    detached runner(停 → `git reset --hard <prev>` + `pip install -e .` → 起)。数据不动:升/降级
    从不触碰 ~/.karvyloop(preflight 备份只是迁移事故的后悔药)。回滚规格不带 prev_commit → runner
    不会对回滚再递归回滚。
    """
    import json as _json
    import os as _os
    import re as _re
    import subprocess as _sp
    import sys as _sys
    import threading as _th
    from pathlib import Path as _P

    if (request.headers.get("x-karvyloop-upgrade") or "") != "1":
        return {"ok": False, "reason": "缺升级标记(防 CSRF);请从控制台界面点回滚"}
    client = (request.client.host if request.client else "") or ""
    if not _is_trusted_upgrade_origin(client):
        return {"ok": False, "reason": f"回滚只能从本机或同局域网触发(你的来源 {client} 不在可信网内)"}

    from karvyloop.update import current_version, detect_install_mode, read_rollback_point
    if detect_install_mode() != "git":
        return {"ok": False, "reason": "当前不是 git 安装,无法 git 回滚"}
    info = read_rollback_point()
    if not info:
        return {"ok": False, "reason": "没有可用的回滚点(还没做过带 preflight 的升级)"}
    prev = str(info.get("prev_commit") or "")
    if not _re.fullmatch(r"[0-9a-fA-F]{7,64}", prev):   # 进 shell 命令,只认 commit hash 形态
        return {"ok": False, "reason": f"回滚点 commit 不合法: {prev[:40]!r}"}

    relaunch = getattr(request.app.state, "console_relaunch", None)
    if not relaunch or not relaunch.get("argv"):
        return {"ok": False, "reason": "无法确定如何重启(console_relaunch 未记)"}

    kl = _P.home() / ".karvyloop"
    kl.mkdir(parents=True, exist_ok=True)
    lock = kl / "_upgrade.lock"
    if not _acquire_upgrade_lock(lock):
        return {"ok": False, "reason": "升级/回滚已在进行中(稍候,或删 ~/.karvyloop/_upgrade.lock 重试)"}

    import karvyloop
    py = _sys.executable
    root = _P(karvyloop.__file__).resolve().parent
    for p in (root, *root.parents):
        if (p / ".git").exists():
            root = p
            break
    spec = {"upgrade_cmd": f'git reset --hard {prev} && "{py}" -m pip install -e .',
            "cwd": str(root), "restart_argv": relaunch["argv"],
            "host": relaunch["host"], "port": relaunch["port"], "old_pid": _os.getpid(),
            "from": current_version(), "to": info.get("prev_version") or prev[:12],
            "python": py, "kind": "rollback"}
    (kl / "_upgrade.json").write_text(_json.dumps(spec, ensure_ascii=False), encoding="utf-8")
    try:
        (kl / "_upgrade_status.json").unlink()
    except Exception:
        pass
    try:
        _sp.Popen([py, "-m", "karvyloop.console.upgrade_runner"],
                  stdout=_sp.DEVNULL, stderr=_sp.DEVNULL,
                  start_new_session=(_os.name != "nt"),
                  creationflags=(_sp.DETACHED_PROCESS if _os.name == "nt" else 0))
    except Exception as e:  # noqa: BLE001
        try:
            lock.unlink()
        except Exception:
            pass
        return {"ok": False, "reason": f"启动回滚器失败: {e}"}

    _th.Timer(1.0, lambda: _os._exit(0)).start()
    return {"ok": True, "started": True, "from": current_version(),
            "to": info.get("prev_version"), "log": str(kl / "upgrade.log")}


@router.get("/ops/diagnose")
async def api_ops_diagnose(request: Request) -> dict[str, Any]:
    """自愈运维 agent(L1):用**活着的** gateway 诊断 doctor 当前发现的真问题,人话说+提修法。

    诚实边界:接地于 doctor 真 findings;LLM **只诊断+提议、绝不执行**;无 gateway(模型挂)→
    退回确定性 doctor(那时 LLM 也帮不上,L0 顶)。bootstrap 悖论:这条本就该在系统活着时用。
    """
    from karvyloop.doctor import FAIL, WARN, run_doctor
    findings = run_doctor(check_port=False)
    problems = [f for f in findings if f.level in (FAIL, WARN)]
    if not problems:
        return {"ok": True, "healthy": True, "diagnosis": None}
    rk = getattr(request.app.state, "runtime_kwargs", None) or {}
    gw = rk.get("gateway")
    if gw is None:
        return {"ok": True, "healthy": False, "diagnosis": None, "reason": "no_model"}
    from karvyloop.i18n import t as _t
    from karvyloop.ops_agent import diagnose
    signal = "\n".join("- " + _t("doctor.msg." + f.code, **f.params) for f in problems)
    d = await diagnose(signal, gateway=gw, model_ref=rk.get("model_ref", ""))
    return {"ok": True, "healthy": False,
            "diagnosis": d.to_dict() if d.ok else None,
            "reason": "" if d.ok else "diagnose_failed"}


@router.post("/ops/propose_fix")
async def api_ops_propose_fix(request: Request) -> dict[str, Any]:
    """L1 自愈 slice3:把运维诊断**升成正式 H2A 决策卡**(不只读着看 / 不只 system_error)。

    信号 = doctor 真 findings **+ 可选的真实运行时报错**(body `{error, source}`)——比固定自检更丰富。
    诚实边界(承 ops_agent / doctor):卡是 unverifiable 诊断;register+broadcast 进 H2A 列由你拍;
    ACCEPT 只跑确定性可逆修复(handler 内),**LLM 文本绝不被执行**。无问题→不造卡;无模型→退回确定性。
    """
    app = request.app
    try:
        body = await request.json()
    except Exception:
        body = {}
    error = str((body or {}).get("error", "") or "").strip()
    source = str((body or {}).get("source", "") or "").strip()

    from karvyloop.doctor import AUTO_FIXABLE, FAIL, WARN, run_doctor
    from karvyloop.i18n import t as _t
    problems = [f for f in run_doctor(check_port=False) if f.level in (FAIL, WARN)]
    parts = ["- " + _t("doctor.msg." + f.code, **f.params) for f in problems]
    if error:
        parts.append(f"- 运行时报错({source or '未知来源'}):{error}")
    if not parts:
        return {"ok": True, "healthy": True, "proposal_id": ""}   # 没问题不造卡

    rk = getattr(app.state, "runtime_kwargs", None) or {}
    gw = rk.get("gateway")
    if gw is None:
        return {"ok": True, "healthy": False, "proposal_id": "", "reason": "no_model"}
    reg = getattr(app.state, "proposal_registry", None)
    if reg is None:
        return {"ok": False, "healthy": False, "proposal_id": "", "reason": "no_registry"}

    from karvyloop.ops_agent import diagnose
    d = await diagnose("\n".join(parts), gateway=gw, model_ref=rk.get("model_ref", ""))
    if not d.ok:
        return {"ok": True, "healthy": False, "proposal_id": "", "reason": "diagnose_failed"}

    import time as _time

    from karvyloop.console.proposals import broadcast_proposal
    from karvyloop.karvy.proposal_registry import proposal_for_ops_fix
    codes = [f.code for f in problems]
    auto_fixable = bool(codes) and any(c in AUTO_FIXABLE for c in codes) and d.risk == "reversible"
    # 幂等键:有 doctor 码用码集合;纯运行时报错用报错前缀(同错收敛成一张卡)
    key = ",".join(sorted(codes)) if codes else ("err:" + error[:120])
    prop = proposal_for_ops_fix(diagnosis=d.to_dict(), finding_codes=codes,
                                ts=_time.time(), auto_fixable=auto_fixable, key=key)
    reg.register(prop)
    try:
        await broadcast_proposal(app, prop)
    except Exception:
        pass
    return {"ok": True, "healthy": False, "proposal_id": prop.proposal_id,
            "auto_fixable": auto_fixable, "diagnosis": d.to_dict()}


# ---- /api/search/config(产品内配搜索:默认 keyless DuckDuckGo;可选填 Brave/Tavily key)----

class SearchConfigRequest(BaseModel):
    provider: str = Field(default="", max_length=32)   # "" / brave / tavily;空=清除回 keyless
    api_key: str = Field(default="", max_length=256)


@router.get("/search/config")
def api_search_config_get(request: Request) -> dict[str, Any]:
    """搜索配置公开态(不回传 key 明文):mode=keyless/keyed + provider + has_key + 可选 provider 列表。"""
    from karvyloop.coding.tools.web import get_search_config_public
    return {"ok": True, **get_search_config_public(), "providers": ["brave", "tavily"]}


@router.post("/search/config")
def api_search_config_set(req: SearchConfigRequest, request: Request) -> dict[str, Any]:
    """产品内保存搜索 key(写仓外 ~/.karvyloop/search.json,绝不进 repo)。
    provider/key 留空 = 清除 → 回 keyless。立即生效(清缓存)。"""
    from karvyloop.coding.tools.web import set_search_config
    return {"ok": True, **set_search_config(req.provider, req.api_key)}


# ---- /api/doctor/fix(doctor 确定性自愈的 UI 触发;auto 直接修,confirm 需二次确认)----

class DoctorFixRequest(BaseModel):
    confirm: bool = False   # True = 已二次确认,一并修 confirm 级危险项(重写 config)


@router.post("/doctor/fix")
def api_doctor_fix(req: DoctorFixRequest, request: Request) -> dict[str, Any]:
    """一键跑 doctor 确定性自愈(auto 直接修;confirm 级危险项需 body confirm=true 才修)。

    复用 cli/doctor_cmd.apply_health_fixes(= --fix 那批逻辑,不重写)。confirm=false 且有危险项 →
    不修、回 needs_confirm 让前端弹二次确认。永不含 key。config_path 取自 app.state。
    """
    from karvyloop.cli.doctor_cmd import apply_health_fixes
    cfg_path = getattr(request.app.state, "config_path", "") or None
    return apply_health_fixes(confirm=bool(req.confirm), config_path=cfg_path)
