# -*- coding: utf-8 -*-
"""运行期输入：只读 preflight 已登记的 kind，不碰文件系统。"""

from preloader_rw.core.kernel.api import (HostInputCancelled, HostInputTimeout,
                              HostInputUnavailable)

__all__ = ["RuntimeInput", "HostInputTimeout", "HostInputCancelled",
           "HostInputUnavailable"]


class RuntimeInput(object):
    def __init__(self, bundle):
        self._bundle = bundle
        # 运行时索取回调：由壳装配（向 GUI/JSON 宿主发事件并等待应答）。
        self.on_request = None

    def resolve(self, kind):
        return self._bundle.credential(kind)

    def request(self, kind, info):
        """载荷在运行途中索取输入（如签名应答）；阻塞到宿主回应。

        壳没接宿主时立即抛错——不许退化成「拿不到就静默继续」。
        """
        if self.on_request is None:
            raise HostInputUnavailable("没有可用的宿主输入通道（kind=%r）" % (kind,))
        return self.on_request(kind, info)
