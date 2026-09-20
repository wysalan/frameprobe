"""電池電流／電壓／功耗與每幀能耗（FPower）。

來源優先序：
  1. /sys/class/power_supply/battery/{current_now,voltage_now}（kernel ABI：µA / µV；Pixel 可讀）
  2. `dumpsys battery`：AOSP 只印 voltage（mV）；Samsung ROM 額外印 `current now:`（mA）
     （Samsung 的 sysfs 對 shell 不可讀，真機驗證：S23）
兩者都是正值充電、負值放電。plugged 一律取自 dumpsys battery。

接 USB 時 current 是「充電器輸入 − 整機消耗」的淨值，不代表整機功耗；plugged 旗標要一路帶到 UI。
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass

from .adb import AdbClient, FrameprobeError


class PowerError(FrameprobeError):
    pass


POWER_COMMAND = (
    "echo '#sysfs'; cat /sys/class/power_supply/battery/current_now 2>&1; "
    "cat /sys/class/power_supply/battery/voltage_now 2>&1; "
    "echo '#dumpsys'; dumpsys battery"
)


_FIELD_RE = {
    "voltage_mv": re.compile(r"^\s*voltage:\s*(-?\d+)\s*$", re.MULTILINE),
    "current_ma": re.compile(r"^\s*current now:\s*(-?\d+)\s*$", re.MULTILINE),
    "level": re.compile(r"^\s*level:\s*(\d+)\s*$", re.MULTILINE),
    "charge_counter": re.compile(r"^\s*[Cc]harge counter:\s*(-?\d+)\s*$", re.MULTILINE),
    "status": re.compile(r"^\s*status:\s*(\d+)\s*$", re.MULTILINE),
}
_PLUG_RE = re.compile(r"^\s*(AC|USB|Wireless|Dock) powered:\s*(true|false)", re.MULTILINE)


@dataclass
class BatteryPower:
    ts: float
    voltage_v: float | None
    current_ma: float | None
    """正值充電、負值放電。ROM 沒印 current now 時為 None。"""
    plugged: bool
    level: int | None
    charge_counter_uah: int | None
    status: int | None = None
    """BatteryManager.BATTERY_STATUS_*：2 充電、3 放電、4 未充電、5 已充滿。"""
    source: str = "dumpsys"
    """'sysfs' | 'dumpsys'"""
    raw: str = ""

    @property
    def power_w(self) -> float | None:
        """V × I。正值代表淨充電，負值代表淨放電。"""
        if self.voltage_v is None or self.current_ma is None:
            return None
        return round(self.voltage_v * self.current_ma / 1000, 3)


def parse_dumpsys_battery(text: str, *, ts: float | None = None) -> BatteryPower:
    values: dict[str, int | None] = {}
    for key, regex in _FIELD_RE.items():
        match = regex.search(text)
        values[key] = int(match.group(1)) if match else None
    if values["voltage_mv"] is None:
        raise PowerError("dumpsys battery 沒有 voltage 欄位。")
    plugged = any(state == "true" for _, state in _PLUG_RE.findall(text))
    return BatteryPower(
        ts=ts or time.time(),
        voltage_v=values["voltage_mv"] / 1000,
        current_ma=float(values["current_ma"]) if values["current_ma"] is not None else None,
        plugged=plugged,
        level=values["level"],
        charge_counter_uah=values["charge_counter"],
        status=values["status"],
        raw=text,
    )


def _int_or_none(line: str) -> int | None:
    stripped = line.strip()
    return int(stripped) if stripped.lstrip("-").isdigit() else None


def parse_power(text: str, *, ts: float | None = None) -> BatteryPower:
    """解析 POWER_COMMAND 的合併輸出：sysfs 兩行可讀就用（µA/µV），否則改用 dumpsys battery。"""
    if "#dumpsys" not in text:  # 直接餵 dumpsys battery 全文也接受
        sysfs_part, dumpsys_part = "", text
    else:
        sysfs_part, _, dumpsys_part = text.partition("#dumpsys")
    sysfs_lines = [
        line for line in sysfs_part.splitlines() if line.strip() and line.strip() != "#sysfs"
    ]
    current_ua = _int_or_none(sysfs_lines[0]) if len(sysfs_lines) >= 1 else None
    voltage_uv = _int_or_none(sysfs_lines[1]) if len(sysfs_lines) >= 2 else None

    dumpsys: BatteryPower | None = None
    try:
        dumpsys = parse_dumpsys_battery(dumpsys_part, ts=ts)
    except PowerError:
        if current_ua is None or voltage_uv is None:
            raise

    if current_ua is not None and voltage_uv is not None:
        return BatteryPower(
            ts=ts or time.time(),
            voltage_v=voltage_uv / 1_000_000,
            current_ma=current_ua / 1000,
            plugged=dumpsys.plugged if dumpsys else False,
            level=dumpsys.level if dumpsys else None,
            charge_counter_uah=dumpsys.charge_counter_uah if dumpsys else None,
            status=dumpsys.status if dumpsys else None,
            source="sysfs",
            raw=text,
        )
    assert dumpsys is not None
    dumpsys.raw = text
    return dumpsys


class PowerCollector:
    def __init__(self, adb: AdbClient, *, min_interval: float = 2.0) -> None:
        self.adb = adb
        self.min_interval = min_interval
        self._last: BatteryPower | None = None
        self._last_fetch = 0.0
        self.failures = 0
        self.raw_snapshots: list[str] = []

    def read(self, *, force: bool = False) -> BatteryPower | None:
        now = time.time()
        if not force and self._last and (now - self._last_fetch) < self.min_interval:
            return self._last
        result = self.adb.shell(POWER_COMMAND, timeout=15.0)
        if not result.ok or not result.stdout.strip():
            self.failures += 1
            return self._last
        self.raw_snapshots.append(result.stdout)
        try:
            self._last = parse_power(result.stdout)
            self._last_fetch = now
            self.failures = 0
        except PowerError:
            self.failures += 1
        return self._last
