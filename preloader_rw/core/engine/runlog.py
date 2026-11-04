# -*- coding: utf-8 -*-
"""运行记录：run 目录、raw.log、verdict.txt。

verdict.txt 的字段名、顺序与取值口径与现有 runs\\<时间戳>\\verdict.txt 保持一致。
"""

from datetime import datetime
from pathlib import Path

from preloader_rw import REPO_ROOT

# 判值阈值沿用现有口径：发满 5 轮即视为节拍可选
GOOD_ROUNDS = 5


def new_run_dir(runs_root=None, stamp=None):
    """创建 runs\\<YYYYmmdd_HHMMSS>\\ 并返回路径。stamp 仅供离线测试注入。"""
    root = Path(runs_root) if runs_root is not None else Path(REPO_ROOT) / "runs"
    name = stamp if stamp is not None else datetime.now().strftime("%Y%m%d_%H%M%S")
    path = root / name
    path.mkdir(parents=True, exist_ok=True)
    return path


class RawLog(object):
    """raw.log 写入器：一行一条，写后刷新。"""

    def __init__(self, run_dir, name="raw.log"):
        self.path = Path(run_dir) / name
        self._fh = open(str(self.path), "w", encoding="utf-8", newline="\n")
        self._closed = False

    def write(self, text):
        if self._closed:
            raise ValueError("RawLog 已关闭")
        line = text if text.endswith("\n") else text + "\n"
        self._fh.write(line)
        self._fh.flush()

    def flush(self):
        if not self._closed:
            self._fh.flush()

    def close(self):
        if not self._closed:
            self._fh.close()
            self._closed = True

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        self.close()
        return False


def _hex_or_text(value):
    if value is None:
        return ""
    if isinstance(value, (bytes, bytearray)):
        return bytes(value).hex()
    return str(value)


def write_verdict(run_dir, state, sent, total, route="official", flash="on",
                  read="", ep_halts=0, post_probe="none", da1_ack="",
                  d2h_ok=0, probe="", sec_verify="", dialog_frames=0,
                  good_rounds=GOOD_ROUNDS):
    """写 verdict.txt，返回 verdict 值。字段顺序与现有 runs 目录一致。"""
    if sent >= good_rounds:
        verdict = "BEAT_OPTIONAL"
    elif sent > 0:
        verdict = "BEAT_SENSITIVE"
    else:
        verdict = "NO_ROUND"

    lines = (
        ("state", state),
        ("route", route or "official"),
        ("flash", flash),
        ("read", read or ""),
        ("ep_halts", "%d" % ep_halts),
        ("post_probe", post_probe),
        ("rounds", "%d/%d" % (sent, total)),
        ("good_rounds", "%d" % good_rounds),
        ("da1_ack", _hex_or_text(da1_ack)),
        ("d2h_ok", "%d" % d2h_ok),
        ("probe", probe or ""),
        ("sec_verify", sec_verify or ""),
        ("dialog_frames", "%d" % dialog_frames),
        ("verdict", verdict),
    )

    path = Path(run_dir) / "verdict.txt"
    with open(str(path), "w", encoding="utf-8", newline="\n") as fh:
        for key, value in lines:
            fh.write("%s=%s\n" % (key, value))
    return verdict
