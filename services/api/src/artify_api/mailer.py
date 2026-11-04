"""Outbound email abstraction. Phase 1 ships console/memory backends only; a real provider
adapter is wired in when hosting is chosen (decision D-3)."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Protocol

from artify_api.settings import get_settings

log = logging.getLogger("artify.mail")


@dataclass
class Message:
    to: str
    template: str
    context: dict[str, Any] = field(default_factory=dict)


class Mailer(Protocol):
    def send(self, message: Message) -> None: ...


class ConsoleMailer:
    def send(self, message: Message) -> None:
        # Token-bearing context is only printed in local development.
        if get_settings().environment == "local":
            log.warning("MAIL to=%s template=%s context=%s", message.to, message.template, message.context)
        else:
            log.info("MAIL queued template=%s", message.template)


class MemoryMailer:
    def __init__(self) -> None:
        self.sent: list[Message] = []

    def send(self, message: Message) -> None:
        self.sent.append(message)

    def last(self, template: str, to: str | None = None) -> Message:
        for m in reversed(self.sent):
            if m.template == template and (to is None or m.to.lower() == to.lower()):
                return m
        raise LookupError(f"no {template} mail for {to}")


_mailer: Mailer | None = None


def get_mailer() -> Mailer:
    global _mailer
    if _mailer is None:
        _mailer = MemoryMailer() if get_settings().mailer_backend == "memory" else ConsoleMailer()
    return _mailer


def set_mailer(mailer: Mailer | None) -> None:
    global _mailer
    _mailer = mailer
