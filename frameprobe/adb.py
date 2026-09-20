"""adb 封裝層。

這一層只負責「把指令送出去、把文字拿回來」，不做任何解析。
解析一律交給 timestats.py / probe.py，方便測試時替換成 fake。
"""

from __future__ import annotations

import re
import shutil
import subprocess
from dataclasses import dataclass, field
from typing import Protocol


class FrameprobeError(Exception):
    """所有本專案例外的基底。"""


class AdbNotFoundError(FrameprobeError):
    pass


class AdbCommandError(FrameprobeError):
    def __init__(self, argv: list[str], returncode: int, stdout: str, stderr: str) -> None:
        self.argv = argv
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr
        super().__init__(
            f"adb command failed (exit {returncode}): {' '.join(argv)}\n"
            f"--- stdout ---\n{stdout}\n--- stderr ---\n{stderr}"
        )


class AdbTimeoutError(FrameprobeError):
    pass


@dataclass(frozen=True)
class CommandResult:
    argv: list[str]
    returncode: int
    stdout: str
    stderr: str

    @property
    def ok(self) -> bool:
        return self.returncode == 0


class AdbClient(Protocol):
    """測試時用 fake 實作這個 Protocol。"""

    serial: str | None

    def shell(self, command: str, *, timeout: float = ...) -> CommandResult: ...
    def run(self, *args: str, timeout: float = ...) -> CommandResult: ...
    def getprop(self, key: str) -> str: ...
    def pull(self, remote: str, local: str, *, timeout: float = ...) -> CommandResult: ...


@dataclass
class Adb:
    """真正呼叫 adb 執行檔的實作。"""

    serial: str | None = None
    adb_path: str = field(default_factory=lambda: shutil.which("adb") or "")
    default_timeout: float = 20.0

    def __post_init__(self) -> None:
        if not self.adb_path:
            raise AdbNotFoundError(
                "找不到 adb 執行檔。請安裝 Android Platform Tools 並確認它在 PATH 中。"
            )

    # ------------------------------------------------------------------ #

    def _base_argv(self) -> list[str]:
        argv = [self.adb_path]
        if self.serial:
            argv += ["-s", self.serial]
        return argv

    def run(self, *args: str, timeout: float | None = None) -> CommandResult:
        argv = self._base_argv() + list(args)
        try:
            proc = subprocess.run(
                argv,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=timeout or self.default_timeout,
            )
        except subprocess.TimeoutExpired as exc:
            raise AdbTimeoutError(
                f"adb 指令逾時（{timeout or self.default_timeout}s）：{' '.join(argv)}"
            ) from exc
        return CommandResult(argv, proc.returncode, proc.stdout, proc.stderr)

    def run_checked(self, *args: str, timeout: float | None = None) -> CommandResult:
        result = self.run(*args, timeout=timeout)
        if not result.ok:
            raise AdbCommandError(result.argv, result.returncode, result.stdout, result.stderr)
        return result

    # ------------------------------------------------------------------ #

    def shell(self, command: str, *, timeout: float | None = None) -> CommandResult:
        return self.run("shell", command, timeout=timeout)

    def shell_checked(self, command: str, *, timeout: float | None = None) -> CommandResult:
        return self.run_checked("shell", command, timeout=timeout)

    def getprop(self, key: str) -> str:
        """取不到就回空字串。getprop 對不存在的 key 本來就回空，不是錯誤。"""
        result = self.shell(f"getprop {key}")
        return result.stdout.strip() if result.ok else ""

    def pull(self, remote: str, local: str, *, timeout: float | None = None) -> CommandResult:
        return self.run_checked("pull", remote, local, timeout=timeout or 120.0)

    # ------------------------------------------------------------------ #

    @staticmethod
    def list_devices(adb_path: str | None = None) -> list[dict[str, str]]:
        """回傳 [{'serial', 'state', 'model', 'transport': 'usb'|'wireless'|'unknown'}, ...]

        state 為 'device' 才是可用的；'unauthorized' 表示還沒在手機上按信任。
        """
        path = adb_path or shutil.which("adb")
        if not path:
            raise AdbNotFoundError("找不到 adb 執行檔。")

        proc = subprocess.run(
            [path, "devices", "-l"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=15.0,
        )
        return parse_devices_output(proc.stdout)


_WIRELESS_SERIAL_RE = re.compile(
    r"^(\d{1,3}(\.\d{1,3}){3}:\d+|\[?[0-9a-f:]+\]?:\d+|adb-.+\._adb-tls-connect\._tcp\.?)$"
)


def parse_devices_output(text: str) -> list[dict[str, str]]:
    """解析 `adb devices -l`。純函式。

    USB 裝置會帶 `usb:<bus-port>` 欄位；無線偵錯（adb pair / tcpip）沒有該欄位，
    序號是 `ip:port` 或 `adb-<id>._adb-tls-connect._tcp`。
    """
    devices: list[dict[str, str]] = []
    for line in text.splitlines()[1:]:
        line = line.strip()
        if not line or line.startswith("*"):
            continue
        parts = line.split()
        if len(parts) < 2:
            continue
        entry = {"serial": parts[0], "state": parts[1]}
        for token in parts[2:]:
            if ":" in token:
                key, _, value = token.partition(":")
                entry[key] = value
        if "usb" in entry:
            entry["transport"] = "usb"
        elif _WIRELESS_SERIAL_RE.match(entry["serial"]):
            entry["transport"] = "wireless"
        else:
            entry["transport"] = "unknown"
        devices.append(entry)
    return devices
