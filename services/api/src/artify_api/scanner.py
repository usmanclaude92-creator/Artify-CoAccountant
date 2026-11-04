"""Malware-scanner interface (docs/documents/STORAGE_SECURITY.md).

Honesty rule: a document is only ever shown as "clean" when a scanner actually examined it. With the default
`NoopScanner`, versions are recorded as `not_scanned` and the product says so. The ClamAV client speaks the clamd
INSTREAM protocol; it is tested against a protocol-faithful fake server, not against a real ClamAV engine.
"""

from __future__ import annotations

import socket
import struct
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from artify_api.settings import Settings, get_settings


@dataclass(frozen=True)
class ScanResult:
    status: str  # clean | infected | error | not_scanned
    detail: str | None = None


class Scanner(Protocol):
    name: str
    active: bool

    def scan_file(self, path: Path) -> ScanResult: ...


class NoopScanner:
    name = "none"
    active = False

    def scan_file(self, path: Path) -> ScanResult:
        return ScanResult("not_scanned", "No malware scanner is configured")


class ClamdScanner:
    """clamd INSTREAM client: length-prefixed chunks, a zero-length terminator, one reply line."""

    name = "clamav"
    active = True

    def __init__(self, host: str, port: int, timeout: float = 30.0) -> None:
        self.host, self.port, self.timeout = host, port, timeout

    def scan_file(self, path: Path) -> ScanResult:
        try:
            with socket.create_connection((self.host, self.port), timeout=self.timeout) as s:
                s.settimeout(self.timeout)
                s.sendall(b"zINSTREAM\0")
                with path.open("rb") as f:
                    while chunk := f.read(64 * 1024):
                        s.sendall(struct.pack("!I", len(chunk)) + chunk)
                s.sendall(struct.pack("!I", 0))
                reply = b""
                while not reply.endswith(b"\0") and (part := s.recv(4096)):
                    reply += part
        except OSError as exc:
            return ScanResult("error", f"scanner unreachable: {type(exc).__name__}")
        text = reply.rstrip(b"\0\n").decode("utf-8", "replace")
        if text.endswith("OK"):
            return ScanResult("clean")
        if text.endswith("FOUND"):
            return ScanResult("infected", text.removeprefix("stream: ").removesuffix(" FOUND"))
        return ScanResult("error", text or "empty scanner reply")


_scanner: Scanner | None = None


def build_scanner(s: Settings) -> Scanner:
    if s.scanner_backend == "clamav":
        return ClamdScanner(s.clamd_host, s.clamd_port, s.clamd_timeout_seconds)
    return NoopScanner()


def get_scanner() -> Scanner:
    global _scanner
    if _scanner is None:
        _scanner = build_scanner(get_settings())
    return _scanner


def set_scanner(scanner: Scanner | None) -> None:
    """Test hook."""
    global _scanner
    _scanner = scanner
