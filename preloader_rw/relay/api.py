# -*- coding: utf-8 -*-
"""对 GUI 的本地接口：JSON Lines 双向管道。

下行（GUI → 壳，stdin）：
    {"kind":"req","id":1,"intent":"fire"}
    {"kind":"req","id":2,"intent":"write","args":{"partition":"misc","image":"staging/misc_mini.bin"}}
    {"kind":"req","id":3,"intent":"read","args":{"partition":"misc","path":"out/misc.bin"}}
    {"kind":"req","id":4,"intent":"info"}
    {"kind":"req","id":5,"intent":"health"}

上行（壳 → GUI，stdout）：
    {"kind":"res","id":1,"ok":true,"code":"FIRED","detail":""}
    {"kind":"evt","event":"state","data":{"state":"gated","reason":"命令循环就绪","ts":1.0}}

stdout 只出 JSON；诊断文字走 stderr。
"""

import json
import queue
import threading
import sys

from . import events as E
from .protocol import KIND_REQ, encode_event, res_msg


def parse_intent(obj):
    """把一条 req envelope 翻成壳的意图对象；不认识返回 None。"""
    kind = str(obj.get("intent", "")).lower()
    args = obj.get("args")
    if not isinstance(args, dict):
        args = {}
    if kind == "fire":
        return E.Fire(da1=str(args.get("da1", "") or ""),
                      da2=str(args.get("da2", "") or ""),
                      payload=str(args.get("payload", "") or ""),
                      table=str(args.get("table", "") or ""))
    if kind == "provide":
        data = b""
        if args.get("hex"):
            data = bytes.fromhex(str(args["hex"]).strip())
        elif args.get("text") is not None:
            data = str(args["text"]).encode("utf-8")
        elif args.get("path"):
            with open(str(args["path"]), "rb") as fh:
                data = fh.read()
        return E.ProvideInput(req_id=int(args.get("id", 0)), data=data)
    if kind == "provide_cancel":
        return E.CancelInput(req_id=int(args.get("id", 0)))
    if kind == "probe":
        return E.ProbeDevice()
    if kind == "write":
        return E.WritePartition(str(args.get("partition", "")),
                                str(args.get("image", "")))
    if kind == "read":
        return E.ReadPartition(str(args.get("partition", "")),
                               str(args.get("path", "")))
    if kind == "erase":
        return E.ErasePartition(str(args.get("partition", "")))
    if kind == "info":
        return E.ReadInfo()
    if kind == "detect":
        return E.Detect()
    if kind == "health":
        return E.ProbeHealth()
    if kind == "cancel":
        return E.Cancel()
    return None


def handle(service, obj):
    """执行一条 req envelope；任何异常都不外抛，转成失败响应。"""
    rid = obj.get("id", 0)
    if obj.get("kind") not in (KIND_REQ, None):
        return res_msg(rid, False, "BAD_KIND", repr(obj.get("kind")))
    if not isinstance(rid, int):
        return res_msg(0, False, "BAD_ID", repr(rid))
    intent = parse_intent(obj)
    if intent is None:
        return res_msg(rid, False, "BAD_INTENT", str(obj.get("intent", "")))
    try:
        result = service.submit(intent)
    except Exception as exc:  # 壳内部异常不得掀掉管道
        return res_msg(rid, False, "FAILED", repr(exc))
    return res_msg(rid, result.ok, result.code, result.detail)


def serve_stdio(service, stdin=None, stdout=None):
    """跑双向管道：主线程读 stdin 派意图，另一线程把事件写到 stdout。

    读 stdin 结束（EOF）即返回；调用方负责 service.stop()。
    """
    stdin = stdin or sys.stdin
    stdout = stdout or sys.stdout
    out = queue.Queue()
    sentinel = object()

    def writer():
        while True:
            item = out.get()
            if item is sentinel:
                return
            stdout.write(json.dumps(item, ensure_ascii=False) + "\n")
            stdout.flush()

    service.subscribe(lambda event: out.put(encode_event(event)))
    worker = threading.Thread(target=writer, name="relay-stdout", daemon=True)
    worker.start()
    try:
        for line in stdin:
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
                if not isinstance(obj, dict):
                    raise ValueError("envelope 必须是 JSON 对象")
            except ValueError as exc:
                out.put(res_msg(0, False, "BAD_JSON", str(exc)))
                continue
            out.put(handle(service, obj))
    finally:
        out.put(sentinel)
        worker.join(timeout=2.0)
