# -*- coding: utf-8 -*-
"""默认时钟：实机用单调时钟。回放时可替换为虚拟时钟。"""

import time


class SystemClock(object):
    def now(self):
        return time.monotonic()

    def sleep(self, seconds):
        if seconds > 0:
            time.sleep(seconds)
