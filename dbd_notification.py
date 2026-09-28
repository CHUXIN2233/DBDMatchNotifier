from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Protocol

from dbd_runtime import safe_print


@dataclass(frozen=True)
class MatchEvent:
    timestamp: float
    udp_ports: tuple[str, ...]
    waited_seconds: float = 0.0


class NotificationSink(Protocol):
    def notify_match(self, event: MatchEvent) -> None:
        """Deliver one match event to a notification backend."""


class LogNotificationSink:
    """Local sink used during validation; replace/add a mobile sink later."""

    def __init__(self, path: Path):
        self.path = path

    def notify_match(self, event: MatchEvent) -> None:
        stamp = datetime.fromtimestamp(event.timestamp).strftime("%H:%M:%S")
        ports = ", ".join(event.udp_ports)
        line = f"[{stamp}] MATCH DETECTED - UDP {ports}"
        safe_print(line, flush=True)
        with self.path.open("a", encoding="utf-8") as fh:
            fh.write(line + "\n")


class CompositeNotificationSink:
    def __init__(self, *sinks: NotificationSink):
        self.sinks = sinks

    def notify_match(self, event: MatchEvent) -> None:
        for sink in self.sinks:
            sink.notify_match(event)
