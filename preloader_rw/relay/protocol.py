# -*- coding: utf-8 -*-
"""GUI 与壳之间的线协议：JSON Lines。

受众: 代理(壳端 / GUI 端)
视角: 对读者称「你」；本件不出现「我」
禁入: 元指令 · 过程注解 · 自注括号 · 对第三方的话

三族 envelope，一行一个 JSON，UTF-8：

    req  {"kind":"req","id":1,"intent":"write","args":{"partition":"misc"}}
    res  {"kind":"res","id":1,"ok":true,"code":"OK","detail":<任意 JSON>}
    evt  {"kind":"evt","event":"state","data":{"state":"gated","ts":1.0}}

不变量：
    · stdout 只出这三族 JSON；诊断文字走 stderr。
    · 意图必有整数 id；响应必回同一个 id。
    · state 覆盖式（后到覆盖先到）；log / command 追加式。
    · 不造单独 ping：等待期的存活信号由内核每 5 秒一条 log 事件承载。

本件只依赖标准库。壳端持本件；GUI 端 `client\\protocol.py` 是同一契约的
副本，一致性由 `tests\\test_protocol.py` 的双向比对断言守住。
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
EV_HOST_INPUT = "host_input"
EVENTS = (EV_STATE, EV_LOG, EV_HEALTH, EV_COMMAND, EV_HOST_INPUT)

INTENTS = ("fire", "probe", "info", "health", "write", "read", "erase", "cancel",
           "provide", "provide_cancel", "detect")


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


def encode_event(event):
    """把壳的事件对象压成 evt envelope；未知类型退化为 log。"""
    from . import events as E

    ts = float(getattr(event, "ts", 0.0) or 0.0)
    if isinstance(event, E.StateEvent):
        return evt_msg(EV_STATE, {"state": event.state,
                                  "reason": event.reason, "ts": ts})
    if isinstance(event, E.HealthReport):
        return evt_msg(EV_HEALTH, {"source": event.source,
                                   "values": event.values, "ts": ts})
    if isinstance(event, E.CommandEvent):
        return evt_msg(EV_COMMAND, {"name": event.name, "ok": event.ok,
                                    "state": event.state, "failure": event.failure,
                                    "rounds": event.rounds, "detail": event.detail,
                                    "ts": ts})
    if isinstance(event, E.HostInput):
        return evt_msg(EV_HOST_INPUT, {"id": event.req_id, "input": event.kind,
                                       "info": event.info,
                                       "timeout_s": event.timeout_s, "ts": ts})
    if isinstance(event, E.LogEvent):
        return evt_msg(EV_LOG, {"level": event.level, "text": event.text,
                                "source": event.source, "ts": ts})
    return evt_msg(EV_LOG, {"level": "info", "text": repr(event),
                            "source": "shell", "ts": ts})
