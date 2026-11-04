# -*- coding: utf-8 -*-
"""日志脱敏：全工程唯一的帧与文本格式化器。

四类角色，由发送方声明；渲染规则固定：

    ctl      控制帧（XML 命令 / OK 令牌）——只出命令名、参数名、长度
    asset    资产帧（DA1/DA2、二层文件、分区镜像、续送块）——只出长度、类别、块序号
    devlog   设备日志——保留正文，长十六进制串 / 路径 / base64 块替换为占位符
    unknown  其余——≤16 B 出十六进制正文，超过只出长度

硬规则：
  · asset 在任何等级下都不输出载荷字节，包括可打印的 ASCII 认证文件；
  · ctl 也不输出 XML 的参数值，只输出参数名；
  · unknown 只出 ≤16 B 的十六进制，超过 16 B 不做任何内容猜测。
"""

import hashlib
import os
import re

# 一次性诊断开关：PANE_RAW_LOG=1 时设备方向帧原样输出（不分类、不脱敏、不抑制空行）。
# 仅用于定位，不得用于常规运行与交付；启动时读一次，运行期不变。
RAW_MODE = os.environ.get("PANE_RAW_LOG", "").strip().lower() in (
    "1", "true", "yes", "on")

CTL = "ctl"
ASSET = "asset"
DEVLOG = "devlog"
UNKNOWN = "unknown"

ROLES = (CTL, ASSET, DEVLOG, UNKNOWN)

MAX_DEVLOG = 200

_TOKEN = re.compile(rb"^OK(?:@0x[0-9A-Fa-f]{1,16})?\x00?$")
_TOKEN_BANG = re.compile(rb"^OK![\x20-\x7E]{0,16}\x00?$")
_TOKEN_ERR = re.compile(rb"^ERR\x00?$")
_CMD = re.compile(rb"<command>\s*([^<]{1,64}?)\s*</command>")
_ARG_BLOCK = re.compile(rb"<arg>(.*?)</arg>", re.S)
_ARG_TAG = re.compile(rb"<([A-Za-z0-9_\-]{1,32})>")

_DEVLOG_MARKERS = (
    b"@Protocol:", b"I'm alive", b"[PMIC]", b"initial_chksum_info",
    b"Download_data end", b"Erase [", b"Upload data to host file:",
    b"Readback total length", b"Process upload_data", b"Upload partition",
    b"Upload [",
)

# 设备日志帧带 4 字节日志前缀（实机 `\x01\x00\x00\x00`）。前缀不进正文，
# 否则 `***8.Enter bootstrap2***` 这类无标记行会被判 unknown、只剩长度。
_LOG_PREFIX = re.compile(rb"^[\x00-\x08\x0b\x0c\x0e-\x1f]{1,8}")

# 纯文本行：只含可打印 ASCII 与制表/换行；XML 载荷（上传内容）不算日志。
def _plain_text(body):
    if len(body) < 2 or body[:1] in (b"<", b"\x00"):
        return False
    if any(b < 0x20 and b not in (0x09, 0x0A, 0x0D) for b in body):
        return False
    return any(0x41 <= b <= 0x5A or 0x61 <= b <= 0x7A for b in body)


def _strip_log_prefix(payload):
    return _LOG_PREFIX.sub(b"", payload or b"", count=1)


_WIN_PATH = re.compile(r"[A-Za-z]:\\[^\s\"'<>]*")
_POSIX_PATH = re.compile(r"(?<![\w.])/(?:[^\s\"'<>/]+/)+[^\s\"'<>]*")
_HEX_RUN = re.compile(r"[0-9A-Fa-f]{16,}")
_B64_RUN = re.compile(r"[A-Za-z0-9+/]{48,}={0,2}")


def is_device_log(payload):
    """设备日志：日志前缀之后以换行开头、含已知标记，或整行是可打印文本。"""
    body = _strip_log_prefix(payload)
    if body.startswith((b"\r\n", b"\n")):
        return True
    if any(marker in body[:128] for marker in _DEVLOG_MARKERS):
        return True
    return _plain_text(body)


def classify_inbound(payload):
    """设备方向的帧分类。先排设备日志，再认协议形态，不猜内容。"""
    if is_device_log(payload):
        return DEVLOG
    if _TOKEN.match(payload) or _TOKEN_BANG.match(payload) or _TOKEN_ERR.match(payload):
        return CTL
    head = payload.lstrip()
    if head.startswith(b"<?xml") and b"CMD:" in payload:
        return CTL
    return UNKNOWN


def devlog_text(payload):
    """设备日志正文脱敏：去日志前缀、路径、长十六进制串、长 base64 块，限长。"""
    text = _strip_log_prefix(payload).decode("ascii", "ignore")
    text = text.replace("\r\n", " | ").replace("\r", " ").replace("\n", " | ")
    text = _WIN_PATH.sub("<path>", text)
    text = _POSIX_PATH.sub("<path>", text)
    text = _HEX_RUN.sub(lambda m: "<hex:%d>" % len(m.group(0)), text)
    text = _B64_RUN.sub(lambda m: "<blob:%d>" % len(m.group(0)), text)
    text = " ".join(text.split())
    if len(text) > MAX_DEVLOG:
        text = text[:MAX_DEVLOG] + "…"
    return text


def _ctl_detail(payload):
    if not payload:
        return "token=EMPTY"
    if _TOKEN.match(payload):
        return "token=%s" % payload.rstrip(b"\x00").decode("ascii", "ignore")
    if _TOKEN_BANG.match(payload):
        return "token=%s" % payload.rstrip(b"\x00").decode("ascii", "ignore")
    if _TOKEN_ERR.match(payload):
        return "token=ERR"
    command = _CMD.search(payload)
    if command:
        name = command.group(1).decode("ascii", "ignore").strip()
        args = []
        for block in _ARG_BLOCK.findall(payload):
            for tag in _ARG_TAG.findall(block):
                arg = tag.decode("ascii", "ignore")
                if arg not in args:
                    args.append(arg)
        detail = "cmd=%s" % name
        if args:
            detail += " args=[%s]" % ",".join(args[:8])
        return detail
    if b"<?xml" in payload[:16]:
        return "xml"
    return "opaque"


def frame_line(direction, seq, payload, role=UNKNOWN, label="",
               index=None, total=None):
    """一行帧摘要。任何分支都不含载荷字节。

    role=DEVLOG 且正文为空时返回 None（设备空日志行成片出现，不逐条打印）。
    """
    payload = payload or b""
    head = "%s #%d %s len=%d" % (direction, int(seq), role, len(payload))
    if role == ASSET:
        if label:
            head += " kind=%s" % label
        if index is not None:
            head += " idx=%d" % (int(index) + 1)
            if total:
                head += "/%d" % int(total)
        return head
    if role == CTL:
        return head + " " + _ctl_detail(payload)
    if role == DEVLOG:
        text = devlog_text(payload)
        if not text.strip(" |"):
            return None
        return head + " text=%r" % text
    hexs = unknown_hex(payload)
    if hexs:
        return head + " hex=" + hexs
    return head


def should_log_asset(index, total):
    """资产帧降频：前三块、每 20 块、最后一块。"""
    if index is None:
        return True
    if index < 3:
        return True
    if total and index + 1 >= int(total):
        return True
    return (int(index) + 1) % 20 == 0


MAX_UNKNOWN_HEX = 16


def unknown_hex(payload):
    """短 unknown 帧的十六进制正文；超过 MAX_UNKNOWN_HEX 不输出内容。"""
    raw = bytes(payload or b"")
    if not raw or len(raw) > MAX_UNKNOWN_HEX:
        return ""
    return " ".join("%02x" % b for b in raw)


def asset_identity(kind, data):
    """资产身份行：类别、长度、内容摘要（前 8 位）。不含内容。"""
    raw = bytes(data or b"")
    return "资产: kind=%s size=%d sha256=%s" % (
        kind, len(raw), hashlib.sha256(raw).hexdigest()[:8])


def mask_serial(value):
    """序列号只留末两位；长度不足 4 时全掩。"""
    text = str(value or "")
    if not text:
        return "(空)"
    if len(text) <= 4:
        return "*" * len(text)
    return "*" * (len(text) - 2) + text[-2:]


__all__ = [
    "CTL", "ASSET", "DEVLOG", "UNKNOWN", "ROLES", "MAX_UNKNOWN_HEX", "RAW_MODE",
    "classify_inbound", "is_device_log", "devlog_text", "frame_line",
    "should_log_asset", "unknown_hex", "asset_identity", "mask_serial",
]
