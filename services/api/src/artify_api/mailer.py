"""Outbound email abstraction. Phase 1 ships console/memory backends only; a real provider
adapter is wired in when hosting is chosen (decision D-3)."""

from __future__ import annotations

import logging
import smtplib
import ssl
from dataclasses import dataclass, field
from email.message import EmailMessage
from typing import Any, Protocol

from artify_api.settings import Settings, get_settings

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


_TEMPLATES: dict[str, tuple[str, str]] = {
    "verify_email": (
        "Verify your email address",
        "Welcome to Artify Co-Accountant.\n\nConfirm your email address to finish creating your account:\n{base}/verify-email?token={token}\n\nIf you did not create an account you can ignore this message.\n",
    ),
    "password_reset": (
        "Reset your password",
        "We received a request to reset your Artify password.\n\nChoose a new password within one hour:\n{base}/reset-password?token={token}\n\nIf you did not request this you can ignore this message; your password is unchanged.\n",
    ),
    "invitation": (
        "You have been invited to Artify Co-Accountant",
        "You have been invited to join an organization on Artify Co-Accountant.\n\nAccept the invitation:\n{base}/accept-invitation?token={token}\n\nThe link expires in seven days.\n",
    ),
    "platform_invitation": (
        "You have been invited to Artify Super Admin",
        "You have been invited to administer the Artify Co-Accountant platform.\n\nAccept the invitation and set your password:\n{base}/platform/accept-invitation?token={token}\n\nYou will be asked to set up two-factor authentication at first sign-in. The link expires in seven days.\n",
    ),
    "support_access_request": (
        "Artify support is requesting access to {organization}",
        "An Artify support administrator has asked for time-limited access to {organization}.\n\nReason given: {reason}\n\nNothing is shared unless you approve. Review the request in Support access:\n{base}/support-access\n\nYou can deny or revoke access at any time.\n",
    ),
    "signup_existing_account": (
        "Sign-up attempt for an existing account",
        "Someone tried to create an Artify account with this email address, which already has one.\n\nSign in: {base}/login\nForgot your password: {base}/forgot-password\n\nIf this was not you, no action is needed.\n",
    ),
}


class SmtpMailer:
    """SMTP delivery on a small background pool: a request never waits on the mail server.

    LIMITATION: delivery is best-effort and in-process. A crash can lose queued messages; failures
    are retried a few times and then logged (without content). Durable delivery through the
    transactional outbox is planned with the notification framework (Phase 3)."""

    def __init__(self, settings: Settings) -> None:
        from concurrent.futures import ThreadPoolExecutor

        self._s = settings
        self._pool = ThreadPoolExecutor(max_workers=2, thread_name_prefix="smtp")
        self._pending: list[Any] = []

    def build(self, message: Message) -> EmailMessage:
        subject, body = _TEMPLATES[message.template]
        values = {"token": "", **{k: str(v) for k, v in message.context.items()}}
        mail = EmailMessage()
        mail["From"] = self._s.mail_from
        mail["To"] = message.to
        mail["Subject"] = subject.format(**values)
        mail.set_content(body.format(base=self._s.web_base_url.rstrip("/"), **values))
        return mail

    def send(self, message: Message) -> None:
        mail = self.build(message)
        self._pending.append(self._pool.submit(self._deliver, mail, message.template))

    def _deliver(self, mail: EmailMessage, template: str) -> None:
        import time

        last: Exception | None = None
        for attempt in range(3):
            try:
                with smtplib.SMTP(self._s.smtp_host or "", self._s.smtp_port, timeout=10) as smtp:
                    if self._s.smtp_starttls:
                        smtp.starttls(context=ssl.create_default_context())
                    if self._s.smtp_username and self._s.smtp_password:
                        smtp.login(self._s.smtp_username, self._s.smtp_password.get_secret_value())
                    smtp.send_message(mail)
                return
            except Exception as exc:
                last = exc
                time.sleep(0.5 * (attempt + 1))
        log.error("mail delivery failed template=%s error=%s", template, type(last).__name__)

    def flush(self, timeout: float = 10.0) -> None:
        """Wait for queued messages (tests and graceful shutdown)."""
        from concurrent.futures import wait

        wait(self._pending, timeout=timeout)
        self._pending.clear()


_mailer: Mailer | None = None


def get_mailer() -> Mailer:
    global _mailer
    if _mailer is None:
        settings = get_settings()
        if settings.mailer_backend == "smtp":
            _mailer = SmtpMailer(settings)
        elif settings.mailer_backend == "memory":
            _mailer = MemoryMailer()
        else:
            _mailer = ConsoleMailer()
    return _mailer


def set_mailer(mailer: Mailer | None) -> None:
    global _mailer
    _mailer = mailer
