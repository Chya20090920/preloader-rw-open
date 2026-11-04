# -*- coding: utf-8 -*-
"""只读枚举：按 GUID_DEVINTERFACE_USB_DEVICE 遍历 USB 设备。

不打开设备句柄、不写注册表。VID/PID 取自硬件 ID 属性，地址取自
SPDRP_ADDRESS，说明取自 SPDRP_DEVICEDESC。返回 DeviceInfo 列表。
"""

import re

from preloader_rw.core.ports.device import DeviceInfo

from . import bindings

_HWID_RE = re.compile(r"USB\\VID_([0-9A-Fa-f]{4})&PID_([0-9A-Fa-f]{4})")


def _decode(data_type: int, raw: bytes):
    """把注册表属性原始字节解成字符串列表。"""
    if not raw:
        return []
    if data_type not in (bindings.REG_SZ, bindings.REG_EXPAND_SZ,
                         bindings.REG_MULTI_SZ):
        return []
    text = raw.decode("utf-16-le", "replace")
    return [part for part in text.split("\x00") if part]


def _read_vid_pid(api, hdev, devinfo):
    """从硬件 ID 取 VID/PID；取不到返回 None。"""
    prop = api.get_property(hdev, devinfo, bindings.SPDRP_HARDWAREID)
    if prop:
        for text in _decode(*prop):
            hit = _HWID_RE.match(text)
            if hit:
                return int(hit.group(1), 16), int(hit.group(2), 16)
    instance = api.get_device_instance_id(hdev, devinfo)
    if instance:
        hit = _HWID_RE.match(instance)
        if hit:
            return int(hit.group(1), 16), int(hit.group(2), 16)
    return None


def _read_dword(api, hdev, devinfo, prop: int) -> int:
    data = api.get_property(hdev, devinfo, prop)
    if not data:
        return 0
    _data_type, raw = data
    if len(raw) >= 4:
        return int.from_bytes(raw[:4], "little")
    return 0


def _read_text(api, hdev, devinfo, prop: int) -> str:
    data = api.get_property(hdev, devinfo, prop)
    if not data:
        return ""
    parts = _decode(*data)
    return parts[0] if parts else ""


def list_interfaces(vid=None):
    """枚举接口，返回 [(DeviceInfo, 接口路径)]；无设备时返回空列表。

    vid 为 None 表示不按厂商过滤。整个过程不打开设备句柄。
    """
    api = bindings.SetupApi()
    hdev = api.create_device_info_list(bindings.GUID_DEVINTERFACE_USB_DEVICE)
    out = []
    seen = set()
    try:
        index = 0
        while True:
            ok, iface, devinfo, _err = api.enum_interface(
                hdev, index, bindings.GUID_DEVINTERFACE_USB_DEVICE)
            if not ok:
                break
            index += 1

            ids = _read_vid_pid(api, hdev, devinfo)
            if ids is None:
                continue
            dev_vid, pid = ids
            if vid is not None and dev_vid != int(vid):
                continue

            path = api.interface_detail(hdev, iface, devinfo)
            if not path:
                continue

            addr = _read_dword(api, hdev, devinfo, bindings.SPDRP_ADDRESS)
            product = _read_text(api, hdev, devinfo, bindings.SPDRP_DEVICEDESC)
            if not product:
                product = _read_text(
                    api, hdev, devinfo, bindings.SPDRP_FRIENDLYNAME)

            # 复合设备的多个子接口会映射到同一设备，按 (vid,pid,addr) 去重
            key = (dev_vid, pid, addr)
            if key in seen:
                continue
            seen.add(key)
            out.append((DeviceInfo(vid=dev_vid, pid=pid, addr=addr,
                                   product=product), path))
        return out
    finally:
        api.destroy(hdev)


def enumerate_devices(vid=None):
    """返回 DeviceInfo 列表；无设备时返回空列表。"""
    return [info for info, _path in list_interfaces(vid)]


def find_path(info: DeviceInfo):
    """按 VID/PID（以及非零地址）找接口路径；未找到返回 None。"""
    for candidate, path in list_interfaces(info.vid):
        if candidate.pid != info.pid:
            continue
        if info.addr and candidate.addr != info.addr:
            continue
        return path
    return None
