"""能力探測。

這是使用者在任何新機型（尤其 Android 17）上第一個會跑的東西。
它的職責是：告訴使用者哪條採集路徑可以用、哪條不能用、以及不能用的原始理由。

任何探測項目失敗時，都必須把原始輸出留在 CheckResult.detail 裡，
因為新機型出問題時那是唯一的線索。
"""

from __future__ import annotations

import shutil
import time
from dataclasses import dataclass, field
from enum import StrEnum

from .adb import AdbClient, FrameprobeError
from .devinfo import DeviceSpecs, collect_device_specs
from .timestats import TimestatsParseError, parse_timestats

# FrameTimeline 自 Android 12 (API 31) 起提供
MIN_SDK_FRAMETIMELINE = 31


class CheckStatus(StrEnum):
    PASS = "pass"
    FAIL = "fail"
    WARN = "warn"
    SKIP = "skip"


@dataclass
class CheckResult:
    name: str
    status: CheckStatus
    summary: str
    detail: str = ""
    """失敗時的原始輸出。新機型除錯全靠這個，不要省略。"""

    @property
    def ok(self) -> bool:
        return self.status in (CheckStatus.PASS, CheckStatus.WARN)


@dataclass
class DeviceInfo:
    serial: str
    manufacturer: str = ""
    model: str = ""
    android_release: str = ""
    sdk_int: int = 0
    is_rooted: bool = False
    specs: DeviceSpecs | None = None
    """完整規格（devinfo.py）；抓不到為 None。"""

    @property
    def label(self) -> str:
        name = f"{self.manufacturer} {self.model}".strip() or self.serial
        return f"{name} (Android {self.android_release or '?'} / API {self.sdk_int or '?'})"


@dataclass
class ProbeReport:
    device: DeviceInfo
    checks: list[CheckResult] = field(default_factory=list)
    layer_candidates: list[str] = field(default_factory=list)

    def get(self, name: str) -> CheckResult | None:
        for check in self.checks:
            if check.name == name:
                return check
        return None

    def _passed(self, name: str) -> bool:
        check = self.get(name)
        return bool(check and check.ok)

    @property
    def realtime_available(self) -> bool:
        return self._passed("timestats")

    @property
    def record_available(self) -> bool:
        return self._passed("perfetto_binary") and self._passed("frametimeline_supported")

    @property
    def analyze_available(self) -> bool:
        return self._passed("trace_processor")

    def blocking_reason(self) -> str | None:
        """兩種模式都不可用時，回傳給使用者的說明。"""
        if self.realtime_available or self.record_available:
            return None
        failures = [c for c in self.checks if c.status is CheckStatus.FAIL]
        lines = ["此裝置上即時模式與離線模式皆不可用。失敗項目："]
        lines += [f"  - {c.name}: {c.summary}" for c in failures]
        lines.append(
            "\n注意：本工具刻意不提供 `dumpsys SurfaceFlinger --latency` 作為備援，"
            "該方法在 Android 15 之後對遊戲類 App 已不可靠。"
        )
        return "\n".join(lines)


def collect_device_info(adb: AdbClient, *, specs: bool = True) -> DeviceInfo:
    sdk_raw = adb.getprop("ro.build.version.sdk")
    try:
        sdk_int = int(sdk_raw)
    except (TypeError, ValueError):
        raise FrameprobeError(
            f"無法取得裝置 SDK 版本（getprop 回傳 {sdk_raw!r}）。"
            "請確認裝置已連線且已授權 USB 偵錯。"
        ) from None

    id_result = adb.shell("id")
    is_rooted = "uid=0(" in id_result.stdout

    return DeviceInfo(
        serial=adb.serial or "unknown",
        manufacturer=adb.getprop("ro.product.manufacturer"),
        model=adb.getprop("ro.product.model"),
        android_release=adb.getprop("ro.build.version.release"),
        sdk_int=sdk_int,
        is_rooted=is_rooted,
        specs=collect_device_specs(adb) if specs else None,
    )


# --------------------------------------------------------------------------- #
# 個別檢查
# --------------------------------------------------------------------------- #


def check_timestats(adb: AdbClient, *, warmup_s: float = 1.5) -> tuple[CheckResult, list[str]]:
    """啟用 timestats、等一下、dump 回來看看有沒有東西。

    回傳 (檢查結果, 解析到的 layer 名稱清單)。
    這個檢查會實際改動裝置狀態（啟用 timestats），結束時會關掉。
    """
    enable = adb.shell("dumpsys SurfaceFlinger --timestats -clear -enable")
    if not enable.ok:
        return (
            CheckResult(
                name="timestats",
                status=CheckStatus.FAIL,
                summary="無法啟用 timestats",
                detail=f"exit={enable.returncode}\nstdout:\n{enable.stdout}\nstderr:\n{enable.stderr}",
            ),
            [],
        )

    time.sleep(warmup_s)
    dump = adb.shell("dumpsys SurfaceFlinger --timestats -dump", timeout=30.0)
    adb.shell("dumpsys SurfaceFlinger --timestats -disable")

    if not dump.ok:
        return (
            CheckResult(
                name="timestats",
                status=CheckStatus.FAIL,
                summary="timestats dump 失敗",
                detail=f"exit={dump.returncode}\nstdout:\n{dump.stdout}\nstderr:\n{dump.stderr}",
            ),
            [],
        )

    try:
        parsed = parse_timestats(dump.stdout)
    except TimestatsParseError as exc:
        return (
            CheckResult(
                name="timestats",
                status=CheckStatus.FAIL,
                summary=f"timestats 輸出無法解析：{exc}",
                detail=dump.stdout[:8000],
            ),
            [],
        )

    active = [layer for layer in parsed.layers if layer.total_frames > 0]
    if not active:
        return (
            CheckResult(
                name="timestats",
                status=CheckStatus.WARN,
                summary=(
                    f"timestats 可用但 {warmup_s}s 內沒有任何圖層產生幀。"
                    "請讓螢幕上有畫面在動（例如把遊戲切到前景）後重試。"
                ),
                detail=dump.stdout[:8000],
            ),
            [layer.layer_name for layer in parsed.layers],
        )

    has_p2p = any(layer.present_to_present.total > 0 for layer in active)
    if not has_p2p:
        return (
            CheckResult(
                name="timestats",
                status=CheckStatus.WARN,
                summary=(
                    "有 layer 資料但找不到 present-to-present 直方圖。"
                    "此機型的鍵名可能與已知格式不同，FPS 將改用 averageFPS 欄位。"
                ),
                detail=dump.stdout[:8000],
            ),
            [layer.layer_name for layer in active],
        )

    return (
        CheckResult(
            name="timestats",
            status=CheckStatus.PASS,
            summary=f"可用，偵測到 {len(active)} 個活躍圖層",
        ),
        [layer.layer_name for layer in active],
    )


DOCTOR_TRACE = "/data/misc/perfetto-traces/frameprobe-doctor.pb"


def check_perfetto_binary(adb: AdbClient) -> CheckResult:
    """不只看二進位存不存在，而是實際錄一段 0.5 秒的空 trace。

    /data/misc/perfetto-traces 對 shell domain 是不可 touch 的（SELinux），
    只有 perfetto 本身寫得進去，所以唯一可靠的檢查就是真的錄一次。
    """
    version = adb.shell("perfetto --version")
    if not (version.ok and version.stdout.strip()):
        return CheckResult(
            name="perfetto_binary",
            status=CheckStatus.FAIL,
            summary="裝置上沒有可用的 perfetto 執行檔",
            detail=f"exit={version.returncode}\nstdout:\n{version.stdout}\nstderr:\n{version.stderr}",
        )
    label = version.stdout.strip().splitlines()[0]
    trial = adb.shell(
        f"echo 'duration_ms: 500' | perfetto -c - --txt -o {DOCTOR_TRACE} && "
        f"rm -f {DOCTOR_TRACE} && echo frameprobe-ok",
        timeout=30.0,
    )
    if trial.ok and "frameprobe-ok" in trial.stdout:
        return CheckResult(
            name="perfetto_binary",
            status=CheckStatus.PASS,
            summary=f"{label}；已實際錄製 0.5s 測試 trace 成功",
        )
    return CheckResult(
        name="perfetto_binary",
        status=CheckStatus.FAIL,
        summary=f"{label} 存在，但無法錄製到 /data/misc/perfetto-traces",
        detail=f"exit={trial.returncode}\nstdout:\n{trial.stdout}\nstderr:\n{trial.stderr}",
    )


def check_traced_running(adb: AdbClient) -> CheckResult:
    state = adb.getprop("init.svc.traced")
    if state == "running":
        return CheckResult(
            name="traced_running", status=CheckStatus.PASS, summary="traced 服務執行中"
        )

    # 嘗試開啟後重測一次
    adb.shell("setprop persist.traced.enable 1")
    time.sleep(1.0)
    state_after = adb.getprop("init.svc.traced")
    if state_after == "running":
        return CheckResult(
            name="traced_running",
            status=CheckStatus.WARN,
            summary="traced 原本未執行，已透過 persist.traced.enable 啟動",
        )
    return CheckResult(
        name="traced_running",
        status=CheckStatus.FAIL,
        summary=f"traced 服務未執行（init.svc.traced={state_after or 'unset'}）",
        detail="嘗試 setprop persist.traced.enable 1 後仍未啟動。",
    )


def check_frametimeline(device: DeviceInfo) -> CheckResult:
    if device.sdk_int >= MIN_SDK_FRAMETIMELINE:
        return CheckResult(
            name="frametimeline_supported",
            status=CheckStatus.PASS,
            summary=f"API {device.sdk_int} >= {MIN_SDK_FRAMETIMELINE}",
        )
    return CheckResult(
        name="frametimeline_supported",
        status=CheckStatus.FAIL,
        summary=(
            f"API {device.sdk_int} 低於 {MIN_SDK_FRAMETIMELINE}，此裝置沒有 FrameTimeline 資料來源"
        ),
    )


def check_trace_processor() -> CheckResult:
    """檢查本機（不是裝置）有沒有辦法分析 trace。"""
    binary = shutil.which("trace_processor_shell")
    if binary:
        return CheckResult(
            name="trace_processor",
            status=CheckStatus.PASS,
            summary=f"找到 trace_processor_shell：{binary}",
        )
    try:
        import perfetto  # noqa: F401
    except ImportError:
        return CheckResult(
            name="trace_processor",
            status=CheckStatus.FAIL,
            summary="本機找不到 trace_processor_shell，也沒有安裝 perfetto 套件",
            detail=(
                "請執行 `uv add perfetto`，"
                "或從 Perfetto releases 下載 trace_processor_shell 放進 PATH。"
            ),
        )
    return CheckResult(
        name="trace_processor",
        status=CheckStatus.PASS,
        summary="已安裝 perfetto Python 套件",
    )


def check_thermal(adb: AdbClient) -> CheckResult:
    """依優先序找出可用的溫度來源，並回報實際讀到什麼。

    溫度不可用不阻擋幀率採集，所以失敗最多只到 WARN。
    """
    from .thermal import ThermalCollector, ThermalError

    collector = ThermalCollector(adb)
    try:
        source = collector.detect_source()
    except ThermalError as exc:
        return CheckResult(
            name="thermal",
            status=CheckStatus.WARN,
            summary="找不到可用的溫度來源，將以無溫度模式執行",
            detail=str(exc),
        )

    snapshot = collector.read(force=True)
    if snapshot is None:
        return CheckResult(
            name="thermal",
            status=CheckStatus.WARN,
            summary=f"來源 {source} 可偵測但讀取失敗",
        )

    parts = [f"來源={source}"]
    for label, value in (
        ("CPU", snapshot.cpu_c),
        ("GPU", snapshot.gpu_c),
        ("SKIN", snapshot.skin_c),
        ("BAT", snapshot.battery_c),
    ):
        if value is not None:
            parts.append(f"{label}={value:.1f}°C")

    if snapshot.status is None:
        return CheckResult(
            name="thermal",
            status=CheckStatus.WARN,
            summary=(f"{' '.join(parts)}；但讀不到 Thermal Status，將無法判斷是否進入熱節流"),
            detail=snapshot.raw[:4000],
        )

    return CheckResult(
        name="thermal",
        status=CheckStatus.PASS,
        summary=f"{' '.join(parts)} 節流狀態={snapshot.status.label}",
    )


def check_screen_state(adb: AdbClient) -> CheckResult:
    """螢幕關著的話任何幀率採集都不會有資料，先擋掉這個低級錯誤。"""
    result = adb.shell("dumpsys power | grep -E 'mWakefulness|Display Power'")
    text = result.stdout
    if "Asleep" in text or "Dozing" in text or "state=OFF" in text:
        return CheckResult(
            name="screen_state",
            status=CheckStatus.FAIL,
            summary="螢幕目前為關閉／休眠狀態，無法採集幀率",
            detail=text,
        )
    return CheckResult(name="screen_state", status=CheckStatus.PASS, summary="螢幕開啟中")


# --------------------------------------------------------------------------- #


def probe(adb: AdbClient, *, run_timestats: bool = True) -> ProbeReport:
    """執行完整探測。

    run_timestats=False 時跳過會改動裝置狀態的那項（測試或唯讀檢查時用）。
    """
    device = collect_device_info(adb)
    report = ProbeReport(device=device)

    report.checks.append(check_screen_state(adb))

    if run_timestats:
        timestats_check, layers = check_timestats(adb)
        report.checks.append(timestats_check)
        report.layer_candidates = layers
    else:
        report.checks.append(
            CheckResult(name="timestats", status=CheckStatus.SKIP, summary="已跳過")
        )

    report.checks.append(check_thermal(adb))
    report.checks.append(check_perfetto_binary(adb))
    report.checks.append(check_traced_running(adb))
    report.checks.append(check_frametimeline(device))
    report.checks.append(check_trace_processor())

    if device.sdk_int > 37:
        report.checks.append(
            CheckResult(
                name="sdk_known",
                status=CheckStatus.WARN,
                summary=(
                    f"API {device.sdk_int} 高於本工具已驗證的範圍（<= 37 / Android 17）。"
                    "採集應該仍然可行，但若解析異常請回報原始輸出。"
                ),
            )
        )

    return report
