# -*- coding: utf-8 -*-
"""产品层注册表：把官方路线装配到 dispatcher。

装配顺序固定：
  1. 官方命令规格（commands + read）；
  2. 官方上行 Provider（写、读）。
"""

from ..engine.dispatcher import Dispatcher
from .official import commands as official_commands
from .official.read import ReadProvider, read_specs
from .official.write import WriteProvider

__all__ = ["build_registry"]


class _CommandNames(tuple):
    """既是元组也可调用的命令名视图。

    engine.Dispatcher 把 command_names 定义为 property；路线层的自检与
    外部调用同时存在 `d.command_names` 与 `d.command_names()` 两种写法。
    本类型只补调用形态，不改 engine 的任何行为。
    """

    def __call__(self):
        return tuple(self)


class Registry(Dispatcher):
    """product 装配出的 dispatcher；command_names 兼容属性与调用两种形态。"""

    @property
    def command_names(self):
        return _CommandNames(Dispatcher.command_names.fget(self))


def build_registry(holder=None):
    """构建一个全新的 dispatcher；每次调用都返回独立实例。

    holder 可以是 PartitionTableHolder（运行期：表由 READ-FLASH 直读 GPT
    后写入）或 PartitionTable（离线已知表）。为 None 时写命令会在 arm 阶段
    被拒绝。装配不再依赖表是否已就绪。
    """
    dispatcher = Registry()

    write_provider = WriteProvider(holder)
    read_provider = ReadProvider()
    table_holder = write_provider.holder

    for spec in official_commands.specs(write_provider):
        dispatcher.register_command(spec)
    for spec in read_specs(read_provider, table_holder):
        dispatcher.register_command(spec)

    # 写 Provider 先注册：它只在写命令活动期内命中，其余请求落到读 Provider。
    dispatcher.register_provider(write_provider)
    dispatcher.register_provider(read_provider)

    return dispatcher
