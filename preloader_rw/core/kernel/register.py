# -*- coding: utf-8 -*-
"""路线阶段：执行路线载荷，然后**用探针裁决**它是否真的注册成功。

为什么不能信载荷自述：

  · `session.loop_entered` 是载荷调 `enter_loop()` 时自己写的 bool，
    不能证明设备真的进了命令循环；
  · 载荷内部的 try/except 会把中途异常吞掉（`payloads/route.py` 的
    READ-PARTITION-TABLE 就是这种：失败只记 warn 继续），于是
    「载荷跑完了」≠「设备真的进了命令循环」。

裁决方式：跑**表里 probe 段声明**的探针，至少一条成立即通过。探针是数据，
不是代码——换一份 DA 固件只换表里的 `probe`，本文件一行不改。

判据只认设备自己的表态：回合尾 `CMD:END result=OK`。主机不推断、不看
`enter_loop`、不看任何"载荷说它成功了"。

默认表的探针是 `GET-HW-INFO`（设备回 484 B 的 HW 信息 XML），它排在认证轮
之后仍能完整走完。`GET-DA-INFO` 没有进默认表：部分平台上紧跟其后发出时
设备不应答，探针自身不可靠时，「注册失败」与「探针有问题」就无法区分。

严禁用 `GET-UFS-INFO` 做探针：部分平台上该命令自身有缺陷，失败时无法
区分「注册没成功」与「这条命令自身不可用」，用它做判据得不到有效结论。

内核不认识路线语义：本文件只知道「表里哪几条命令可以当探针」这份数据。
"""

import os

from .loader import load

PROBE_DIR = "downloads"

# 设备「回合有明确结果」的取值；判据只认这一个。
OK_RESULT = "OK"


class RegisterError(IOError):
    pass


class RegisterPhase(object):
    """跑路线载荷，然后用表里的探针裁决注册是否真的成立。"""

    def __init__(self, session, lease, log=None, probes=None,
                 save_dir=PROBE_DIR, table=None):
        self.session = session
        self.lease = lease
        self.log = log or (lambda level, text: None)
        self.table = table if table is not None else getattr(session, "table", None)
        if probes is None:
            probes = self.table.probes if self.table is not None else ()
        self.probes = tuple(probes)
        self.save_dir = save_dir
        # 每一次探针的一行结果，供调用方事后查看（不含判据逻辑）。
        self.last_detail = ""

    def run(self, route_path):
        route = load(route_path)
        try:
            size = os.path.getsize(route_path)
        except OSError:
            size = 0
        self.log("info", "加载路线载荷 size=%d" % size)
        route.run(self.session)
        # 路线里只要有一条**受限命令**完整走完（收到 CMD:END result=OK），
        # 就说明命令表确实被打开了。此时**不再重发探针**：
        # 部分平台上同一条命令短时间内重发可能得不到应答，拿重复发送去换
        # 一个已经拿到的结论没有收益。
        self.log("info", "载荷自述 loop_entered=%r loop_verified=%r"
                 % (getattr(self.session, "loop_entered", False),
                    getattr(self.session, "loop_verified", False)))
        if getattr(self.session, "loop_verified", False):
            self.last_detail = ("注册判据：路线内受限命令已收到 CMD:END result=OK"
                                "（设备回合已确认，无需重发探针）")
            self.log("ok", self.last_detail)
            self.lease.enter_gated()
            return True
        if not self.probes:
            raise RegisterError(
                "注册失败：路线内没有一条受限命令走完，表 %s 又没声明探针"
                "（probe 段）⇒ 无法确认命令面已打通，拒绝进入命令循环"
                % (getattr(self.table, "name", "?"),))
        passed, detail = self.probe()
        self.last_detail = detail
        self.log("ok" if passed else "err", detail)
        if not passed:
            raise RegisterError(
                "注册失败：表里的探针（%s）无一收到 CMD:END result=OK ⇒ 命令面未打通，"
                "拒绝进入命令循环"
                % ", ".join(probe.command for probe in self.probes))
        self.lease.enter_gated()
        return True

    # ---- 探针裁决 ---------------------------------------------------

    def probe(self):
        """跑表里的探针；返回 (是否通过, 明细串)。

        判据：**至少一条**探针的回合尾拿到 `CMD:END result=OK`。
        任一条抛异常（设备静默 / 传输异常）不算通过，继续试下一条——
        全部失败才判注册失败。
        """
        details = []
        for probe in self.probes:
            ok, line = self._one(probe)
            details.append(line)
            if ok:
                others = "; ".join(details) if len(details) > 1 else ""
                return True, ("注册探针通过：%s%s"
                              % (line, ("（同轮其它探针：%s）" % others) if others else ""))
        return False, "注册探针全部失败：%s" % "; ".join(details)

    def _one(self, probe):
        """跑一条探针；返回 (是否通过, 一行明细)。"""
        filename = probe.save_as or ("probe_%s.bin"
                                     % probe.command.lower().replace(":", "_"))
        save = os.path.join(self.save_dir, filename) if self.save_dir else ""
        if self.save_dir:
            try:
                os.makedirs(self.save_dir, exist_ok=True)
            except OSError:
                pass
        try:
            result = self.session.command(probe.command,
                                          {"target_file": probe.target},
                                          save_path=save)
        except Exception as exc:
            return False, "%s 探针异常: %r" % (probe.command, exc)
        text = (getattr(result, "result", "") or "").strip()
        moved = int(getattr(result, "moved", 0) or 0)
        if text.upper() != OK_RESULT:
            gap = getattr(self.session, "last_round_gap", "") or ""
            return False, ("%s 回合尾 result=%r%s ⇒ 未收到 CMD:END result=OK"
                           % (probe.command, text or "(无 END)",
                              ("，" + gap) if gap else ""))
        note = ""
        if probe.expect and moved and moved != probe.expect:
            # 字节数不是判据：只记下来，供比对设备固件差异。
            note = "，实收 %d B（表里记 %d B）" % (moved, probe.expect)
        elif probe.expect:
            note = "，%d B" % (moved or probe.expect)
        return True, "%s result=OK%s" % (probe.command, note)
