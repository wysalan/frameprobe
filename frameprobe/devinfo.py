"""裝置規格（顯示在三個頁面頂端的裝置列）。

一次 adb shell 把所有來源帶回，用 `#section` 分段；解析為純函式。
格式依真機樣本：tests/fixtures/devinfo_samsung_s23.txt、devinfo_pixel11pro.txt。
抓不到的欄位一律 None，不猜。
"""

from __future__ import annotations

import re
import time
from collections import Counter
from dataclasses import asdict, dataclass, field
from typing import Any

from .adb import AdbClient

_PROPS = (
    "ro.product.brand",
    "ro.product.manufacturer",
    "ro.product.model",
    "ro.product.marketname",
    "ro.product.device",
    "ro.build.version.release",
    "ro.build.version.sdk",
    "ro.build.version.security_patch",
    "ro.build.display.id",
    "ro.build.type",
    "ro.soc.manufacturer",
    "ro.soc.model",
    "ro.board.platform",
    "ro.hardware.egl",
    "ro.hardware.vulkan",
    "ro.product.cpu.abilist",
)

DEVINFO_COMMAND = (
    "echo '#props'; for p in " + " ".join(_PROPS) + '; do echo "$p=$(getprop $p)"; done; '
    "echo '#cpu'; for c in /sys/devices/system/cpu/cpu[0-9]*; do "
    'echo "$(basename $c) $(cat $c/cpufreq/cpuinfo_max_freq 2>&1)"; done; '
    "echo '#mem'; grep MemTotal /proc/meminfo; "
    "echo '#disk'; df /data 2>&1 | tail -1; "
    "echo '#screen'; wm size; wm density; "
    "echo '#kernel'; uname -r; getenforce; id; "
    "echo '#display'; dumpsys display 2>/dev/null | grep -E "
    "'mActiveSfDisplayMode=|mSupportedRefreshRates=|hasArrSupport' | head -3; "
    "echo '#gles'; dumpsys SurfaceFlinger 2>/dev/null | grep -E '^GLES:' | head -1; "
    "echo '#battery'; cat /sys/class/power_supply/battery/charge_full_design 2>&1; "
    "cat /sys/class/power_supply/battery/cycle_count 2>&1; "
    "dumpsys battery 2>/dev/null | grep -E 'mSavedBatteryAsoc'; "
    # 最後一段的結束碼就是整條指令的結束碼；Pixel 沒有 Asoc 行會讓 grep 回 1。以內容判斷，不看 rc。
    "true"
)


@dataclass
class DeviceSpecs:
    brand: str = ""
    manufacturer: str = ""
    model: str = ""
    marketname: str = ""
    codename: str = ""
    android_release: str = ""
    sdk_int: int | None = None
    security_patch: str = ""
    build_id: str = ""
    build_type: str = ""
    soc: str = ""
    """例如 'QTI SM8550' / 'Google Tensor G6'"""
    platform: str = ""
    abi: str = ""
    cpu_cores: int | None = None
    cpu_max_mhz: list[int] = field(default_factory=list)
    """各核心最高頻率（MHz），依 cpu 編號。"""
    gpu_driver: str = ""
    """ro.hardware.egl，例如 adreno / powervr / mali"""
    gpu_name: str = ""
    """SurfaceFlinger 的 GLES 行，例如 'Adreno (TM) 740'；走 Vulkan RenderEngine 的裝置沒有。"""
    gles_version: str = ""
    gpu_driver_version: str = ""
    ram_mb: int | None = None
    storage_total_gb: float | None = None
    storage_free_gb: float | None = None
    screen_px: str = ""
    screen_dpi: int | None = None
    refresh_rate_hz: float | None = None
    """目前顯示模式的更新率（SurfaceFlinger active mode）。ARR 裝置上是上限。"""
    refresh_rates_hz: list[float] = field(default_factory=list)
    arr: bool | None = None
    """Adaptive Refresh Rate：true 時實際更新率會在 1Hz～上限之間變動。"""
    kernel: str = ""
    selinux: str = ""
    is_rooted: bool | None = None
    battery_design_mah: int | None = None
    battery_cycles: int | None = None
    battery_health_pct: int | None = None
    """Samsung 的 Asoc；其他廠商多半沒有。"""
    collected_at: float = 0.0
    raw: str = ""

    # --- 顯示用 ------------------------------------------------------------ #

    @property
    def display_name(self) -> str:
        name = self.marketname or self.model
        maker = self.brand or self.manufacturer
        return f"{maker} {name}".strip() if maker.lower() not in name.lower() else name

    @property
    def cpu_summary(self) -> str:
        """'8 核：1×3.36 + 4×2.80 + 3×2.02 GHz'，由高到低。"""
        if not self.cpu_max_mhz:
            return ""
        groups = Counter(self.cpu_max_mhz)
        parts = [f"{n}×{mhz / 1000:.2f}" for mhz, n in sorted(groups.items(), reverse=True)]
        return f"{len(self.cpu_max_mhz)} 核：{' + '.join(parts)} GHz"

    @property
    def gpu_summary(self) -> str:
        if self.gpu_name:
            extra = ", ".join(x for x in (self.gles_version, self.gpu_driver_version) if x)
            return f"{self.gpu_name}（{extra}）" if extra else self.gpu_name
        return self.gpu_driver or ""

    @property
    def os_summary(self) -> str:
        parts = [f"Android {self.android_release}" if self.android_release else ""]
        if self.sdk_int:
            parts.append(f"API {self.sdk_int}")
        if self.security_patch:
            parts.append(f"安全性更新 {self.security_patch}")
        return " · ".join(p for p in parts if p)

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data.pop("raw", None)
        data.update(
            display_name=self.display_name,
            cpu_summary=self.cpu_summary,
            gpu_summary=self.gpu_summary,
            os_summary=self.os_summary,
        )
        return data


# --------------------------------------------------------------------------- #

_GLES_RE = re.compile(r"^GLES:\s*([^,]+),\s*([^,]+),\s*(OpenGL ES [\d.]+)\s*(\S+)?", re.MULTILINE)
_ACTIVE_MODE_RE = re.compile(r"mActiveSfDisplayMode=DisplayMode\{[^}]*?peakRefreshRate=([\d.]+)")
_SUPPORTED_RE = re.compile(r"mSupportedRefreshRates=\[([^\]]*)\]")
_ARR_RE = re.compile(r"hasArrSupport (true|false)")
_ASOC_RE = re.compile(r"mSavedBatteryAsoc:\s*\[(\d+)\]")


def _sections(text: str) -> dict[str, list[str]]:
    out: dict[str, list[str]] = {}
    current = "_"
    for line in text.splitlines():
        if line.startswith("#") and " " not in line.strip():
            current = line[1:].strip()
            out[current] = []
            continue
        out.setdefault(current, []).append(line)
    return out


def _int(value: str) -> int | None:
    value = value.strip()
    return int(value) if value.lstrip("-").isdigit() else None


def parse_devinfo(text: str, *, ts: float | None = None) -> DeviceSpecs:
    sec = _sections(text)
    specs = DeviceSpecs(collected_at=ts or time.time(), raw=text)

    props: dict[str, str] = {}
    for line in sec.get("props", []):
        key, sep, value = line.partition("=")
        if sep:
            props[key.strip()] = value.strip()
    specs.brand = props.get("ro.product.brand", "")
    specs.manufacturer = props.get("ro.product.manufacturer", "")
    specs.model = props.get("ro.product.model", "")
    specs.marketname = props.get("ro.product.marketname", "")
    specs.codename = props.get("ro.product.device", "")
    specs.android_release = props.get("ro.build.version.release", "")
    specs.sdk_int = _int(props.get("ro.build.version.sdk", ""))
    specs.security_patch = props.get("ro.build.version.security_patch", "")
    specs.build_id = props.get("ro.build.display.id", "")
    specs.build_type = props.get("ro.build.type", "")
    soc = " ".join(
        x for x in (props.get("ro.soc.manufacturer", ""), props.get("ro.soc.model", "")) if x
    )
    specs.soc = soc
    specs.platform = props.get("ro.board.platform", "")
    specs.abi = props.get("ro.product.cpu.abilist", "").split(",")[0]
    specs.gpu_driver = props.get("ro.hardware.egl", "") or props.get("ro.hardware.vulkan", "")

    freqs: list[int] = []
    for line in sec.get("cpu", []):
        parts = line.split()
        if len(parts) == 2 and parts[1].isdigit():
            freqs.append(int(parts[1]) // 1000)
    specs.cpu_max_mhz = freqs
    specs.cpu_cores = len(freqs) or None

    for line in sec.get("mem", []):
        parts = line.split()
        if len(parts) >= 2 and parts[0] == "MemTotal:" and parts[1].isdigit():
            specs.ram_mb = int(parts[1]) // 1024

    for line in sec.get("disk", []):
        parts = line.split()
        if len(parts) >= 4 and parts[1].isdigit() and parts[3].isdigit():
            specs.storage_total_gb = round(int(parts[1]) / 1024 / 1024, 1)
            specs.storage_free_gb = round(int(parts[3]) / 1024 / 1024, 1)

    for line in sec.get("screen", []):
        if line.startswith("Physical size:"):
            specs.screen_px = line.split(":", 1)[1].strip()
        elif line.startswith("Physical density:"):
            specs.screen_dpi = _int(line.split(":", 1)[1])

    kernel_lines = [line.strip() for line in sec.get("kernel", []) if line.strip()]
    for line in kernel_lines:
        if line in ("Enforcing", "Permissive", "Disabled"):
            specs.selinux = line
        elif line.startswith("uid="):
            specs.is_rooted = "uid=0(" in line
        elif not specs.kernel:
            specs.kernel = line

    display = "\n".join(sec.get("display", []))
    if match := _ACTIVE_MODE_RE.search(display):
        specs.refresh_rate_hz = round(float(match.group(1)))
    if match := _SUPPORTED_RE.search(display):
        specs.refresh_rates_hz = [round(float(v)) for v in match.group(1).split(",") if v.strip()]
    if match := _ARR_RE.search(display):
        specs.arr = match.group(1) == "true"

    if match := _GLES_RE.search("\n".join(sec.get("gles", []))):
        specs.gpu_name = match.group(2).strip()
        specs.gles_version = match.group(3).strip()
        specs.gpu_driver_version = (match.group(4) or "").strip()

    battery = [line.strip() for line in sec.get("battery", []) if line.strip()]
    numbers = [line for line in battery if line.isdigit()]
    if len(numbers) >= 1 and int(numbers[0]) > 100_000:
        specs.battery_design_mah = int(numbers[0]) // 1000  # µAh → mAh
    if len(numbers) >= 2:
        specs.battery_cycles = int(numbers[1])
    if match := _ASOC_RE.search("\n".join(battery)):
        specs.battery_health_pct = int(match.group(1))
    return specs


def collect_device_specs(adb: AdbClient) -> DeviceSpecs | None:
    """抓不到（adb 失敗、輸出空）回 None，由呼叫端決定要不要提示。"""
    result = adb.shell(DEVINFO_COMMAND, timeout=30.0)
    if "#props" not in result.stdout:
        return None
    return parse_devinfo(result.stdout)
