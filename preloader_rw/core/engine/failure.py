# -*- coding: utf-8 -*-
"""失败分类。

主机超时 ≠ 设备故障。壳按本枚举决定处置，不再解析错误字符串。
"""

from enum import Enum


class Failure(str, Enum):
    HOST_TIMEOUT = "host_timeout"        # 本机预算内没等到
    DEV_ERROR = "dev_error"              # 设备回 ERR / END 非 OK
    PROTO_MISMATCH = "proto_mismatch"    # 帧形态或字段不符合协议
    DEV_GONE = "dev_gone"                # 设备从总线消失
    USER_CANCEL = "user_cancel"          # 外部取消
    ROUTE_ERROR = "route_error"          # 载荷自身断言失败 / 异常


# 处置建议：重试 / 重新引导 / 直接终止
DISPOSITION = {
    Failure.HOST_TIMEOUT: "retry_or_rebootstrap",
    Failure.DEV_ERROR: "stop",
    Failure.PROTO_MISMATCH: "stop",
    Failure.DEV_GONE: "wait_replug",
    Failure.USER_CANCEL: "stop",
    Failure.ROUTE_ERROR: "stop",
}
