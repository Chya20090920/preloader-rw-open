# -*- coding: utf-8 -*-
"""默认载荷：官方工作流（DA1 → DA2 → 签名认证 → 移交命令循环）。

载荷 = 工作流。本文件是唯一决定「发什么、按什么顺序发」的地方；
内核只提供原语（握手/帧收发/上传供数/提交命令/等待与收讫/二阶段循环）。
换一份载荷 = 换这个文件（`PANE_PAYLOAD` 指向新文件），内核一行不改。

按官方下载流程推进：

  DA1 上传完成（内核 transport 段完成，EP0/握手/探针/DA1 不在载荷内）
  → DA1 阶段前置命令
  → BOOT-TO ⇒ DOWNLOAD-FILE(info=2nd-DA) ⇒ 供数（api.serve_da2）
  → DA2 阶段前置命令（含 SECURITY-GET-DEV-FW-INFO 上传应答）
  → SECURITY-SET-FLASH-POLICY + DOWNLOAD-FILE(SEC-POLICY) ⇒ 供签名凭证
  → GET-HW-INFO（注册后信息读取）
  → READ-FLASH 读 GPT（分区表落盘 downloads/gpt.bin，供壳解析）
  → enter_loop（移交：此后由壳经内核的二阶段命令循环投递意图）

设备端始终是官方 DA2；认证只有提交服务器签名这一条路，没有第二种。
本载荷只调用内核公开 API（见 `preloader_rw/core/kernel/api.py` 的 KernelAPI）。
"""

import os as _os

from preloader_rw.core.engine import acks, timing
from preloader_rw.core.engine.inbound import EventKind
from preloader_rw.core.proto import xml

# ---- 本工作流的落盘位置（设备侧 target_file 是 MEM://，不能直接当路径）----
FW_INFO_PATH = "downloads/fw_info.bin"
HW_INFO_PATH = "downloads/hw_info.bin"
SYS_PROP_PATH = "downloads/sys_prop.bin"
GPT_PATH = "downloads/gpt.bin"

# ---- DA1 前置命令的属性键（官方原值） ----
SYS_PROP_KEY = "DA.SLA"

# ---- 命令目标地址 ----
SYS_PROP_TARGET = 0x0EE73040     # GET-SYS-PROPERTY(DA.SLA)
SYS_PROP_LEN = 0x1000
SPEED_TARGET = 0x0096E9F0        # CAN-HIGHER-USB-SPEED
SPEED_TARGET_LEN = 0x40
FW_INFO_TARGET = 0x1A637020      # SECURITY-GET-DEV-FW-INFO
FW_INFO_LEN = 0x200000
HW_INFO_TARGET = 0x1A63A020      # GET-HW-INFO
HW_INFO_LEN = 0x200000
SEC_POLICY_SOURCE = 0x16B2C030   # SECURITY-SET-FLASH-POLICY
SEC_POLICY_LEN = 0x200             # 凭证长度（512 B 十六进制文本）

# ---- GPT 直读参数（官方 READ-FLASH 读元数据的形态） ----
GPT_LABEL = "UFS-LUA2"           # 存储 LUN 标签（READ-FLASH 的 partition 字段）
GPT_OFFSET = 0
GPT_LENGTH = 0x10000             # 64 KiB：GPT 头 + 条目区
GPT_TARGET = "gpt"

# 回合失败标记：设备在本轮里打印这些文本即判失败（认证未通过 / 安全拒绝）。
# 命中即中止——"Server is not authenticated. Locked." 是设备侧明确说
# 「这一轮没把认证打开」，不看就等于把失败当成功往下走。
ROUND_FAIL_MARKERS = ("not authenticated", "security deny", "locked.")


class RoundRejected(IOError):
    """回合被设备明确拒绝（END 非 OK / 日志报未认证 / 缺回合尾）。

    区别于传输失败（设备静默、设备消失）：拒绝意味着设备收到了主机
    提交的内容并作了表态；静默/消失则什么都没表态。
    """


class AuthRejected(RoundRejected):
    """签名认证轮被设备拒绝：验签未通过或设备明确报未认证。"""


def _round_failure(reason):
    """回合失败原因串：必须能直接看出是「没有结果」还是「设备说不行」。"""
    text = (reason or "").strip()
    if not text:
        return "回合尾 CMD:END 缺 result（设备没表态）"
    return "设备在回合尾给出的结果是 %r（非 OK）" % (text,)


def _ack(api):
    api.reply(xml.OK)


def _pump(api, on_download=None, on_upload=None, on_file_sys=None):
    """推进本轮直到 START；每个状态帧按官方回合的顺序回 OK。

    活性规则：设备心跳约一个 DIALOG_MS 一帧，静默累计超过 LIVENESS_MS
    即判会话结束——不是「预算」，是心跳缺席。

    裁决规则：**本轮必须有明确结果**。
      · 收到 `CMD:END result=OK` → 本轮成立；
      · 收到 END 但 result 非 OK（含 `ERR` / `Security deny` /
        `Server is not authenticated` 一类文本）→ 判失败并中止；
      · 收到申报长度的全部数据但始终没有 END → 不算「软成功」：
        数据完整只说明设备把字节吐完了，不说明回合成功。
    抛出即中止：本函数是注册期回合的唯一推进者，异常一路上传成注册失败，
    绝不在失败后继续发下一条命令。
    """
    wait_ms = int(_os.environ.get("PANE_DIALOG_MS") or timing.DIALOG_MS)
    upload_done = False
    silent_ms = 0
    while True:
        event = api.wait_event(wait_ms)
        kind = event.kind

        if kind is EventKind.TIMEOUT:
            silent_ms += wait_ms
            if silent_ms < timing.LIVENESS_MS:
                continue
            if upload_done:
                raise RoundRejected(
                    "回合被设备拒绝：申报长度已收满，但 %d ms 内无 CMD:END"
                    " ⇒ 设备状态未知（不得按成功处理）" % wait_ms)
            raise IOError("设备静默 %d ms（会话已死）" % silent_ms)

        # 收到任何一帧（含 devlog 心跳）都算活性：清空累计空窗。
        silent_ms = 0

        if kind is EventKind.LOG:
            text = getattr(event, "text", "") or ""
            lowered = text.lower()
            for marker in ROUND_FAIL_MARKERS:
                if marker in lowered:
                    raise RoundRejected(
                        "回合被设备拒绝：设备日志报告 %r（命中标记 %r）"
                        % (text[:160], marker))
            continue
        # 跨轮通用收讫：唯一规则见 engine.acks（内核原语）。
        reply = acks.generic_reply(kind.value, event.raw, xml.OK)
        if kind in (EventKind.OK, EventKind.NOTICE):
            if reply:
                api.reply(reply)
            continue
        if kind is EventKind.END:
            result = (getattr(event, "result", "") or "").strip()
            api.log("info", "END result=%r raw=%r"
                    % (result, (event.raw or b"")[:200]))
            _ack(api)              # 当轮收讫归当轮处理者：注册期即本载荷
            if result.upper() != "OK":
                raise RoundRejected("回合被设备拒绝：%s" % _round_failure(result))
            continue
        if kind is EventKind.START:
            api.reply(reply)
            return
        if kind is EventKind.UPLOAD_FILE:
            _ack(api)
            if on_upload is None:
                raise IOError("未预期的上传请求: %s" % (event.target_file,))
            on_upload(api, event)
            upload_done = True
            continue
        if kind is EventKind.FILE_SYS_OP:
            if on_file_sys is None:
                raise IOError("未预期的文件查询: %s" % (event.key,))
            on_file_sys(api, event)
            continue
        if kind is EventKind.DOWNLOAD_FILE:
            if on_download is None:
                raise IOError("未预期的下载请求: %s" % (event.info,))
            on_download(api, event)
            continue
        if kind is EventKind.DEV_GONE:
            raise IOError("设备消失")
        raise IOError("未知事件: %r" % (kind,))


def _await_start(api):
    """等一个 CMD:START 并应答；DA1 上传后与 DA2 启动后各一次。"""
    deadline_ms = timing.DEV_INIT_MS
    while True:
        event = api.wait_event(deadline_ms)
        kind = event.kind
        if kind is EventKind.START:
            _ack(api)
            return
        if kind is EventKind.TIMEOUT:
            raise IOError("等 START 失败：设备无帧")
        deadline_ms = timing.DIALOG_MS
        reply = acks.generic_reply(kind.value, event.raw, xml.OK)
        if kind in (EventKind.OK, EventKind.NOTICE):
            if reply:
                api.reply(reply)
            continue
        if kind is EventKind.END:
            _ack(api)
            continue
        if kind is EventKind.LOG:
            continue
        raise IOError("等 START 失败: %r" % (kind,))


def _da1_preamble(api):
    """DA1 上传后、BOOT-TO 之前的五条前置命令（官方顺序与参数）。

    `da_log_level` 可用 `PANE_RUNTIME_LOG_LEVEL` 覆盖（默认 `INFO`，与官方一致）；
    `log_channel` 默认 UART（官方用 USB，但 USB 日志与协议抢同一条 bulk IN，
    会把协议挤死），`PANE_RUNTIME_LOG_CHANNEL=USB` 取回官方值。
    """
    level = (_os.environ.get("PANE_RUNTIME_LOG_LEVEL") or "INFO").strip().upper() or "INFO"
    channel = (_os.environ.get("PANE_RUNTIME_LOG_CHANNEL") or "UART").strip().upper() or "UART"
    api.log("info", "SET-RUNTIME da_log_level=%s log_channel=%s" % (level, channel))
    api.command("SET-RUNTIME-PARAMETER",
                {"da_log_level": level, "log_channel": channel})
    api.command("HOST-SUPPORTED-COMMANDS")
    api.command("NOTIFY-INIT-HW")
    api.command("SET-HOST-INFO")
    api.command("GET-SYS-PROPERTY", {
        "key": SYS_PROP_KEY,
        "target_file": xml.mem_source(SYS_PROP_TARGET, SYS_PROP_LEN),
    }, save_path=SYS_PROP_PATH)


def _signature_round(api, policy_frame):
    """签名认证回合：SECURITY-SET-FLASH-POLICY + 实时索取并供出签名应答。

    设备发 DOWNLOAD-FILE(info=SEC-POLICY) ⇒ 主机回文件级应答
    `OK@0x<凭证长度>` ⇒ `OK@0x0`（CHK_NO，无校验）⇒ 凭证原文。
    申报长度就是凭证真实长度，设备按申报收满即止，不需要退出行。
    设备验签通过打印 `[SEC] verify pass`、END result=OK；验签失败打印
    `Server is not authenticated` 一类文本并以非 OK 收尾（→ RoundRejected）。

    凭证在这一刻才向宿主索取：设备开口要，主机才给——不是启动前提。
    """
    api.send(policy_frame)

    def on_download(client, _event):
        credential = bytes(client.request_input("auth", "SEC-POLICY"))
        client.reply(b"OK@0x%X\x00" % len(credential))
        client.reply(b"OK@0x0\x00")
        client.supply(credential, len(credential))

    _pump(api, on_download=on_download)


def _info_rounds(api):
    """注册后的信息读取（与官方同序、同参数）。

    READ-PARTITION-TABLE 与第二条 GET-HW-INFO 不发（实测会把设备写死，
    分区表改由 READ-FLASH 读 GPT——见 route_main 末尾）。
    """
    api.command("GET-HW-INFO", {
        "target_file": xml.mem_source(HW_INFO_TARGET, HW_INFO_LEN)},
        save_path=HW_INFO_PATH)
    api.log("info", "READ-PARTITION-TABLE 与第二条 GET-HW-INFO 已跳过"
                    "（设备侧缓冲分配缺陷，实测会把设备写死）")


def route_main(api):
    if api.attached:
        # 设备已运行 DA2：没有 DA1 前置与 2nd-DA 下载，直接从命令态接上。
        _await_start(api)
    else:
        _await_start(api)          # DA1 上传后的首个 CMD:START
        _da1_preamble(api)         # DA1 前置五条
        api.serve_da2()            # BOOT-TO + 2nd-DA 供数
        _await_start(api)          # DA2 启动后的首个 CMD:START

    api.command("HOST-SUPPORTED-COMMANDS")
    api.command("NOTIFY-INIT-HW")
    api.command("CAN-HIGHER-USB-SPEED", {
        "target_file": xml.mem_source(SPEED_TARGET, SPEED_TARGET_LEN)})
    api.command("SECURITY-GET-DEV-FW-INFO", {
        "target_file": xml.mem_source(FW_INFO_TARGET, FW_INFO_LEN)},
        save_path=FW_INFO_PATH)

    # 认证轮（唯一路径）：SECURITY-SET-FLASH-POLICY + 实时索取签名应答。
    # 官方是挑战应答：设备在 DOWNLOAD-FILE 里开口要，主机才给——签名不是
    # 启动前提，载荷走到这一步才向宿主索取（api.request_input）。
    # 索取超时/取消与设备拒签是三种不同的失败，各自分开。
    policy_frame = xml.security_set_flash_policy(
        xml.mem_source(SEC_POLICY_SOURCE, SEC_POLICY_LEN))
    try:
        _signature_round(api, policy_frame)
    except RoundRejected as exc:
        # 设备收到凭证并明确拒绝（验签未过）；传输失败不落入此分支。
        raise AuthRejected("设备拒绝签名凭证（验签未过）: %s" % (exc,))

    _info_rounds(api)

    # 分区表是工作流的一部分：READ-FLASH 直读设备 GPT 落盘，供壳解析。
    # 不走 READ-PARTITION-TABLE（设备侧 16 KB 输出缓冲不可靠）。
    api.command("READ-FLASH", {
        "partition": GPT_LABEL,
        "offset": GPT_OFFSET,
        "length": GPT_LENGTH,
        "target_file": GPT_TARGET,
    }, save_path=GPT_PATH)

    # 移交：第一阶段结束。此后由壳经内核的二阶段命令循环投递意图。
    api.enter_loop()
