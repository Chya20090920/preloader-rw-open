# -*- coding: utf-8 -*-
"""GUI 侧的线协议副本：与 `relay\\protocol.py` 同一份契约。

受众: 代理(GUI 端)
视角: 对读者称「你」；本件不出现「我」
禁入: 元指令 · 过程注解 · 自注括号 · 对第三方的话

两边的 KINDS / EVENTS / INTENTS 与三种 envelope 的字段由
`tests\\test_protocol.py` 逐字比对；改动任一侧必须同步另一侧。

壳端另有 `encode_event`（把壳内部事件对象压成 evt），那是壳内部事务，
不在本件。
"""

import json

KIND_REQ = "req"
KIND_RES = "res"
KIND_EVT = "evt"
KINDS = (KIND_REQ, KIND_RES, KIND_EVT)

EV_STATE = "state"
EV_LOG = "log"
EV_HEALTH = "health"
EV_COMMAND = "command"
EVENTS = (EV_STATE, EV_LOG, EV_HEALTH, EV_COMMAND)

INTENTS = ("fire", "probe", "info", "health", "write", "read", "erase", "cancel")


def dumps(obj):
    """序列化成一行 JSON（不转义非 ASCII）。"""
    return json.dumps(obj, ensure_ascii=False)


def loads(line):
    """解析一行 JSON；非对象或非法 JSON 抛 ValueError。"""
    obj = json.loads(line)
    if not isinstance(obj, dict):
        raise ValueError("envelope 必须是 JSON 对象")
    return obj


def req_msg(rid, intent, args=None):
    return {"kind": KIND_REQ, "id": int(rid), "intent": str(intent),
            "args": dict(args or {})}


def res_msg(rid, ok, code="", detail=None):
    return {"kind": KIND_RES, "id": int(rid), "ok": bool(ok),
            "code": str(code), "detail": detail}


def evt_msg(event, data=None):
    return {"kind": KIND_EVT, "event": str(event), "data": dict(data or {})}
