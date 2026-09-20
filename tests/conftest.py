from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

import pytest

from frameprobe.adb import CommandResult

FIXTURES = Path(__file__).parent / "fixtures"


def load(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


@pytest.fixture
def fixture_text():
    return load


@dataclass
class FakeAdb:
    """實作 AdbClient Protocol。以 regex → (stdout, returncode) 對照回應指令。"""

    serial: str | None = "FAKE"
    responses: dict[str, tuple[str, int]] = field(default_factory=dict)
    props: dict[str, str] = field(default_factory=dict)
    calls: list[str] = field(default_factory=list)

    def shell(self, command: str, *, timeout: float = 20.0) -> CommandResult:
        self.calls.append(command)
        if command.startswith("getprop "):
            return CommandResult(["adb"], 0, self.props.get(command[8:], ""), "")
        for pattern, (stdout, rc) in self.responses.items():
            if re.search(pattern, command):
                return CommandResult(
                    ["adb", "shell", command], rc, stdout, "" if rc == 0 else "err"
                )
        return CommandResult(["adb", "shell", command], 1, "", f"unknown command: {command}")

    def run(self, *args: str, timeout: float = 20.0) -> CommandResult:
        self.calls.append(" ".join(args))
        return CommandResult(["adb", *args], 0, "", "")

    def getprop(self, key: str) -> str:
        return self.props.get(key, "")

    def pull(self, remote: str, local: str, *, timeout: float = 120.0) -> CommandResult:
        return self.run("pull", remote, local)
