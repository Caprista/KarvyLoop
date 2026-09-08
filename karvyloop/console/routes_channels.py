"""本机渠道配置 API：安全读写 config.yaml，并热更新通道运行时。"""
from __future__ import annotations

import os
import tempfile
import threading
import uuid
from pathlib import Path
from typing import Any

import yaml
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from karvyloop.config_channels import load_dingtalk_channel_configs

router = APIRouter(prefix="/api/channels")
_LOCK = threading.RLock()


class DingTalkConfigInput(BaseModel):
    name: str = Field(default="", max_length=128)
    client_id: str = Field(..., min_length=1, max_length=256)
    client_secret: str | None = Field(default=None, max_length=4096)
    role: str = Field(..., min_length=1, max_length=256)
    domain_id: str = Field(default="", max_length=256)
    allow_senders: list[str] = Field(default_factory=list, max_length=1000)
    enabled: bool = True


def _config_path(app: Any) -> Path:
    raw = getattr(app.state, "config_path", "") or ""
    if not raw:
        raise HTTPException(status_code=503, detail="console 未接 config.yaml")
    return Path(raw).expanduser()


def _load(path: Path) -> dict:
    if not path.exists():
        return {}
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except Exception as exc:
        raise HTTPException(status_code=409, detail="config.yaml 无法安全解析") from exc
    if not isinstance(data, dict):
        raise HTTPException(status_code=409, detail="config.yaml 根节点必须是映射")
    return data


def _items(data: dict) -> list[dict]:
    channels = data.setdefault("channels", {})
    if not isinstance(channels, dict):
        raise HTTPException(status_code=409, detail="channels 必须是映射")
    raw = channels.get("dingtalk")
    if raw is None:
        return []
    values = raw if isinstance(raw, list) else [raw]
    if not all(isinstance(x, dict) for x in values):
        raise HTTPException(status_code=409, detail="channels.dingtalk 格式错误")
    return [dict(x) for x in values]


def _ensure_ids(items: list[dict]) -> bool:
    changed = False
    seen: set[str] = set()
    for item in items:
        ident = str(item.get("id") or item.get("instance_id") or "").strip()
        if not ident or ident in seen:
            ident = uuid.uuid4().hex
        if item.get("id") != ident or "instance_id" in item:
            item["id"] = ident
            item.pop("instance_id", None)
            changed = True
        seen.add(ident)
    return changed


def _save(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    content = yaml.safe_dump(data, allow_unicode=True, sort_keys=False)
    fd, tmp_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        try:
            os.chmod(tmp_name, 0o600)
        except OSError:
            pass
        os.replace(tmp_name, path)
    finally:
        try:
            os.unlink(tmp_name)
        except FileNotFoundError:
            pass


def _public(item: dict) -> dict:
    return {"id": str(item.get("id") or item.get("instance_id") or ""),
            "name": str(item.get("name") or ""),
            "client_id": str(item.get("client_id") or ""),
            "has_client_secret": bool(item.get("client_secret")),
            "role": str(item.get("role") or ""),
            "domain_id": str(item.get("domain_id") or ""),
            "allow_senders": [str(x) for x in (item.get("allow_senders") or [])],
            "enabled": bool(item.get("enabled"))}


def _body(req: DingTalkConfigInput, ident: str, old: dict | None = None) -> dict:
    client_id = req.client_id.strip()
    role = req.role.strip()
    if not client_id:
        raise HTTPException(status_code=422, detail="client_id 必填")
    if not role:
        raise HTTPException(status_code=422, detail="role 必填")
    secret = req.client_secret
    if secret is None and old is not None:
        secret = str(old.get("client_secret") or "")
    if not secret or not secret.strip():
        raise HTTPException(status_code=422, detail="client_secret 必填")
    senders = list(dict.fromkeys(s.strip() for s in req.allow_senders if s.strip()))
    return {"id": ident, "enabled": req.enabled, "name": req.name.strip(),
            "client_id": client_id, "client_secret": secret,
            "role": role, "domain_id": req.domain_id.strip(),
            "allow_senders": senders}


def _reconcile(app: Any, path: Path) -> dict:
    from karvyloop.channels.dingtalk_runtime import reconcile_dingtalk_channels
    return reconcile_dingtalk_channels(app, load_dingtalk_channel_configs(path))


@router.get("/dingtalk")
def list_dingtalk(request: Request) -> dict:
    path = _config_path(request.app)
    with _LOCK:
        data = _load(path)
        items = _items(data)
        if _ensure_ids(items):
            data["channels"]["dingtalk"] = items
            _save(path, data)
    pending = getattr(request.app.state, "pending_channel_senders", None)
    return {"instances": [_public(x) for x in items],
            "pending_senders": pending.list() if pending is not None else []}


@router.post("/dingtalk", status_code=201)
def create_dingtalk(req: DingTalkConfigInput, request: Request) -> dict:
    path = _config_path(request.app)
    with _LOCK:
        data = _load(path)
        items = _items(data)
        _ensure_ids(items)
        item = _body(req, uuid.uuid4().hex)
        items.append(item)
        data["channels"]["dingtalk"] = items
        _save(path, data)
    return {"instance": _public(item), "runtime": _reconcile(request.app, path)}


@router.put("/dingtalk/{instance_id}")
def update_dingtalk(instance_id: str, req: DingTalkConfigInput, request: Request) -> dict:
    path = _config_path(request.app)
    with _LOCK:
        data = _load(path)
        items = _items(data)
        _ensure_ids(items)
        index = next((i for i, x in enumerate(items) if x.get("id") == instance_id), None)
        if index is None:
            raise HTTPException(status_code=404, detail="渠道实例不存在")
        item = _body(req, instance_id, items[index])
        items[index] = item
        data["channels"]["dingtalk"] = items
        _save(path, data)
    return {"instance": _public(item), "runtime": _reconcile(request.app, path)}


@router.delete("/dingtalk/{instance_id}")
def delete_dingtalk(instance_id: str, request: Request) -> dict:
    path = _config_path(request.app)
    with _LOCK:
        data = _load(path)
        items = _items(data)
        _ensure_ids(items)
        kept = [x for x in items if x.get("id") != instance_id]
        if len(kept) == len(items):
            raise HTTPException(status_code=404, detail="渠道实例不存在")
        data["channels"]["dingtalk"] = kept
        _save(path, data)
    return {"ok": True, "runtime": _reconcile(request.app, path)}


@router.post("/dingtalk/{instance_id}/senders/{sender}/allow")
def allow_sender(instance_id: str, sender: str, request: Request) -> dict:
    sender = sender.strip()
    if not sender or len(sender) > 256:
        raise HTTPException(status_code=422, detail="sender 格式错误")
    path = _config_path(request.app)
    with _LOCK:
        data = _load(path)
        items = _items(data)
        _ensure_ids(items)
        item = next((x for x in items if x.get("id") == instance_id), None)
        if item is None:
            raise HTTPException(status_code=404, detail="渠道实例不存在")
        allowed = [str(x) for x in (item.get("allow_senders") or [])]
        if sender not in allowed:
            allowed.append(sender)
            item["allow_senders"] = allowed
            data["channels"]["dingtalk"] = items
            _save(path, data)
    pending = getattr(request.app.state, "pending_channel_senders", None)
    if pending is not None:
        pending.remove(instance_id, sender)
    return {"ok": True, "runtime": _reconcile(request.app, path)}
