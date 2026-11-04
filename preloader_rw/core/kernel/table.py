# -*- coding: utf-8 -*-
"""命令表：内核认识哪些命令、帧怎么拼，全部来自数据。

内核里不再出现任何「某个固件才有」的命令名字面量。它启动时读一张表，
据此：

  · 判定设备上行帧属于哪一类（START / END / DOWNLOAD-FILE / 文件查询 …）；
  · 按名字取命令条目：参数清单、拼帧方式、回合内可能索要的 `<info>`、
    可能上传的 `<info>`、可能的 `<key>` 查询、安全级；
  · 用表里的探针裁决「命令面是否真的打通」。

默认表随仓库发布（`preloader_rw/profiles/mtk_official.json`）。换表 =
换一个 JSON（外加与它配套的载荷），内核代码一行不改。

拼帧有两种写法，同一张表里可以混用：

  "build": "read_flash"                            具名构造器
  "build": {"template": "<command>…</command>"}    纯数据模板，`{参数}` 占位

具名构造器来自 `kernel/commands.py` 的 BUILDERS（带条件元素、嵌套 XML 的
命令只能这么写）；模板只做扁平替换，给「换一份 DA、XML 形状略有不同」的
场景用，写表的人不必碰 Python。

校验一律在装载时做完并给出可读错误：缺参数、模板引用了未声明的参数、
探针命令不在表里、名字重复，都在启动前报错，不等到实机回合里才炸。
"""

import hashlib
import json
import os
import string
from dataclasses import dataclass
from typing import Callable, Mapping, Tuple

from ..proto import xml
from ..proto.const import EXISTS_NOT, OK

__all__ = [
    "SCHEMA",
    "SHAPE_COMMAND",
    "SHAPE_UPLOAD",
    "SHAPE_DOWNLOAD",
    "SHAPE_DUPLEX",
    "PARTITION_TAG",
    "TableError",
    "Command",
    "Probe",
    "Transport",
    "FileSys",
    "Boot",
    "CommandTable",
    "load",
    "parse",
]

SCHEMA = 1

SHAPE_COMMAND = "command"
SHAPE_UPLOAD = "upload"
SHAPE_DOWNLOAD = "download"
SHAPE_DUPLEX = "duplex"

# pushes 里的动态项：设备把 da/arg/partition 的值当 `<info>` 用。
PARTITION_TAG = "<partition>"

PHASES = ("pre", "post", "both")


class TableError(ValueError):
    """表不成立。消息必须能直接告诉写表的人改哪一行。"""


@dataclass(frozen=True)
class Command:
    """一条命令的静态描述。字段含义与旧 `kernel/commands.py` 的表完全一致。"""

    name: str
    phase: str
    args: Tuple[str, ...]
    build: Callable[[Mapping], bytes]
    pulls: Tuple[str, ...] = ()
    pushes: Tuple[str, ...] = ()
    fs_keys: Tuple[str, ...] = ()
    levels: int = 1

    @property
    def shape(self):
        if self.pulls and self.pushes:
            return SHAPE_DUPLEX
        if self.pulls:
            return SHAPE_DOWNLOAD
        if self.pushes:
            return SHAPE_UPLOAD
        return SHAPE_COMMAND

    @property
    def args_names(self):
        """去掉 `da/arg/` 前缀后的扁平名字，供载荷写参数 dict 用。"""
        return tuple(path[len("da/arg/"):] for path in self.args)


@dataclass(frozen=True)
class Probe:
    """注册裁决探针：命令、目标缓冲、期望字节数（只用于日志）。"""

    command: str
    target: str
    expect: int = 0
    save_as: str = ""


@dataclass(frozen=True)
class Transport:
    """传输 / 框架命令的符号。设备帧靠这些字面量分类。"""

    start: bytes = b"CMD:START"
    end: bytes = b"CMD:END"
    download: bytes = b"CMD:DOWNLOAD-FILE"
    upload: bytes = b"CMD:UPLOAD-FILE"
    filesys: bytes = b"CMD:FILE-SYS-OPERATION"
    notice: bytes = b"CMD:"
    ok: bytes = OK


@dataclass(frozen=True)
class FileSys:
    """文件查询的键名与缺省应答。"""

    exists: str = "EXISTS"
    file_size: str = "FILE-SIZE"
    not_exists_reply: bytes = EXISTS_NOT


@dataclass(frozen=True)
class Boot:
    """2nd-DA 接管的默认参数（KernelConfig 显式给了就以那里为准）。"""

    at: int = 0x40000000
    jmp: int = 0x40000000
    da2_mem: int = 0xF5D7794


class CommandTable(object):
    """一张装好的表。只读；装载后不再变化。"""

    def __init__(self, name, source, commands, transport=None, filesys=None,
                 boot=None, probes=(), da2_tags=("2nd-DA", "DA2", "2nd_DA"),
                 partition_tag=PARTITION_TAG, digest=""):
        self.name = name
        self.source = source
        self.commands = dict(commands)
        self.transport = transport or Transport()
        self.filesys = filesys or FileSys()
        self.boot = boot or Boot()
        self.probes = tuple(probes)
        self.da2_tags = tuple(da2_tags)
        self.partition_tag = partition_tag
        self.digest = digest

    # ---- 查询 -------------------------------------------------------

    def __len__(self):
        return len(self.commands)

    def __contains__(self, name):
        return _bare(name) in self.commands

    def get(self, name):
        """按名字取条目；带不带 CMD: 前缀都接受。未登记即抛 KeyError。"""
        key = _bare(name)
        try:
            return self.commands[key]
        except KeyError:
            raise KeyError("未登记的命令: %s（表 %s）" % (name, self.name))

    def names(self, phase=None):
        """已登记的命令名；phase 取 pre / post。"""
        items = tuple(self.commands.values())
        if phase is None:
            return tuple(item.name for item in items)
        if phase == "post":
            return tuple(i.name for i in items if i.phase in ("post", "both"))
        if phase == "pre":
            return tuple(i.name for i in items if i.phase in ("pre", "both"))
        raise ValueError("phase 只能是 pre / post，收到 %r" % (phase,))

    def shape(self, name, override=None):
        if override is not None:
            if override not in (SHAPE_COMMAND, SHAPE_UPLOAD, SHAPE_DOWNLOAD,
                                SHAPE_DUPLEX):
                raise ValueError("未知回合形态: %r" % (override,))
            return override
        return self.get(name).shape

    def frame(self, name, args=None, args_xml=None):
        """构造命令帧。

        args     交给 build 的参数 dict；键名见 `Command.args_names`。
        args_xml 需要绕开命令表直接给命令体时使用，须含结束标签 `</da>`。
        """
        spec = self.get(name)
        if args_xml is not None:
            return xml.command(args_xml)
        return spec.build(dict(args or {}))

    def uncovered(self, known):
        """`known` 里已命中、但本表未登记的名字（供离线判据用）。"""
        return tuple(name for name in known if _bare(name) not in self.commands)

    # ---- 上行帧分类 -------------------------------------------------

    def classify(self, payload):
        """XML 命令帧 → 类别标记；不认识返回空串。

        取值：download / filesys / upload / start / end / notice。
        顺序有意义：`CMD:DOWNLOAD-FILE` 里也含 `CMD:`，必须先判具体再判通配。
        """
        if not payload or payload[:5] != b"<?xml":
            return ""
        t = self.transport
        if t.download in payload:
            return "download"
        if t.filesys in payload:
            return "filesys"
        if t.upload in payload:
            return "upload"
        if t.start in payload:
            return "start"
        if t.end in payload:
            return "end"
        if t.notice in payload:
            return "notice"
        return ""

    # ---- 展示 -------------------------------------------------------

    def summary(self):
        return "表 %s（%d 条命令，sha=%s，来源 %s）" % (
            self.name, len(self.commands), self.digest or "?", self.source)

    def probe_target(self, default=""):
        """第一条探针的目标；表里没配探针时用 default。"""
        return self.probes[0].target if self.probes else default


# ---- 装载 -------------------------------------------------------------

def _bare(name):
    return name[len("CMD:"):] if name.startswith("CMD:") else name


def _bytes(value, label):
    if isinstance(value, bytes):
        return value
    if not isinstance(value, str) or not value:
        raise TableError("%s 必须是非空字符串" % (label,))
    return value.encode("utf-8")


def _int(value, label, default=None):
    if value is None:
        if default is None:
            raise TableError("%s 未给出" % (label,))
        return default
    try:
        return int(str(value), 0)
    except (TypeError, ValueError):
        raise TableError("%s 需要整数（十进制或 0x 十六进制），收到 %r"
                         % (label, value))


def _strings(value, label):
    if value is None:
        return ()
    if not isinstance(value, (list, tuple)):
        raise TableError("%s 必须是字符串数组，收到 %r" % (label, type(value).__name__))
    out = []
    for item in value:
        if not isinstance(item, str) or not item:
            raise TableError("%s 含空项或非字符串: %r" % (label, item))
        out.append(item)
    return tuple(out)


def _template_fields(template):
    """模板里引用的参数名；空占位 / 位置占位一律拒绝。"""
    fields = []
    for _literal, name, _spec, _conv in string.Formatter().parse(template):
        if name is None:
            continue
        if not name:
            raise TableError("模板里有空占位 {}，请写参数名")
        if name.isdigit():
            raise TableError("模板不支持位置占位 {%s}，请写参数名" % (name,))
        fields.append(name.split(".")[0].split("[")[0])
    return fields


def _template_builder(command, template, head=True, nul=True):
    """把 `{参数}` 模板包成 build 函数。缺参数时报出是哪条命令。"""

    def build(args):
        try:
            body = template.format(**args)
        except KeyError as exc:
            raise TableError("命令 %s 的模板缺少参数 %s（已给 %r）"
                             % (command, exc, sorted(args)))
        except (IndexError, ValueError) as exc:
            raise TableError("命令 %s 的模板渲染失败: %s" % (command, exc))
        raw = body.encode("utf-8")
        if head:
            raw = xml.HEAD + raw
        if nul:
            raw = raw + xml.NUL
        return raw

    build.__name__ = "template_%s" % (command.lower().replace("-", "_"),)
    return build


def _parse_build(item, builders, args_names, label):
    spec = item.get("build")
    if isinstance(spec, str):
        spec = {"builder": spec}
    if not isinstance(spec, dict):
        raise TableError("%s: build 必须是构造器名或对象" % (label,))

    if "template" in spec:
        template = spec.get("template")
        if not isinstance(template, str) or not template.strip():
            raise TableError("%s: template 必须是非空字符串" % (label,))
        used = _template_fields(template)
        unknown = [name for name in used if name not in args_names]
        if unknown:
            raise TableError(
                "%s: 模板引用了未声明的参数 %r（args 里只声明了 %r）"
                % (label, unknown, list(args_names)))
        return _template_builder(_bare(item["name"]), template,
                                 head=spec.get("head", True),
                                 nul=spec.get("nul", True))

    name = spec.get("builder")
    if not isinstance(name, str) or not name:
        raise TableError("%s: build 需要 builder 或 template" % (label,))
    fn = builders.get(name)
    if fn is None:
        raise TableError("%s: 未知构造器 %r（可用: %s）"
                         % (label, name, ", ".join(sorted(builders))))
    return fn


def _parse_commands(raw, builders, source):
    if not isinstance(raw, (list, tuple)) or not raw:
        raise TableError("表 %s 的 commands 必须是非空数组" % (source,))
    out = {}
    for index, item in enumerate(raw):
        if not isinstance(item, dict):
            raise TableError("commands[%d] 必须是对象" % (index,))
        name = item.get("name")
        if not isinstance(name, str) or not name.strip():
            raise TableError("commands[%d] 缺 name" % (index,))
        name = _bare(name.strip())
        label = "命令 %s" % (name,)
        if name in out:
            raise TableError("%s 重复登记" % (label,))
        phase = item.get("phase", "post")
        if phase not in PHASES:
            raise TableError("%s: phase 只能是 %s，收到 %r"
                             % (label, " / ".join(PHASES), phase))
        args = _strings(item.get("args"), "%s 的 args" % (label,))
        for path in args:
            if not path.startswith("da/arg/"):
                raise TableError("%s: args 项必须以 da/arg/ 开头，收到 %r"
                                 % (label, path))
        args_names = tuple(path[len("da/arg/"):] for path in args)
        build = _parse_build(item, builders, args_names, label)
        levels = item.get("levels", 1)
        if not isinstance(levels, int) or levels < 1:
            raise TableError("%s: levels 必须是 >=1 的整数" % (label,))
        out[name] = Command(
            name=name, phase=phase, args=args, build=build,
            pulls=_strings(item.get("pulls"), "%s 的 pulls" % (label,)),
            pushes=_strings(item.get("pushes"), "%s 的 pushes" % (label,)),
            fs_keys=_strings(item.get("fs_keys"), "%s 的 fs_keys" % (label,)),
            levels=levels,
        )
    return out


def _parse_probes(raw, commands, source):
    if raw is None:
        return ()
    if not isinstance(raw, (list, tuple)):
        raise TableError("表 %s 的 probe 必须是数组" % (source,))
    out = []
    for index, item in enumerate(raw):
        if isinstance(item, str):
            item = {"command": item}
        if not isinstance(item, dict):
            raise TableError("probe[%d] 必须是对象" % (index,))
        command = _bare(str(item.get("command") or "").strip())
        if not command:
            raise TableError("probe[%d] 缺 command" % (index,))
        if command not in commands:
            raise TableError("probe[%d]: 命令 %s 未在 commands 里登记，探针发不出去"
                             % (index, command))
        target = str(item.get("target") or "").strip()
        if not target:
            raise TableError("probe[%d]: 缺 target（设备侧缓冲，如 MEM://0x…:0x…）"
                             % (index,))
        expect = item.get("expect", 0)
        if not isinstance(expect, int) or expect < 0:
            raise TableError("probe[%d]: expect 必须是非负整数" % (index,))
        out.append(Probe(command=command, target=target, expect=expect,
                         save_as=str(item.get("save_as") or "")))
    return tuple(out)


def _parse_transport(raw):
    if not isinstance(raw, dict):
        if raw is not None:
            raise TableError("transport 必须是对象")
        raw = {}
    default = Transport()
    return Transport(
        start=_bytes(raw.get("start", default.start), "transport.start"),
        end=_bytes(raw.get("end", default.end), "transport.end"),
        download=_bytes(raw.get("download", default.download), "transport.download"),
        upload=_bytes(raw.get("upload", default.upload), "transport.upload"),
        filesys=_bytes(raw.get("filesys", default.filesys), "transport.filesys"),
        notice=_bytes(raw.get("notice", default.notice), "transport.notice"),
        ok=_bytes(raw.get("ok", default.ok), "transport.ok"),
    )


def _parse_filesys(raw):
    if not isinstance(raw, dict):
        if raw is not None:
            raise TableError("filesys 必须是对象")
        raw = {}
    default = FileSys()
    return FileSys(
        exists=str(raw.get("exists", default.exists)),
        file_size=str(raw.get("file_size", default.file_size)),
        not_exists_reply=_bytes(
            raw.get("not_exists_reply", default.not_exists_reply),
            "filesys.not_exists_reply"),
    )


def _parse_boot(raw):
    if not isinstance(raw, dict):
        if raw is not None:
            raise TableError("boot 必须是对象")
        raw = {}
    default = Boot()
    return Boot(
        at=_int(raw.get("at"), "boot.at", default.at),
        jmp=_int(raw.get("jmp"), "boot.jmp", default.jmp),
        da2_mem=_int(raw.get("da2_mem"), "boot.da2_mem", default.da2_mem),
    )


def parse(data, builders, source="<memory>"):
    """把已解析的 JSON 对象装成表；任何不成立都抛 TableError。"""
    if not isinstance(data, dict):
        raise TableError("表 %s 的根必须是对象" % (source,))
    schema = data.get("schema")
    if schema != SCHEMA:
        raise TableError("表 %s 的 schema 必须是 %d，收到 %r"
                         % (source, SCHEMA, schema))
    name = str(data.get("name") or "").strip() or os.path.basename(str(source))
    digest = hashlib.sha256(
        json.dumps(data, sort_keys=True, ensure_ascii=False).encode("utf-8")
    ).hexdigest()[:12]

    commands = _parse_commands(data.get("commands"), builders, source)
    probes = _parse_probes(data.get("probe"), commands, source)
    handover = data.get("handover") or {}
    if not isinstance(handover, dict):
        raise TableError("handover 必须是对象")
    tags = _strings(handover.get("da2_info"), "handover.da2_info") or (
        "2nd-DA", "DA2", "2nd_DA")

    return CommandTable(
        name=name,
        source=str(source),
        commands=commands,
        transport=_parse_transport(data.get("transport")),
        filesys=_parse_filesys(data.get("filesys")),
        boot=_parse_boot(data.get("boot")),
        probes=probes,
        da2_tags=tags,
        partition_tag=str(data.get("partition_tag") or PARTITION_TAG),
        digest=digest,
    )


def load(path, builders, source=None):
    """从 JSON 文件装表。文件不存在 / JSON 语法错都转成 TableError。"""
    text = os.fspath(path)
    if not os.path.isfile(text):
        raise TableError("命令表不存在: %s" % (text,))
    try:
        with open(text, "r", encoding="utf-8") as handle:
            data = json.load(handle)
    except OSError as exc:
        raise TableError("命令表读取失败: %s（%s）" % (text, exc))
    except ValueError as exc:
        raise TableError("命令表不是合法 JSON: %s（%s）" % (text, exc))
    return parse(data, builders, source=source or text)
