"""把一串 Sample 彙總成 session 層級的數字（純函式，TASKS T2.3）。

重點：average FPS 不能把每筆 sample 的 fps 平均，要用總幀數 ÷ 總時間。
P90/P99 則把所有 sample 的直方圖累加後重算。
"""

from __future__ import annotations

import math
from datetime import datetime

from .probe import DeviceInfo
from .sampler import Sample
from .storage import SessionSummary
from .thermal import ThrottlingStatus
from .timestats import Histogram


def merge_histograms(samples: list[Sample]) -> Histogram:
    merged: dict[int, int] = {}
    for sample in samples:
        for ms, count in sample.histogram.items():
            merged[ms] = merged.get(ms, 0) + count
    return Histogram(merged)


def summarize_realtime(
    samples: list[Sample],
    *,
    session_id: str,
    device: DeviceInfo,
    package: str,
    layer_name: str,
    started_at: datetime,
    notes: list[str] | None = None,
) -> SessionSummary:
    summary = SessionSummary(
        session_id=session_id,
        device=device,
        package=package,
        layer_name=layer_name,
        mode="realtime",
        started_at=started_at,
        notes=list(notes or []),
        samples=samples,
    )
    if not samples:
        return summary

    hist = merge_histograms(samples)
    summary.duration_s = samples[-1].elapsed
    summary.total_frames = sum(s.frames for s in samples)
    summary.dropped_frames = sum(s.dropped for s in samples)
    if summary.duration_s > 0:
        summary.average_fps = summary.total_frames / summary.duration_s
    else:
        mean = hist.mean_ms()
        summary.average_fps = 1000.0 / mean if mean else None
    summary.p90_fps = hist.percentile_fps(0.90)
    summary.p99_fps = hist.percentile_fps(0.99)
    apply_thermal_summary(summary, samples)
    apply_sys_summary(summary, samples)
    apply_jank_summary(summary, samples)
    apply_stability_summary(summary, samples)
    return summary


def apply_stability_summary(summary: SessionSummary, samples: list[Sample]) -> None:
    fps = [s.fps for s in samples if s.fps is not None]
    if len(fps) >= 2:
        mean = sum(fps) / len(fps)
        summary.fps_std = math.sqrt(sum((f - mean) ** 2 for f in fps) / len(fps))
    summary.drop_fps = sum(1 for a, b in zip(fps, fps[1:], strict=False) if a - b > 8)

    hist = merge_histograms(samples)
    total = hist.total
    if total:
        summary.low_1_fps = hist.low_percent_fps(0.01)
        mean_ms = hist.mean_ms() or 0.0
        var = sum(count * (ms - mean_ms) ** 2 for ms, count in hist.buckets.items()) / total
        summary.ftime_avg_ms = mean_ms
        summary.ftime_std_ms = math.sqrt(var)
        summary.delta_ftime = sum(count for ms, count in hist.buckets.items() if ms > 100)


def apply_jank_summary(summary: SessionSummary, samples: list[Sample]) -> None:
    if not samples:
        return
    summary.jank_total = sum(s.jank for s in samples)
    summary.big_jank_total = sum(s.big_jank for s in samples)
    methods = {s.jank_method for s in samples}
    summary.jank_method = methods.pop() if len(methods) == 1 else "mixed"
    duration = summary.duration_s
    if duration > 0:
        summary.jank_per_10min = summary.jank_total / duration * 600
        summary.big_jank_per_10min = summary.big_jank_total / duration * 600
        summary.stutter_ratio = sum(s.stutter_ms for s in samples) / (duration * 1000)


def _avg_per_core(rows: list[list[int]]) -> list[float]:
    """每核心的平均頻率（MHz）。0 代表核心離線或讀不到，不計入平均。"""
    sums: list[float] = []
    counts: list[int] = []
    for row in rows:
        for i, mhz in enumerate(row):
            if i >= len(sums):
                sums.append(0.0)
                counts.append(0)
            if mhz > 0:
                sums[i] += mhz
                counts[i] += 1
    return [s / c if c else 0.0 for s, c in zip(sums, counts, strict=True)]


def apply_sys_summary(summary: SessionSummary, samples: list[Sample]) -> None:
    app = [s.cpu_app_pct for s in samples if s.cpu_app_pct is not None]
    total = [s.cpu_total_pct for s in samples if s.cpu_total_pct is not None]
    summary.avg_cpu_app_pct = sum(app) / len(app) if app else None
    summary.peak_cpu_app_pct = max(app) if app else None
    summary.avg_cpu_total_pct = sum(total) / len(total) if total else None
    summary.peak_mem_pss_mb = _peak(s.mem_pss_mb for s in samples)
    summary.peak_mem_rss_mb = _peak(s.mem_rss_mb for s in samples)
    gpu = [s.gpu_busy_pct for s in samples if s.gpu_busy_pct is not None]
    summary.avg_gpu_busy_pct = sum(gpu) / len(gpu) if gpu else None
    summary.peak_gpu_busy_pct = max(gpu) if gpu else None
    summary.peak_gpu_mem_mb = _peak(s.gpu_mem_mb for s in samples)
    summary.avg_cpu_freq_mhz = _avg_per_core([s.cpu_freq_mhz for s in samples])
    gpu_freq = [s.gpu_freq_mhz for s in samples if s.gpu_freq_mhz]
    summary.avg_gpu_freq_mhz = sum(gpu_freq) / len(gpu_freq) if gpu_freq else None
    summary.peak_gpu_freq_mhz = max(gpu_freq) if gpu_freq else None
    summary.avg_wakeups = _avg(s.wakeups for s in samples)
    summary.avg_cswitch = _avg(s.cswitch for s in samples)
    summary.avg_net_rx_kbps = _avg(s.net_rx_kbps for s in samples)
    summary.avg_net_tx_kbps = _avg(s.net_tx_kbps for s in samples)

    levels = [s.battery_level_pct for s in samples if s.battery_level_pct is not None]
    if levels:
        summary.battery_level_start, summary.battery_level_end = levels[0], levels[-1]
    charge = [s.battery_charge_mah for s in samples if s.battery_charge_mah is not None]
    if len(charge) >= 2:
        summary.battery_consumed_mah = round(charge[0] - charge[-1], 1)
    statuses = [s.battery_status for s in samples if s.battery_status is not None]
    if statuses:
        summary.battery_status = max(set(statuses), key=statuses.count)

    with_power = [s for s in samples if s.battery_plugged is not None]
    if with_power:
        plugged = sum(1 for s in with_power if s.battery_plugged)
        summary.plugged_ratio = plugged / len(with_power)
        unplugged = [
            abs(s.battery_power_w)
            for s in with_power
            if not s.battery_plugged and s.battery_power_w is not None
        ]
        summary.avg_power_w = sum(unplugged) / len(unplugged) if unplugged else None
        fpower = [s.fpower_mw for s in samples if s.fpower_mw is not None]
        summary.avg_fpower_mw = sum(fpower) / len(fpower) if fpower else None
        if plugged == len(with_power):
            summary.notes.append("全程接著電源，功耗數值為淨充電電流，不代表整機耗電；FPower 不計")
        elif plugged:
            summary.notes.append(
                f"{plugged}/{len(with_power)} 筆取樣接著電源，功耗只統計未接電源的部分"
            )


def apply_thermal_summary(summary: SessionSummary, samples: list[Sample]) -> None:
    """peak 溫度、time_to_throttle、throttle_duration、throttle_timeline（SPEC §12.6）。

    每筆 sample 代表 (上一筆 elapsed, 本筆 elapsed] 這段區間；第一筆從 0 起算。
    """
    summary.peak_cpu_c = _peak(s.cpu_c for s in samples)
    summary.peak_gpu_c = _peak(s.gpu_c for s in samples)
    summary.peak_skin_c = _peak(s.skin_c for s in samples)
    summary.peak_npu_c = _peak(s.npu_c for s in samples)

    timeline: list[tuple[float, int]] = []
    throttle_duration = 0.0
    time_to_throttle: float | None = None
    previous_elapsed = 0.0
    for sample in samples:
        status = sample.throttling_status
        if status is not None:
            if not timeline or timeline[-1][1] != status:
                timeline.append((sample.elapsed, status))
            if status >= ThrottlingStatus.MODERATE:
                throttle_duration += max(0.0, sample.elapsed - previous_elapsed)
                if time_to_throttle is None:
                    time_to_throttle = sample.elapsed
        previous_elapsed = sample.elapsed

    summary.throttle_timeline = timeline
    summary.throttle_duration_s = throttle_duration
    summary.time_to_throttle_s = time_to_throttle


def _avg(values: object) -> float | None:
    present = [v for v in values if isinstance(v, int | float)]  # type: ignore[attr-defined]
    return sum(present) / len(present) if present else None


def _peak(values: object) -> float | None:
    present = [v for v in values if isinstance(v, int | float)]  # type: ignore[attr-defined]
    return max(present) if present else None
