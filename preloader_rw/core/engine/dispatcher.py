# -*- coding: utf-8 -*-
"""唯一路由。

上行：按注册顺序问 Provider，第一个 match 命中者负责 serve。
下行：按 name 查 CommandSpec。
重复注册一律抛错，不允许后注册者覆盖先注册者。
"""

from .command import CommandSpec
from .provider import Provider, Request


class DispatchError(Exception):
    """路由错误基类。"""


class DuplicateRegistration(DispatchError):
    """重复注册。"""


class UnknownCommand(DispatchError):
    """下行查不到 CommandSpec。"""


class Dispatcher(object):
    def __init__(self):
        self._providers = []
        self._commands = {}

    # ---- 注册 ----

    def register_provider(self, provider):
        if not callable(getattr(provider, "match", None)):
            raise TypeError("Provider 缺少 match(): %r" % (provider,))
        if not callable(getattr(provider, "serve", None)):
            raise TypeError("Provider 缺少 serve(): %r" % (provider,))
        for existing in self._providers:
            if existing is provider:
                raise DuplicateRegistration("Provider 重复注册: %r" % (type(provider).__name__,))
        self._providers.append(provider)

    def register_command(self, spec):
        if not isinstance(spec, CommandSpec):
            raise TypeError("register_command 只接受 CommandSpec，收到 %r" % (type(spec).__name__,))
        if spec.name in self._commands:
            raise DuplicateRegistration("CommandSpec 重复注册: %r" % (spec.name,))
        self._commands[spec.name] = spec

    # ---- 上行 ----

    def route(self, request):
        # type: (Request) -> Provider | None
        for provider in self._providers:
            if provider.match(request):
                return provider
        return None

    def serve(self, provider, request):
        # type: (Provider, Request) -> object | None
        return provider.serve(request)

    # ---- 下行 ----

    def command(self, name):
        # type: (str) -> CommandSpec
        try:
            return self._commands[name]
        except KeyError:
            raise UnknownCommand("未注册的命令: %r" % (name,))

    # ---- 只读视图 ----

    @property
    def providers(self):
        return tuple(self._providers)

    @property
    def command_names(self):
        return tuple(self._commands.keys())
