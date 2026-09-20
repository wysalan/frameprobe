"""溫度與熱節流狀態採集。

這個模組存在的理由不只是「顯示溫度好看」：遊戲 FPS 掉下來的最常見原因就是
熱節流，而節流是漸進的、看 FPS 曲線本身看不出成因。把 Thermal Status 疊在
FPS 曲線上，掉幀是「這段場景太重」還是「裝置熱了」一眼就分得出來。

資料來源優先序（SPEC §12.1）：
  1. dumpsys thermalservice  — 最完整，有分類溫度 + 節流狀態，不需 root
  2. dumpsys hardware_properties — 舊機型備援
  3. /sys/class/thermal/thermal_zone*/ — 最後手段，單位需要猜
  4. dumpsys battery — 一定拿得到，但只有電池溫度

解析為純函式，取樣器負責 I/O。
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass, field
from enum import IntEnum
from typing import Any

from .adb import AdbClient, FrameprobeError


class ThermalError(FrameprobeError):
    pass


# --------------------------------------------------------------------------- #
# 列舉
# --------------------------------------------------------------------------- #


class ThrottlingStatus(IntEnum):
    """對應 android.os.PowerManager.THERMAL_STATUS_*。

    >= MODERATE 時系統開始實質降頻，這是解讀 FPS 下滑的關鍵訊號。
    """

    NONE = 0
    LIGHT = 1
    MODERATE = 2
    SEVERE = 3
    CRITICAL = 4
    EMERGENCY = 5
    SHUTDOWN = 6

    @property
    def is_throttling(self) -> bool:
        return self >= ThrottlingStatus.MODERATE

    @property
    def label(self) -> str:
        return self.name

    @classmethod
    def parse(cls, value: int) -> ThrottlingStatus | None:
        try:
            return cls(value)
        except ValueError:
            return None


# android.os.Temperature.TYPE_* 的對照。
# 10 以上的型別是較新版本才加入的，若遇到未知值一律回退成 TYPE_<n>，
# 不要因為對照表沒涵蓋就丟掉這筆讀數。
_TEMPERATURE_TYPES: dict[int, str] = {
    -1: "UNKNOWN",
    0: "CPU",
    1: "GPU",
    2: "BATTERY",
    3: "SKIN",
    4: "USB_PORT",
    5: "POWER_AMPLIFIER",
    6: "BCL_VOLTAGE",
    7: "BCL_CURRENT",
    8: "BCL_PERCENTAGE",
    9: "NPU",
    10: "TPU",
    11: "DISPLAY",
    12: "MODEM",
    13: "SOC",
}

# 這些型別不是溫度（是電池電流／電壓／百分比），不可當成攝氏度顯示
_NON_TEMPERATURE_TYPES = {6, 7, 8}

#: 攝氏度的合理範圍。超出此範圍視為感測器無效讀數（例如 Samsung 常見的 0.0）。
TEMP_MIN_C = 1.0
TEMP_MAX_C = 150.0


# --------------------------------------------------------------------------- #
# 資料結構
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class TemperatureReading:
    name: str
    """感測器名稱，例如 cpu_thermal_zone、AP、BAT。各家 ROM 命名不一致。"""

    type_id: int
    celsius: float
    status: int = 0

    @property
    def type_name(self) -> str:
        return _TEMPERATURE_TYPES.get(self.type_id, f"TYPE_{self.type_id}")

    @property
    def is_temperature(self) -> bool:
        """BCL 類型雖然也走 Temperature 結構，但單位不是攝氏度。"""
        return self.type_id not in _NON_TEMPERATURE_TYPES

    @property
    def is_plausible(self) -> bool:
        return self.is_temperature and TEMP_MIN_C <= self.celsius <= TEMP_MAX_C


@dataclass
class ThermalSnapshot:
    ts: float
    source: str
    """'thermalservice' | 'hardware_properties' | 'sysfs' | 'battery'"""

    status: ThrottlingStatus | None = None
    readings: list[TemperatureReading] = field(default_factory=list)
    raw: str = ""

    # ------------------------------------------------------------------ #

    def by_type(self, type_id: int) -> list[TemperatureReading]:
        return [r for r in self.readings if r.type_id == type_id and r.is_plausible]

    def peak(self, type_id: int) -> float | None:
        """同一類型可能有多顆感測器（例如 8 個 CPU cluster），取最高的。

        取最高而非平均，是因為節流是由最熱的那顆觸發的。
        """
        values = [r.celsius for r in self.by_type(type_id)]
        return max(values) if values else None

    @property
    def cpu_c(self) -> float | None:
        return self.peak(0)

    @property
    def gpu_c(self) -> float | None:
        return self.peak(1)

    @property
    def battery_c(self) -> float | None:
        return self.peak(2)

    @property
    def skin_c(self) -> float | None:
        """機身表面溫度。使用者實際摸到的溫度，也是多數 OEM 節流的依據。"""
        return self.peak(3)

    @property
    def npu_c(self) -> float | None:
        return self.peak(9)

    @property
    def hottest(self) -> TemperatureReading | None:
        plausible = [r for r in self.readings if r.is_plausible]
        return max(plausible, key=lambda r: r.celsius) if plausible else None

    def to_dict(self) -> dict[str, Any]:
        return {
            "ts": self.ts,
            "source": self.source,
            "status": int(self.status) if self.status is not None else None,
            "status_label": self.status.label if self.status is not None else None,
            "throttling": self.status.is_throttling if self.status is not None else None,
            "cpu_c": self.cpu_c,
            "gpu_c": self.gpu_c,
            "battery_c": self.battery_c,
            "skin_c": self.skin_c,
            "npu_c": self.npu_c,
            "readings": [
                {
                    "name": r.name,
                    "type": r.type_name,
                    "type_id": r.type_id,
                    "celsius": r.celsius,
                }
                for r in self.readings
                if r.is_plausible
            ],
        }


# --------------------------------------------------------------------------- #
# 解析器（純函式）
# --------------------------------------------------------------------------- #

_TEMPERATURE_RE = re.compile(
    r"Temperature\{"
    r"mValue=(?P<value>-?[\d.]+),\s*"
    r"mType=(?P<type>-?\d+),\s*"
    r"mName=(?P<name>[^,}]*?),\s*"
    r"mStatus=(?P<status>-?\d+)"
    r"\}"
)
_THERMAL_STATUS_RE = re.compile(r"^\s*Thermal Status:\s*(-?\d+)\s*$", re.MULTILINE)


def parse_thermalservice(text: str, *, ts: float | None = None) -> ThermalSnapshot:
    """解析 `dumpsys thermalservice`。

    輸出大致長這樣（實際會更長）：

        IsStatusOverride: false
        Thermal Status: 0
        Cached temperatures:
            Temperature{mValue=30.8, mType=3, mName=LRB, mStatus=0}
        HAL Ready: true
        Current temperatures from HAL:
            Temperature{mValue=49.071, mType=0, mName=cpu_thermal_zone, mStatus=0}
            Temperature{mValue=48.937, mType=1, mName=gpu_thermal_zone, mStatus=0}

    重點：
    - 優先取「Current temperatures from HAL」區段；那裡是即時值。
      「Cached temperatures」是上次回呼的快取，可能是舊的。
    - 兩個區段的感測器名稱會重複，以 HAL 區段為準。
    - mType 才是可信的分類依據，mName 各家亂命名（看過 mType=5 卻叫
      *-battery 的板子），不要用名稱猜類型。
    """
    snapshot = ThermalSnapshot(ts=ts or time.time(), source="thermalservice", raw=text)

    status_match = _THERMAL_STATUS_RE.search(text)
    if status_match:
        snapshot.status = ThrottlingStatus.parse(int(status_match.group(1)))

    hal_section = _extract_section(text, "Current temperatures from HAL:")
    cached_section = _extract_section(text, "Cached temperatures:")

    # HAL 區段優先；找不到才改用 cached；都沒有就掃全文
    primary = hal_section or cached_section or text

    by_name: dict[str, TemperatureReading] = {}
    for match in _TEMPERATURE_RE.finditer(primary):
        reading = TemperatureReading(
            name=match.group("name").strip(),
            type_id=int(match.group("type")),
            celsius=float(match.group("value")),
            status=int(match.group("status")),
        )
        by_name[f"{reading.type_id}:{reading.name}"] = reading

    snapshot.readings = list(by_name.values())

    if not snapshot.readings and snapshot.status is None:
        raise ThermalError(
            "無法從 dumpsys thermalservice 解析出溫度或節流狀態。此裝置可能未實作 Thermal HAL 2.0。"
        )
    return snapshot


def _extract_section(text: str, header: str) -> str:
    """取出某個標題之後、下一個非縮排標題之前的內容。"""
    start = text.find(header)
    if start == -1:
        return ""
    start += len(header)
    lines: list[str] = []
    for line in text[start:].splitlines():
        if line.strip() and not line[0].isspace():
            break  # 碰到下一個頂層標題
        lines.append(line)
    return "\n".join(lines)


_HW_PROP_RE = re.compile(
    r"^(?P<label>CPU|GPU|Battery|Skin|Device)\s+temperatures?.*?:\s*\[(?P<values>[^\]]*)\]",
    re.IGNORECASE | re.MULTILINE,
)
_HW_PROP_TYPE_MAP = {"cpu": 0, "gpu": 1, "battery": 2, "skin": 3, "device": 3}


def parse_hardware_properties(text: str, *, ts: float | None = None) -> ThermalSnapshot:
    """解析 `dumpsys hardware_properties`（舊機型備援）。

    格式（AOSP HardwarePropertiesManagerService.dump）：
        CPU temperatures: [42.0, 41.5]
        CPU throttling temperatures: [85.0, 85.0]
        GPU temperatures: [39.0]
        Battery temperatures: [30.0]
    只取 `<label> temperatures:` 那行，throttling / shutdown 門檻行不是讀數。
    """
    snapshot = ThermalSnapshot(ts=ts or time.time(), source="hardware_properties", raw=text)
    for match in _HW_PROP_RE.finditer(text):
        type_id = _HW_PROP_TYPE_MAP.get(match.group("label").lower())
        if type_id is None:
            continue
        for index, chunk in enumerate(match.group("values").split(",")):
            chunk = chunk.strip()
            if not chunk:
                continue
            try:
                value = float(chunk)
            except ValueError:
                continue
            snapshot.readings.append(
                TemperatureReading(
                    name=f"{match.group('label').lower()}{index}",
                    type_id=type_id,
                    celsius=value,
                )
            )
    if not snapshot.readings:
        raise ThermalError("dumpsys hardware_properties 沒有回傳任何溫度。")
    return snapshot


_SYSFS_ZONE_RE = re.compile(r"^(?P<zone>thermal_zone\d+)\s+(?P<type>\S+)\s+(?P<temp>-?\d+)\s*$")

# 依感測器名稱關鍵字猜類型。sysfs 沒有 mType，只能靠名字，準確度有限。
_SYSFS_NAME_HINTS: tuple[tuple[str, int], ...] = (
    ("gpu", 1),
    ("mali", 1),
    ("kgsl", 1),
    ("batt", 2),
    ("skin", 3),
    ("shell", 3),
    ("quiet", 3),
    ("cpu", 0),
    ("soc", 0),
    ("ap", 0),
    ("tsens", 0),
)


#: 推斷 sysfs 單位時使用的帶寬。刻意比 TEMP_MIN_C/TEMP_MAX_C 窄。
#: 運作中的手機零件溫度幾乎必然落在這個區間，用寬帶寬會讓多種刻度同時「看起來合理」。
_UNIT_INFER_MIN = 10.0
_UNIT_INFER_MAX = 120.0


def normalize_sysfs_temp(raw_value: int) -> float | None:
    """sysfs 的單位在不同 SoC 上分別是度／十分之一度／毫度，必須猜。

    除數**由小到大**嘗試，取第一個落在推斷帶寬內的結果。順序很重要：
    若先試 1000，raw=44 會先被 ÷10 判成 4.4°C（也在寬帶寬內）而給出錯誤答案。
    由小到大則 44 → 44°C、368 → 36.8°C、58400 → 58.4°C 三種刻度都正確。

    猜不出來就回 None，不要硬塞一個離譜的數字給使用者。
    """
    for divisor in (1.0, 10.0, 1000.0):
        candidate = raw_value / divisor
        if _UNIT_INFER_MIN <= candidate <= _UNIT_INFER_MAX:
            return candidate
    return None


def parse_sysfs_zones(text: str, *, ts: float | None = None) -> ThermalSnapshot:
    """解析自製的 sysfs 掃描輸出，每行格式為 `<zone> <type> <temp>`。

    產生這種輸出的 shell 指令見 ThermalCollector.SYSFS_COMMAND。
    """
    snapshot = ThermalSnapshot(ts=ts or time.time(), source="sysfs", raw=text)
    for line in text.splitlines():
        match = _SYSFS_ZONE_RE.match(line.strip())
        if not match:
            continue
        celsius = normalize_sysfs_temp(int(match.group("temp")))
        if celsius is None:
            continue
        name = match.group("type")
        lowered = name.lower()
        type_id = next((tid for hint, tid in _SYSFS_NAME_HINTS if hint in lowered), -1)
        snapshot.readings.append(TemperatureReading(name=name, type_id=type_id, celsius=celsius))
    if not snapshot.readings:
        raise ThermalError("沒有任何可讀取的 thermal_zone。")
    return snapshot


_BATTERY_TEMP_RE = re.compile(r"^\s*temperature:\s*(-?\d+)\s*$", re.MULTILINE)


def parse_battery(text: str, *, ts: float | None = None) -> ThermalSnapshot:
    """解析 `dumpsys battery`。單位固定是十分之一攝氏度。"""
    match = _BATTERY_TEMP_RE.search(text)
    if not match:
        raise ThermalError("dumpsys battery 沒有 temperature 欄位。")
    snapshot = ThermalSnapshot(ts=ts or time.time(), source="battery", raw=text)
    snapshot.readings.append(
        TemperatureReading(name="battery", type_id=2, celsius=int(match.group(1)) / 10.0)
    )
    return snapshot


# --------------------------------------------------------------------------- #
# 採集器
# --------------------------------------------------------------------------- #


class ThermalCollector:
    """負責決定用哪個來源、並以較低的頻率取樣。

    溫度變化遠比幀率慢，而每次 dumpsys 都有成本。因此預設每 2 秒才真正打一次
    指令，中間的查詢直接回傳上一次的快照（並標記 stale）。這讓 FPS 可以用 1 秒
    取樣而不會被溫度採集拖累。
    """

    SYSFS_COMMAND = (
        "for z in /sys/class/thermal/thermal_zone*; do "
        '  [ -r "$z/temp" ] || continue; '
        '  echo "$(basename $z) $(cat $z/type 2>/dev/null || echo unknown) '
        '$(cat $z/temp 2>/dev/null || echo 0)"; '
        "done"
    )

    def __init__(self, adb: AdbClient, *, min_interval: float = 2.0) -> None:
        self.adb = adb
        self.min_interval = min_interval
        self.source: str | None = None
        self._last: ThermalSnapshot | None = None
        self._last_fetch: float = 0.0
        self._raw_snapshots: list[str] = []

    # ------------------------------------------------------------------ #

    def detect_source(self) -> str:
        """依優先序試一輪，回傳第一個能用的來源名稱。"""
        attempts: list[tuple[str, str, object]] = [
            ("thermalservice", "dumpsys thermalservice", parse_thermalservice),
            ("hardware_properties", "dumpsys hardware_properties", parse_hardware_properties),
            ("sysfs", self.SYSFS_COMMAND, parse_sysfs_zones),
            ("battery", "dumpsys battery", parse_battery),
        ]
        for name, command, parser in attempts:
            result = self.adb.shell(command, timeout=15.0)
            if not result.ok or not result.stdout.strip():
                continue
            try:
                parser(result.stdout)  # type: ignore[operator]
            except ThermalError:
                continue
            self.source = name
            return name
        raise ThermalError(
            "此裝置上找不到任何可用的溫度來源。\n"
            "已嘗試：dumpsys thermalservice / hardware_properties / "
            "sysfs thermal_zone / dumpsys battery。\n"
            "請執行 `frameprobe doctor --verbose` 查看各來源的原始輸出。"
        )

    def _fetch(self) -> ThermalSnapshot:
        if self.source is None:
            self.detect_source()

        command, parser = {
            "thermalservice": ("dumpsys thermalservice", parse_thermalservice),
            "hardware_properties": ("dumpsys hardware_properties", parse_hardware_properties),
            "sysfs": (self.SYSFS_COMMAND, parse_sysfs_zones),
            "battery": ("dumpsys battery", parse_battery),
        }[self.source]  # type: ignore[index]

        result = self.adb.shell(command, timeout=15.0)
        if not result.ok:
            raise ThermalError(f"溫度採集失敗：{result.stderr or result.stdout}")
        self._raw_snapshots.append(result.stdout)
        return parser(result.stdout)

    def read(self, *, force: bool = False) -> ThermalSnapshot | None:
        """取得目前溫度。距上次採集未滿 min_interval 時回傳快取值。

        採集失敗不拋例外——溫度是附加資訊，不該讓整個 FPS session 掛掉。
        失敗時回傳上一次的快照（或 None）。
        """
        now = time.time()
        if not force and self._last and (now - self._last_fetch) < self.min_interval:
            return self._last
        try:
            self._last = self._fetch()
            self._last_fetch = now
        except (ThermalError, FrameprobeError):
            pass
        return self._last

    @property
    def raw_snapshots(self) -> list[str]:
        return self._raw_snapshots
