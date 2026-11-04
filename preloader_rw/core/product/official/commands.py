# -*- coding: utf-8 -*-
"""官方命令的 CommandSpec。

命令名只作为注册表数据出现：本文件用一张数据表描述“名字 → 载荷构造 →
阶段”，不按名字写分支。内核侧的命令清单见
`preloader_rw/profiles/mtk_official.json`（名字源自 `proto/da2_commands.py`）；
本文件只登记协议形态已由 `src\\commands.py` / `preloader_rw.core.proto.xml` 证实的命令。

READ-FLASH / READ-PARTITION-TABLE 的载荷与规格由 read.py 组装，但载荷
构造函数仍在本文件，避免官方路线出现第二套命令字节。
"""

from ...engine.command import CommandSpec, Stage
from ...engine.events import SessionState
from ...engine.failure import Failure
from ...engine.waits import WaitKind
from ...engine.inbound import EventKind
from ...proto import xml
from . import partitions
from .write import WriteCommandInstance, WriteRequest

__all__ = [
    "STORAGE_LABEL",
    "SimpleCommandInstance",
    "as_args",
    "read_flash_payload",
    "read_partition_payload",
    "read_partition_table_payload",
    "write_spec",
    "specs",
    "read_flash_spec",
    "read_partition_spec",
    "upload_info_spec",
    "read_partition_table_spec",
    "read_specs",
]

# READ-FLASH 用存储 LUN 标签，WRITE-PARTITION 用分区名；两者不可混用。
STORAGE_LABEL = "UFS-LUA2"


def as_args(args):
    """统一参数入口：None / dict / 可转 dict 的对象。"""
    if args is None:
        return {}
    if isinstance(args, dict):
        return dict(args)
    try:
        return dict(args)
    except (TypeError, ValueError):
        raise TypeError("命令参数必须是 dict 或键值对，收到 %r" % (type(args).__name__,))


def _need(args, key):
    if key not in args:
        raise ValueError("缺少命令参数: %s" % (key,))
    return args[key]


# ---- 载荷构造 -------------------------------------------------------

def _boot_to(args):
    return xml.boot_to(_need(args, "at_address"),
                       _need(args, "jmp_address"),
                       _need(args, "source_file"))


def _host_supported(args):
    return xml.host_supported()


def _notify_init_hw(args):
    return xml.notify_init_hw()


def _can_higher_speed(args):
    return xml.can_higher_speed(_need(args, "target_file"))


def _security_get_fw_info(args):
    return xml.security_get_fw_info(_need(args, "target_file"))


def _security_set_flash_policy(args):
    return xml.security_set_flash_policy(_need(args, "source_file"))


def _erase_partition(args):
    return xml.erase_partition(_need(args, "partition"))


def _get_hw_info(args):
    return xml.get_hw_info(_need(args, "target_file"))


def _get_ufs_info(args):
    return xml.get_ufs_info(_need(args, "target_file"))


def _get_da_info(args):
    return xml.get_da_info(_need(args, "target_file"))


def _set_runtime_parameter(args):
    return xml.set_runtime_parameter(
        checksum_level=args.get("checksum_level", "NONE"),
        battery_exist=args.get("battery_exist", "AUTO-DETECT"),
        da_log_level=args.get("da_log_level", "INFO"),
        log_channel=args.get("log_channel", "USB"),
        system_os=args.get("system_os", "WINDOWS"),
    )


def _set_host_info(args):
    return xml.set_host_info(args.get("timestamp"))


def _get_sys_property(args):
    return xml.get_sys_property(_need(args, "key"), _need(args, "target_file"))


def read_flash_payload(partition=STORAGE_LABEL, offset=0, length=0, target_file=""):
    """READ-FLASH 的命令字节；partition 是存储标签，不是分区名。"""
    return xml.read_flash(partition, offset, length, target_file)


def read_partition_payload(partition, target_file=""):
    """整分区回读的命令字节；只给分区名，设备自查分区表定起止。"""
    return xml.read_partition(partition, target_file)


def read_partition_table_payload(target_file=""):
    return xml.read_partition_table(target_file)


# ---- 通用命令实例 ---------------------------------------------------

class SimpleCommandInstance(object):
    """单阶段 / 双阶段命令的通用推进器。

    只认 END 的 result：OK 即成功，其余为设备错误。UPLOAD_FILE 仅用于
    把 upload 阶段的读命令（read.py）推进到 end。
    """

    # 读命令的落盘路径；由 read.py 的规格在 build 时写入。
    recv_path = ""

    def __init__(self, spec, payload, on_done=None):
        self._spec = spec
        self.payload = payload
        self._index = 0
        self._done = False
        self._failure = None
        self._on_done = on_done

    @property
    def spec(self):
        return self._spec

    @property
    def stage(self):
        return self._spec.stages[min(self._index, len(self._spec.stages) - 1)]

    @property
    def done(self):
        return self._done

    @property
    def failure(self):
        return self._failure

    def next_wait(self):
        return self._spec.wait_for(self._index)

    def advance(self, event):
        if self._done:
            return

        kind = event.kind

        if kind is EventKind.DEV_GONE:
            self._fail(Failure.DEV_GONE)
            return

        if kind is EventKind.TIMEOUT:
            self._fail(Failure.HOST_TIMEOUT)
            return

        if kind is EventKind.UPLOAD_FILE and self.stage.name == "upload":
            self._index = min(self._index + 1, len(self._spec.stages) - 1)
            return

        if kind is EventKind.END:
            if event.result == "OK":
                self._done = True
                if self._on_done is not None:
                    self._on_done(self)
            else:
                self._fail(Failure.DEV_ERROR)

    def _fail(self, failure):
        self._failure = failure
        self._done = True
        if self._on_done is not None:
            self._on_done(self)


# ---- 规格表 ---------------------------------------------------------
#
# 每行：(命令名, 载荷构造, 阶段, 超时档, 幂等)
# 命令名是数据；新增命令只加行，不写分支。

_SIMPLE_ROWS = (
    ("BOOT-TO", _boot_to,
     (Stage("end", WaitKind.FRAME),), "boot", False),
    ("HOST-SUPPORTED-COMMANDS", _host_supported,
     (Stage("end", WaitKind.FRAME),), "dialog", True),
    ("NOTIFY-INIT-HW", _notify_init_hw,
     (Stage("end", WaitKind.FRAME),), "dialog", True),
    ("CAN-HIGHER-USB-SPEED", _can_higher_speed,
     (Stage("end", WaitKind.FRAME),), "dialog", True),
    ("SECURITY-GET-DEV-FW-INFO", _security_get_fw_info,
     (Stage("end", WaitKind.FRAME),), "dialog", False),
    ("SECURITY-SET-FLASH-POLICY", _security_set_flash_policy,
     (Stage("end", WaitKind.FRAME),), "dialog", False),
    ("ERASE-PARTITION", _erase_partition,
     (Stage("end", WaitKind.FRAME),), "write", False),
    ("SET-RUNTIME-PARAMETER", _set_runtime_parameter,
     (Stage("end", WaitKind.FRAME),), "dialog", True),
    ("SET-HOST-INFO", _set_host_info,
     (Stage("end", WaitKind.FRAME),), "dialog", True),
    ("GET-SYS-PROPERTY", _get_sys_property,
     (Stage("end", WaitKind.FRAME),), "dialog", True),
)


def _make_spec(name, encode, stages, profile, idempotent):
    def build(args=None):
        return SimpleCommandInstance(spec, encode(as_args(args)))

    spec = CommandSpec(
        name=name,
        build=build,
        stages=stages,
        idempotent=idempotent,
        timeout_profile=profile,
    )
    return spec


def write_spec(provider):
    """WRITE-PARTITION 的规格；四段对话固定为 exists/download/supply/end。"""

    def build(args=None):
        return WriteCommandInstance(spec, args, provider)

    spec = CommandSpec(
        name="WRITE-PARTITION",
        build=build,
        stages=(
            # 设备在发 EXISTS 前可能先擦除，沿用写档的静默预算。
            Stage("exists", WaitKind.FRAME),
            Stage("download", WaitKind.FRAME),
            Stage("supply", WaitKind.TRANSFER, "write"),
            Stage("end", WaitKind.FRAME, "write"),
        ),
        idempotent=False,
        timeout_profile="write",
    )
    return spec


def write_flash_spec(provider):
    """WRITE-FLASH 的规格：设备先问 FILE-SIZE，再 DOWNLOAD-FILE。

    与 WRITE-PARTITION 同四段对话，只有开场问句不同（FILE-SIZE 代替 EXISTS）。
    命令字节见 `proto\\xml.write_flash`。
    """

    def build(args=None):
        return WriteCommandInstance(spec, args, provider)

    spec = CommandSpec(
        name="WRITE-FLASH",
        build=build,
        stages=(
            Stage("exists", WaitKind.FRAME),
            Stage("download", WaitKind.FRAME),
            Stage("supply", WaitKind.TRANSFER, "write"),
            Stage("end", WaitKind.FRAME, "write"),
        ),
        idempotent=False,
        timeout_profile="write",
    )
    return spec


def specs(write_provider):
    """官方非读命令的规格集合（写命令需要 Provider 绑定）。"""
    out = [_make_spec(name, encode, stages, profile, idempotent)
           for name, encode, stages, profile, idempotent in _SIMPLE_ROWS]
    out.append(write_spec(write_provider))
    out.append(write_flash_spec(write_provider))
    return tuple(out)


# ---- 读命令规格 -----------------------------------------------------
#
# READ-FLASH / READ-PARTITION-TABLE 的 CommandSpec 定义在本文件，read.py
# 只做再导出并提供上行 Provider，避免出现第二套命令字节。

def _release_callback(provider):
    if provider is None:
        return None
    return lambda instance: provider.release(instance)


def read_flash_spec(provider=None):
    """READ-FLASH：存储标签 + offset + length + 上传目标文件。

    target_file 发给设备（回显在 UPLOAD-FILE 请求里）；save_path 是主机落盘
    路径。两者分开，设备侧字符串不必是合法路径。
    """

    def build(args=None):
        params = as_args(args)
        payload = read_flash_payload(
            params.get("partition", STORAGE_LABEL),
            int(params.get("offset", 0)),
            int(params.get("length", 0)),
            params.get("target_file", ""),
        )
        instance = SimpleCommandInstance(spec, payload,
                                         on_done=_release_callback(provider))
        instance.recv_path = params.get("save_path") or params.get("target_file", "")
        if provider is not None:
            provider.arm(instance)
        return instance

    spec = CommandSpec(
        name="READ-FLASH",
        build=build,
        stages=(
            Stage("upload", WaitKind.TRANSFER, "upload"),
            Stage("end", WaitKind.FRAME, "upload"),
        ),
        idempotent=True,
        timeout_profile="upload",
    )
    return spec


def read_partition_spec(provider=None):
    """READ-PARTITION：只给分区名 + 上传目标文件；设备按分区表定起止。"""

    def build(args=None):
        params = as_args(args)
        payload = read_partition_payload(_need(params, "partition"),
                                         params.get("target_file", ""))
        instance = SimpleCommandInstance(spec, payload,
                                         on_done=_release_callback(provider))
        instance.recv_path = params.get("target_file", "")
        if provider is not None:
            provider.arm(instance)
        return instance

    spec = CommandSpec(
        name="READ-PARTITION",
        build=build,
        stages=(
            Stage("upload", WaitKind.TRANSFER, "upload"),
            Stage("end", WaitKind.FRAME, "upload"),
        ),
        idempotent=True,
        timeout_profile="upload",
    )
    return spec


def upload_info_spec(provider, name, encoder, requires=None):
    """GET-HW-INFO / GET-UFS-INFO / GET-DA-INFO：设备先发 UPLOAD-FILE，
    再上传信息 XML；主机落盘，由调用方解析。

    requires 给定时覆盖默认的「只有 GATED 可发」。这是把某条命令标记成
    「设备状态未知（STALE）时的探针」的唯一方式：租约只对 requires 含
    STALE 的命令放行（见 engine.lease.begin_command(probe=True)）。
    """

    def build(args=None):
        params = as_args(args)
        payload = encoder(params)
        instance = SimpleCommandInstance(spec, payload,
                                         on_done=_release_callback(provider))
        instance.recv_path = params.get("save_path", "")
        if provider is not None:
            provider.arm(instance)
        return instance

    kwargs = {} if requires is None else {"requires": frozenset(requires)}
    spec = CommandSpec(
        name=name,
        build=build,
        stages=(
            Stage("upload", WaitKind.TRANSFER, "upload"),
            Stage("end", WaitKind.FRAME, "upload"),
        ),
        idempotent=True,
        timeout_profile="upload",
        **kwargs
    )
    return spec


def read_partition_table_spec(provider=None, holder=None, save_path=""):
    """READ-PARTITION-TABLE：设备把表上传到主机（golden 002034 i=414-436）。

    设备先发 UPLOAD-FILE（target_file 即设备侧 MEM:// 地址），随后上传 XML
    表；主机落盘后解析成运行期权威表并写入 holder。上传阶段与 READ-FLASH
    同形，故阶段名同为 upload。
    """

    def build(args=None):
        params = as_args(args)
        payload = read_partition_table_payload(params.get("target_file", ""))
        instance = SimpleCommandInstance(spec, payload, on_done=_done)
        instance.recv_path = params.get("save_path", "") or save_path
        if provider is not None:
            provider.arm(instance)
        return instance

    def _done(instance):
        if provider is not None:
            provider.release(instance)
        if instance.failure is not None or holder is None:
            return
        if not instance.recv_path:
            return
        partitions.load_into(holder, instance.recv_path)

    spec = CommandSpec(
        name="READ-PARTITION-TABLE",
        build=build,
        stages=(
            Stage("upload", WaitKind.TRANSFER, "upload"),
            Stage("end", WaitKind.FRAME, "upload"),
        ),
        idempotent=True,
        timeout_profile="upload",
    )
    return spec


def read_specs(provider=None, holder=None, save_path=""):
    return (read_partition_spec(provider),
            read_flash_spec(provider),
            read_partition_table_spec(provider, holder, save_path),
            upload_info_spec(provider, "GET-HW-INFO", _get_hw_info),
            # GET-UFS-INFO 已从路线移除：部分平台上该命令在收满申报长度后
            # 不再发 CMD:END，主机侧只能按「软成功」收尾，设备随后不再服务
            # 任何新命令，故不进入任何路线。
            # 命令表仍保留该项：需要时用探针单独发，不混进正常流程。
            # GET-DA-INFO 是设备状态未知（STALE）时的指定探针：回合完整
            # （144 B，回合尾 END OK），且设备侧无已知缺陷。
            # 只加 STALE 到 requires，不改任何帧字节。
            upload_info_spec(provider, "GET-DA-INFO", _get_da_info,
                             requires=(SessionState.GATED, SessionState.STALE)))

