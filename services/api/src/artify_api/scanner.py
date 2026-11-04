"""Malware scanning interface. Scanning is OFF unless configured; the system never claims otherwise.

`scan_status` on a version is `not_scanned` when scanning is off, `clean` / `infected` after a real scan, and `pending`
when scanning is required but the scanner is unavailable (the file is then quarantined, never served)."""

from __future__ import annotations

import socket
import struct
from typing import BinaryIO, Protocol

from artify_api.settings import get_settings


class ScanUnavailable(Exception):
    pass


class Scanner(Protocol):
    name: str

    def scan(self, fh: BinaryIO) -> tuple[bool, str | None]:
        """Return (clean, signature). Raise ScanUnavailable when the scanner cannot answer."""


class ClamdScanner:
    """ClamAV daemon over the INSTREAM protocol."""

    name = "clamd"

    def __init__(self, host: str, port: int, timeout: float = 60.0) -> None:
        self.host, self.port, self.timeout = host, port, timeout

    def scan(self, fh: BinaryIO) -> tuple[bool, str | None]:
        try:
            with socket.create_connection((self.host, self.port), timeout=self.timeout) as s:
                s.sendall(b"zINSTREAM\0")
                fh.seek(0)
                while chunk := fh.read(64 * 1024):
                    s.sendall(struct.pack("!I", len(chunk)) + chunk)
                s.sendall(struct.pack("!I", 0))
                reply = b""
                while not reply.endswith(b"\0") and (part := s.recv(4096)):
                    reply += part
        except OSError as exc:
            raise ScanUnavailable("the malware scanner is not reachable") from exc
        text = reply.rstrip(b"\0\n").decode("utf-8", "replace")
        if text.endswith("OK"):
            return True, None
        if text.endswith("FOUND"):
            return False, text.removeprefix("stream: ").removesuffix(" FOUND")
        raise ScanUnavailable("the malware scanner returned an error")


_scanner: Scanner | None = None
_override = False


def get_scanner() -> Scanner | None:
    if _override:
        return _scanner
    s = get_settings()
    if s.malware_scan == "off":
        return None
    return ClamdScanner(s.clamd_host, s.clamd_port)


def set_scanner(scanner: Scanner | None) -> None:
    """Tests inject a scanner; call set_scanner(None) then reset_scanner() to restore configuration."""
    global _scanner, _override
    _scanner, _override = scanner, True


def reset_scanner() -> None:
    global _scanner, _override
    _scanner, _override = None, False
