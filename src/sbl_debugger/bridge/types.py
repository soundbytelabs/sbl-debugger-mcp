"""Typed wrappers for GDB/MI responses."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class FrameInfo:
    """A stack frame from GDB."""

    func: str = "??"
    file: str | None = None
    line: int | None = None
    address: str | None = None

    def to_dict(self) -> dict:
        result: dict = {"func": self.func}
        if self.address is not None:
            result["address"] = self.address
        if self.file is not None:
            result["file"] = self.file
        if self.line is not None:
            result["line"] = self.line
        return result

    @classmethod
    def from_mi(cls, data: dict) -> FrameInfo:
        """Parse a GDB/MI frame dict (all string values)."""
        return cls(
            func=data.get("func", "??"),
            file=data.get("fullname") or data.get("file"),
            line=int(data["line"]) if "line" in data else None,
            address=data.get("addr"),
        )


@dataclass(frozen=True)
class StopEvent:
    """Target stopped event from GDB."""

    reason: str
    frame: FrameInfo | None = None

    def to_dict(self) -> dict:
        result: dict = {"reason": self.reason}
        if self.frame is not None:
            result["frame"] = self.frame.to_dict()
        return result

    @classmethod
    def from_mi(cls, payload: dict) -> StopEvent:
        """Parse a *stopped MI notification payload."""
        reason = payload.get("reason", "unknown")
        frame = None
        if "frame" in payload:
            frame = FrameInfo.from_mi(payload["frame"])
        return cls(reason=reason, frame=frame)


@dataclass
class MiResult:
    """Parsed result of a GDB/MI command."""

    message: str  # "done", "running", "error", etc.
    payload: dict | list | str | None = None
    console_output: list[str] = field(default_factory=list)
    events: list[dict] = field(default_factory=list)

    @property
    def is_error(self) -> bool:
        return self.message == "error"

    @property
    def error_msg(self) -> str | None:
        if self.is_error and isinstance(self.payload, dict):
            return self.payload.get("msg", str(self.payload))
        return None

    @classmethod
    def from_responses(cls, responses: list[dict]) -> MiResult:
        """Parse a list of pygdbmi response dicts into a typed result."""
        result_msg = "done"
        result_payload = None
        console: list[str] = []
        events: list[dict] = []

        for r in responses:
            rtype = r.get("type")
            if rtype == "result":
                result_msg = r.get("message", "done")
                result_payload = r.get("payload")
            elif rtype == "notify":
                events.append(r)
            elif rtype == "console":
                text = r.get("payload", "")
                if text:
                    console.append(text.rstrip("\n"))

        return cls(
            message=result_msg,
            payload=result_payload,
            console_output=console,
            events=events,
        )
