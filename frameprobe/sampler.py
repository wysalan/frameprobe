"""即時模式：timestats 累積值差分取樣。

核心想法（SPEC §1.1）：timestats 啟用後是累積計數器，
所以「本次快照 − 上次快照」就是這段區間的真實幀資料。
這讓我們不需要碰 `--latency` 就能做到即時監看。
"""

from __future__ import annotations

import time
from collections.abc import Iterator
from dataclasses import asdict, dataclass, field
from typing import Any

from .adb import AdbClient, FrameprobeError
from .jank import jank_from_histogram
from .meminfo import MemDetailCollector
from .power import PowerCollector
from .sysstats import CpuUsage, SysSnapshot, SysStatsCollector, kb_to_mb
from .thermal import ThermalCollector, ThermalSnapshot
from .timestats import LayerStats, TimestatsDump, parse_timestats

MIN_INTERVAL_S = 0.5


class SamplerError(FrameprobeError):
    pass


@dataclass
class Sample:
    ts: float
    elapsed: float
    frames: int
    fps: float | None
    p90_fps: float | None
    p99_fps: float | None
    dropped: int
    dropped_ratio: float
    histogram: dict[int, int] = field(default_factory=dict)

    # --- 熱資訊（SPEC §12）---------------------------------------------- #
    # 溫度取樣頻率低於幀率取樣，所以這幾欄可能沿用上一次的讀數。
    # thermal_stale 為 True 時代表這筆不是本次區間新採的值。
    cpu_c: float | None = None
    gpu_c: float | None = None
    battery_c: float | None = None
    skin_c: float | None = None
    npu_c: float | None = None
    throttling_status: int | None = None
    throttling_label: str | None = None
    thermal_stale: bool = False

    # --- CPU / 記憶體 ------------------------------------------------------- #
    cpu_total_pct: float | None = None
    cpu_app_pct: float | None = None
    cpu_app_norm_pct: float | None = None
    cpu_freq_mhz: list[int] = field(default_factory=list)
    mem_pss_mb: float | None = None
    mem_rss_mb: float | None = None
    mem_swap_mb: float | None = None
    mem_available_mb: float | None = None
    gpu_busy_pct: float | None = None
    gpu_freq_mhz: int | None = None
    gpu_mem_mb: float | None = None
    wakeups: int | None = None
    cswitch: int | None = None
    net_rx_kbps: float | None = None
    net_tx_kbps: float | None = None
    mem_detail_mb: dict[str, float] | None = None
    """dumpsys meminfo 的分類（java_heap / native_heap / graphics / gl_mtrack …），MB。"""
    mem_detail_stale: bool = False

    # --- 功耗（dumpsys battery；接 USB 時為淨充電值，不代表整機耗電）------------ #
    battery_voltage_v: float | None = None
    battery_current_ma: float | None = None
    battery_power_w: float | None = None
    battery_plugged: bool | None = None
    battery_level_pct: int | None = None
    battery_charge_mah: float | None = None
    """dumpsys battery 的 Charge counter（µAh → mAh），開始減結束就是這段消耗的電量。"""
    battery_status: int | None = None
    """BatteryManager.BATTERY_STATUS_*：2 充電、3 放電、4 未充電、5 已充滿。"""
    fpower_mw: float | None = None
    """單幀功耗 = |power| × 1000 / fps。接 USB 時無意義，維持 None。"""
    power_stale: bool = False

    screenshot: str | None = None
    """相對 session 目錄的截圖路徑（shots/000012.png），僅在開啟截圖時有值。"""

    # --- Jank（即時模式為直方圖近似，method="histogram"）---------------------- #
    jank: int = 0
    big_jank: int = 0
    stutter_ms: float = 0.0
    jank_method: str = "histogram"

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        # JSON 的 key 必須是字串
        data["histogram"] = {str(k): v for k, v in self.histogram.items()}
        return data

    def attach_thermal(self, snapshot: ThermalSnapshot | None, *, stale: bool) -> None:
        if snapshot is None:
            return
        self.cpu_c = snapshot.cpu_c
        self.gpu_c = snapshot.gpu_c
        self.battery_c = snapshot.battery_c
        self.skin_c = snapshot.skin_c
        self.npu_c = snapshot.npu_c
        self.thermal_stale = stale
        if snapshot.status is not None:
            self.throttling_status = int(snapshot.status)
            self.throttling_label = snapshot.status.label

    def attach_sysstats(self, usage: CpuUsage | None, snap: SysSnapshot) -> None:
        if usage is not None:
            self.cpu_total_pct = usage.total_pct
            self.cpu_app_pct = usage.app_pct
            self.cpu_app_norm_pct = usage.app_norm_pct
            self.cpu_freq_mhz = usage.freq_mhz
            self.wakeups, self.cswitch = usage.wakeups, usage.cswitch
            self.net_rx_kbps, self.net_tx_kbps = usage.net_rx_kbps, usage.net_tx_kbps
        self.mem_pss_mb = kb_to_mb(snap.pss_kb)
        self.mem_rss_mb = kb_to_mb(snap.rss_kb)
        self.mem_swap_mb = kb_to_mb(snap.swap_kb)
        self.mem_available_mb = kb_to_mb(snap.mem_available_kb)
        self.gpu_busy_pct = snap.gpu_busy_pct
        self.gpu_freq_mhz = snap.gpu_freq_mhz
        self.gpu_mem_mb = kb_to_mb(snap.gpu_mem_kb)
        if self.gpu_c is None and snap.gpu_temp_c is not None:
            self.gpu_c = snap.gpu_temp_c  # thermalservice 沒有 GPU 型別時用 kgsl temp


def capture_timestats(adb: AdbClient, *, warmup_s: float = 1.5) -> TimestatsDump:
    """啟用 timestats、等一小段時間、dump、停用。用於一次性查詢（layers / API）。"""
    enable = adb.shell("dumpsys SurfaceFlinger --timestats -clear -enable")
    if not enable.ok:
        raise SamplerError(f"無法啟用 timestats：{enable.stderr or enable.stdout}")
    time.sleep(warmup_s)
    dump = adb.shell("dumpsys SurfaceFlinger --timestats -dump", timeout=30.0)
    adb.shell("dumpsys SurfaceFlinger --timestats -disable")
    if not dump.ok:
        raise SamplerError(f"timestats dump 失敗：{dump.stderr or dump.stdout}")
    return parse_timestats(dump.stdout)


class RealtimeSampler:
    """用法：

        sampler = RealtimeSampler(adb, package="com.example.game", interval=1.0)
        with sampler:
            for sample in sampler.stream():
                print(sample.fps)

    context manager 負責 enable / disable，確保即使中途例外也會把裝置狀態還原。
    """

    def __init__(
        self,
        adb: AdbClient,
        package: str,
        *,
        interval: float = 1.0,
        layer_hint: str | None = None,
        thermal: bool = True,
        thermal_interval: float = 2.0,
        sysstats: bool = True,
    ) -> None:
        if interval < MIN_INTERVAL_S:
            raise ValueError(
                f"取樣間隔不得低於 {MIN_INTERVAL_S}s；更短的間隔中 dumpsys 本身的"
                "開銷會開始干擾被測 App。"
            )
        self.adb = adb
        self.package = package
        self.interval = interval
        self.layer_hint = layer_hint

        self.thermal_requested = thermal
        self.thermal: ThermalCollector | None = (
            ThermalCollector(adb, min_interval=max(thermal_interval, interval)) if thermal else None
        )
        self._last_thermal_ts: float = 0.0
        self.sysstats: SysStatsCollector | None = (
            SysStatsCollector(adb, package) if sysstats else None
        )
        self.sysstats_pss_error: str | None = None
        self.gpu_temp_from_kgsl = False
        self.memdetail: MemDetailCollector | None = (
            MemDetailCollector(adb, package, min_interval=max(thermal_interval, interval))
            if sysstats
            else None
        )
        self._last_memdetail_ts: float = 0.0
        self.pss_from_meminfo = False
        self.power: PowerCollector | None = (
            PowerCollector(adb, min_interval=max(thermal_interval, interval)) if sysstats else None
        )
        self._last_power_ts: float = 0.0

        self.layer_name: str | None = None
        self._previous: LayerStats | None = None
        self._started_at: float = 0.0
        self._raw_snapshots: list[str] = []
        self._active = False

    # ------------------------------------------------------------------ #

    def __enter__(self) -> RealtimeSampler:
        self.start()
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.stop()

    def start(self) -> None:
        result = self.adb.shell("dumpsys SurfaceFlinger --timestats -clear -enable")
        if not result.ok:
            raise SamplerError(
                "無法啟用 timestats。請先執行 `frameprobe doctor` 確認裝置支援。\n"
                f"{result.stderr or result.stdout}"
            )
        self._active = True
        self._started_at = time.time()
        self._previous = None

        # 溫度來源在開始時就確定，避免每次取樣都重試一輪。
        # 偵測失敗時停用溫度採集，不影響主要的幀率採集。
        if self.thermal is not None:
            try:
                self.thermal.detect_source()
            except FrameprobeError:
                self.thermal = None

    def stop(self) -> None:
        if self._active:
            self.adb.shell("dumpsys SurfaceFlinger --timestats -disable")
            self._active = False

    # ------------------------------------------------------------------ #

    def _dump(self) -> TimestatsDump:
        result = self.adb.shell("dumpsys SurfaceFlinger --timestats -dump", timeout=30.0)
        if not result.ok:
            raise SamplerError(f"timestats dump 失敗：{result.stderr or result.stdout}")
        self._raw_snapshots.append(result.stdout)
        return parse_timestats(result.stdout)

    def _resolve_layer(self, dump: TimestatsDump) -> LayerStats:
        """第一次取樣時決定要盯哪個 layer，之後固定不變。"""
        if self.layer_name:
            layer = dump.find_layer(self.layer_name)
            if layer is None:
                raise SamplerError(
                    f"先前鎖定的圖層在本次 dump 中消失了：{self.layer_name}\n"
                    "可能是遊戲被切到背景或重新建立了 surface。"
                )
            return layer

        candidates = dump.candidates_for(self.package)
        if self.layer_hint:
            candidates = [c for c in candidates if self.layer_hint.lower() in c.layer_name.lower()]

        if not candidates:
            raise SamplerError(
                f"找不到屬於 {self.package} 且有幀資料的圖層。\n"
                "請確認：(1) 遊戲在前景 (2) 畫面有在動 (3) 套件名稱正確。\n"
                "可執行 `frameprobe layers -p <pkg>` 查看所有候選。"
            )

        if dump.is_ambiguous(candidates) and not self.layer_hint:
            names = "\n".join(
                f"  - {c.layer_name} (frames={c.total_frames})" for c in candidates[:5]
            )
            raise SamplerError(
                "偵測到多個幀數相近的候選圖層，無法自動判定。\n"
                f"{names}\n"
                "請用 --layer <關鍵字> 指定。"
            )

        self.layer_name = candidates[0].layer_name
        return candidates[0]

    # ------------------------------------------------------------------ #

    def sample_once(self) -> Sample | None:
        """取一次快照並與上次差分。第一次呼叫只建立基準，回傳 None。"""
        dump = self._dump()
        current = self._resolve_layer(dump)

        if self._previous is None:
            self._previous = current
            if self.sysstats is not None:
                self.sysstats.read()  # CPU 差分也需要基準
            return None

        delta = current.subtract(self._previous)
        self._previous = current

        hist = delta.present_to_present
        now = time.time()

        sample = Sample(
            ts=now,
            elapsed=now - self._started_at,
            frames=delta.total_frames,
            fps=delta.effective_fps(),
            p90_fps=hist.percentile_fps(0.90),
            p99_fps=hist.percentile_fps(0.99),
            dropped=delta.dropped_frames,
            dropped_ratio=delta.dropped_ratio(),
            histogram=dict(hist.buckets),
        )
        jank = jank_from_histogram(hist.buckets)
        sample.jank, sample.big_jank, sample.stutter_ms = jank.jank, jank.big_jank, jank.stutter_ms
        sample.jank_method = jank.method

        if self.thermal is not None:
            snapshot = self.thermal.read()
            if snapshot is not None:
                stale = snapshot.ts <= self._last_thermal_ts
                sample.attach_thermal(snapshot, stale=stale)
                self._last_thermal_ts = snapshot.ts

        if self.sysstats is not None:
            read = self.sysstats.read()
            if read is not None:
                had_gpu_c = sample.gpu_c is not None
                sample.attach_sysstats(*read)
                if not had_gpu_c and sample.gpu_c is not None:
                    self.gpu_temp_from_kgsl = True
                if read[1].pss_error and self.sysstats_pss_error is None:
                    self.sysstats_pss_error = read[1].pss_error
            elif self.sysstats.failures >= 5:
                self.sysstats = None  # 連續失敗就停用，由 session 記進 notes

        if self.memdetail is not None:
            detail = self.memdetail.read()
            if detail is not None:
                sample.mem_detail_mb = detail.to_dict()
                sample.mem_detail_stale = detail.ts <= self._last_memdetail_ts
                self._last_memdetail_ts = detail.ts
                # smaps_rollup 被擋時，dumpsys meminfo 的 TOTAL PSS 是更好的替代（比 RSS 準）
                if sample.mem_pss_mb is None and detail.total_pss_mb is not None:
                    sample.mem_pss_mb = round(detail.total_pss_mb, 1)
                    if detail.total_swap_pss_mb is not None:
                        sample.mem_swap_mb = round(detail.total_swap_pss_mb, 1)
                    self.pss_from_meminfo = True
            elif self.memdetail.failures >= 5:
                self.memdetail = None

        if self.power is not None:
            power = self.power.read()
            if power is not None:
                sample.battery_voltage_v = power.voltage_v
                sample.battery_current_ma = power.current_ma
                sample.battery_power_w = power.power_w
                sample.battery_plugged = power.plugged
                sample.battery_level_pct = power.level
                sample.battery_charge_mah = (
                    round(power.charge_counter_uah / 1000, 1)
                    if power.charge_counter_uah is not None
                    else None
                )
                sample.battery_status = power.status
                sample.power_stale = power.ts <= self._last_power_ts
                self._last_power_ts = power.ts
                if (
                    not power.plugged
                    and power.power_w is not None
                    and sample.fps
                    and sample.fps > 0
                ):
                    sample.fpower_mw = round(abs(power.power_w) * 1000 / sample.fps, 2)
            elif self.power.failures >= 5:
                self.power = None

        return sample

    def stream(self, *, duration: float | None = None) -> Iterator[Sample]:
        """持續產出 Sample，直到 duration 到期或呼叫端中斷迴圈。"""
        deadline = time.time() + duration if duration else None
        while True:
            if deadline and time.time() >= deadline:
                return
            started = time.time()
            sample = self.sample_once()
            if sample is not None:
                yield sample
            # 扣掉 dump 本身耗時，讓間隔盡量貼近設定值
            remaining = self.interval - (time.time() - started)
            if remaining > 0:
                time.sleep(remaining)

    @property
    def raw_snapshots(self) -> list[str]:
        """原始 dumpsys 文字，session 結束時要寫進 raw/ 目錄。"""
        return self._raw_snapshots
