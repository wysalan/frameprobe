"""Perfetto trace 分析（SPEC §4）。

SQL 執行透過 QueryFn 注入，測試時不需要真的 trace。
真實路徑：open_trace() 用 perfetto 套件的 TraceProcessor。
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from .adb import FrameprobeError
from .jank import jank_flags
from .probe import DeviceInfo
from .sampler import Sample
from .storage import SessionSummary
from .summary import (
    apply_jank_summary,
    apply_stability_summary,
    apply_sys_summary,
    apply_thermal_summary,
)
from .thermal import _SYSFS_NAME_HINTS, ThrottlingStatus, normalize_sysfs_temp
from .timestats import Histogram

QueryFn = Callable[[str], list[dict[str, Any]]]

SURFACEFLINGER = "/system/bin/surfaceflinger"


class AnalysisError(FrameprobeError):
    pass


def _sql_str(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


# SPEC §4.1；{where} 由呼叫端代入（display_where / app_where）
FPS_SQL = """
WITH actual_present_times AS (
    SELECT (ts + dur) AS present_ts
    FROM actual_frame_timeline_slice
    WHERE ({where}) AND dur > 0
),
present_intervals AS (
    SELECT (LEAD(present_ts) OVER (ORDER BY present_ts ASC) - present_ts) / 1000000.0 AS p2p_ms
    FROM actual_present_times
),
valid_intervals AS (
    SELECT p2p_ms FROM present_intervals WHERE p2p_ms IS NOT NULL AND p2p_ms > 0
),
ordered_frames AS (
    SELECT p2p_ms,
           ROW_NUMBER() OVER (ORDER BY p2p_ms ASC) AS row_num,
           COUNT(1) OVER () AS total_frames
    FROM valid_intervals
)
SELECT
    (SELECT COUNT(1) FROM valid_intervals) AS total_presented_frames,
    ROUND(1000.0 / NULLIF((SELECT AVG(p2p_ms) FROM valid_intervals), 0), 2) AS average_fps,
    ROUND(1000.0 / NULLIF((SELECT p2p_ms FROM ordered_frames
        WHERE row_num = CAST(total_frames * 0.90 AS INT)), 0), 2) AS low_10_fps,
    ROUND(1000.0 / NULLIF((SELECT p2p_ms FROM ordered_frames
        WHERE row_num = CAST(total_frames * 0.99 AS INT)), 0), 2) AS low_1_fps;
"""

PRESENT_SQL = """
SELECT (ts + dur) AS present_ts
FROM actual_frame_timeline_slice
WHERE ({where}) AND dur > 0
ORDER BY present_ts ASC;
"""

# SPEC §4.2
JANK_SQL = """
SELECT jank_type, COUNT(*) AS cnt
FROM actual_frame_timeline_slice
WHERE ({where})
GROUP BY jank_type ORDER BY cnt DESC;
"""

JANK_FRAMES_SQL = """
SELECT ts, jank_type
FROM actual_frame_timeline_slice
WHERE ({where})
  AND jank_type IS NOT NULL AND jank_type != 'None';
"""

OVERWRITE_SQL = """
SELECT name, value FROM stats
WHERE name IN ('traced_buf_bytes_overwritten', 'traced_buf_write_wrap_count',
               'traced_chunks_discarded')
  AND value > 0;
"""

# 顯示層級 = SurfaceFlinger 的 DisplayFrame：layer_name 為空、沒有 surface token。
# 不用 process 名稱判斷，因為 ring buffer 覆寫後 process 中繼資料會遺失（真機驗證：Pixel 11 Pro）。
DISPLAY_WHERE = "layer_name IS NULL AND surface_frame_token IS NULL"


def app_where(package: str) -> str:
    """App 層級：process 名稱等於 package，或 layer 名稱含 package（process 名稱遺失時的備援）。"""
    quoted = _sql_str(package)
    like = _sql_str(f"%{package}%")
    return f"upid IN (SELECT upid FROM process WHERE name = {quoted}) OR layer_name LIKE {like}"


BOUNDS_SQL = "SELECT start_ts, end_ts FROM trace_bounds;"
COUNTER_TRACKS_SQL = "SELECT id, name, type FROM counter_track;"
COUNTER_VALUES_SQL = "SELECT ts, value FROM counter WHERE track_id = {track_id} ORDER BY ts ASC;"


@dataclass
class FpsStats:
    total_frames: int = 0
    average_fps: float | None = None
    p90_fps: float | None = None
    p99_fps: float | None = None


@dataclass
class AnalysisResult:
    display: FpsStats
    app: FpsStats | None
    app_level_available: bool
    jank_breakdown: dict[str, int] | None
    samples: list[Sample]
    duration_s: float
    thermal_timeline: str | None
    notes: list[str] = field(default_factory=list)


# --------------------------------------------------------------------------- #


@contextmanager
def open_trace(path: Path | str) -> Iterator[QueryFn]:
    """以 perfetto 套件開啟 trace；第一次使用會自動下載 trace_processor_shell。"""
    try:
        from perfetto.trace_processor import TraceProcessor
    except ImportError as exc:
        raise AnalysisError("本機未安裝 perfetto 套件，無法分析 trace。") from exc
    if not Path(path).exists():
        raise AnalysisError(f"找不到 trace 檔：{path}")
    try:
        tp = TraceProcessor(trace=str(path))
    except Exception as exc:  # perfetto 拋的是各種裸例外
        raise AnalysisError(f"無法載入 trace（{path}）：{exc}") from exc
    try:
        yield lambda sql: [dict(row.__dict__) for row in tp.query(sql)]
    finally:
        tp.close()


def _fps_stats(query: QueryFn, where: str) -> FpsStats:
    rows = query(FPS_SQL.format(where=where))
    if not rows:
        return FpsStats()
    row = rows[0]
    return FpsStats(
        total_frames=int(row.get("total_presented_frames") or 0),
        average_fps=row.get("average_fps"),
        p90_fps=row.get("low_10_fps"),
        p99_fps=row.get("low_1_fps"),
    )


def analyze(
    query: QueryFn,
    *,
    package: str | None,
    thermal_poll: list[dict[str, Any]] | None = None,
    thermal_timeline_hint: str | None = None,
    sys_poll: list[dict[str, Any]] | None = None,
) -> AnalysisResult:
    notes: list[str] = []
    bounds = query(BOUNDS_SQL)
    t0 = int(bounds[0]["start_ts"]) if bounds else 0
    t1 = int(bounds[0]["end_ts"]) if bounds else 0
    duration_s = max(0.0, (t1 - t0) / 1e9)

    display = _fps_stats(query, DISPLAY_WHERE)
    if display.total_frames == 0:
        raise AnalysisError(
            "trace 裡沒有任何 SurfaceFlinger 的 actual_frame_timeline_slice。\n"
            "可能原因：frametimeline 資料來源未啟用、錄製期間畫面沒有更新、或裝置不支援。"
        )
    for row in query(OVERWRITE_SQL):
        if row.get("name") == "traced_buf_bytes_overwritten":
            notes.append(
                f"trace buffer 溢位：{int(row['value']) / 1e6:.0f} MB 被覆寫，"
                f"只保留最後 {duration_s:.0f} 秒；請縮短錄製或改用較新的版本（write_into_file）"
            )

    app: FpsStats | None = None
    jank: dict[str, int] | None = None
    app_available = False
    present_where = DISPLAY_WHERE
    if package:
        where = app_where(package)
        app = _fps_stats(query, where)
        jank_rows = query(JANK_SQL.format(where=where))
        if app.total_frames > 0 or jank_rows:
            app_available = True
            jank = {str(r["jank_type"]): int(r["cnt"]) for r in jank_rows}
            if app.total_frames > 0.5 * display.total_frames:
                present_where = where
            elif app.total_frames > 0:
                notes.append(
                    f"App 層級只有 {app.total_frames} 幀（顯示層級 {display.total_frames}），"
                    "FrameTimeline 對此 SurfaceView 僅零星回報，逐秒資料以顯示層級為準"
                )
        else:
            app = None
            notes.append(
                f"App 層級查詢（process.name = {package}）回傳 0 列，已回退到顯示層級。"
                "FrameTimeline 對 SurfaceView 的支援不完整，這對純 SurfaceView 遊戲是預期行為。"
            )

    samples = _per_second_samples(query, present_where, t0)
    if app_available:
        _attach_jank(samples, query, app_where(package or ""), t0)

    thermal_poll = _align_poll_end(thermal_poll, duration_s, notes, "溫度")
    sys_poll = _align_poll_end(sys_poll, duration_s, notes, "CPU/記憶體")
    thermal_timeline = _attach_trace_thermal(samples, query, t0, notes)
    if thermal_timeline == "exact" and thermal_poll:
        _attach_polled_thermal(samples, thermal_poll, only_status=True)
        notes.append(
            "節流狀態取自錄製期間平行 poll 的 dumpsys thermalservice（ftrace 沒有 Thermal Status）"
        )
    if thermal_timeline is None and thermal_poll:
        _attach_polled_thermal(samples, thermal_poll)
        thermal_timeline = "approximate"
        notes.append("溫度取自錄製期間平行 poll 的 dumpsys thermalservice，時間軸為近似對齊")
    elif thermal_timeline is None and thermal_timeline_hint:
        notes.append("錄製時標記為有溫度資料，但 trace 中找不到 thermal counter track")
    if sys_poll:
        _attach_polled_sys(samples, sys_poll)
        notes.append("CPU/記憶體取自錄製期間每 2 秒 poll 的 procfs，時間軸為近似對齊")
        pss_err = next((p["pss_error"] for p in sys_poll if p.get("pss_error")), None)
        if pss_err:
            notes.append(f"PSS 不可讀（{str(pss_err).splitlines()[0]}），記憶體改用 statm 的 RSS")

    return AnalysisResult(
        display=display,
        app=app,
        app_level_available=app_available,
        jank_breakdown=jank,
        samples=samples,
        duration_s=duration_s,
        thermal_timeline=thermal_timeline,
        notes=notes,
    )


def _align_poll_end(
    points: list[dict[str, Any]] | None, duration_s: float, notes: list[str], label: str
) -> list[dict[str, Any]] | None:
    """poll 點的 elapsed 從 session 開始算；trace 若因 buffer 覆寫只剩尾段，兩者起點不同。

    以「尾端對齊」處理：poll 最後一點對到 trace 結尾，往前平移，落在 trace 之前的點丟掉。
    """
    if not points:
        return points
    last = max(float(p["elapsed"]) for p in points)
    shift = last - duration_s
    if shift <= 5.0:
        return points
    shifted = [
        {**p, "elapsed": round(float(p["elapsed"]) - shift, 3)}
        for p in points
        if float(p["elapsed"]) - shift >= 0
    ]
    notes.append(
        f"{label}取樣涵蓋 {last:.0f} 秒但 trace 只有 {duration_s:.0f} 秒，"
        f"已將取樣時間軸尾端對齊 trace（平移 {shift:.0f} 秒）"
    )
    return shifted


def _per_second_samples(query: QueryFn, where: str, t0: int) -> list[Sample]:
    """把逐幀 present 時間切成每秒一筆 Sample，讓 record 模式也能畫 FPS 曲線。

    Jank 用逐幀序列精確計算（前 3 幀平均 2 倍規則，見 jank.py），再依幀所在的秒數歸戶。
    """
    rows = query(PRESENT_SQL.format(where=where))
    present = [int(r["present_ts"]) for r in rows]
    frame_ms: list[float] = []
    frame_sec: list[int] = []
    for prev, curr in zip(present, present[1:], strict=False):
        p2p_ms = (curr - prev) / 1e6
        if p2p_ms <= 0:
            continue
        frame_ms.append(p2p_ms)
        frame_sec.append(int((curr - t0) // 1_000_000_000))
    flags = jank_flags(frame_ms)

    per_sec: dict[int, dict[int, int]] = {}
    jank_per_sec: dict[int, list[float]] = {}
    for ms, sec, flag in zip(frame_ms, frame_sec, flags, strict=True):
        bucket = per_sec.setdefault(sec, {})
        key = round(ms)
        bucket[key] = bucket.get(key, 0) + 1
        if flag:
            jank_per_sec.setdefault(sec, []).append(ms if flag == 1 else -ms)  # 負值標 BigJank

    samples = []
    for sec in sorted(per_sec):
        hist = Histogram(per_sec[sec])
        frames = hist.total
        janks = jank_per_sec.get(sec, [])
        samples.append(
            Sample(
                ts=(t0 / 1e9) + sec + 1,
                elapsed=float(sec + 1),
                frames=frames,
                fps=float(frames),
                p90_fps=hist.percentile_fps(0.90),
                p99_fps=hist.percentile_fps(0.99),
                dropped=0,
                dropped_ratio=0.0,
                histogram=dict(hist.buckets),
                jank=len(janks),
                big_jank=sum(1 for j in janks if j < 0),
                stutter_ms=sum(abs(j) for j in janks),
                jank_method="exact",
            )
        )
    return samples


def _attach_jank(samples: list[Sample], query: QueryFn, where: str, t0: int) -> None:
    by_sec: dict[int, int] = {}
    for row in query(JANK_FRAMES_SQL.format(where=where)):
        sec = int((int(row["ts"]) - t0) // 1_000_000_000)
        by_sec[sec] = by_sec.get(sec, 0) + 1
    for sample in samples:
        sample.dropped = by_sec.get(int(sample.elapsed) - 1, 0)
        denom = sample.frames + sample.dropped
        sample.dropped_ratio = sample.dropped / denom if denom else 0.0


def _thermal_type(name: str) -> int:
    lowered = name.lower()
    return next((tid for hint, tid in _SYSFS_NAME_HINTS if hint in lowered), -1)


def _attach_trace_thermal(
    samples: list[Sample], query: QueryFn, t0: int, notes: list[str]
) -> str | None:
    """從 counter 表撈 thermal_temperature 的 counter track，對齊到每秒 Sample。

    ftrace 的 temp 欄位單位依 kernel 而異，沿用 sysfs 的推斷規則（1 → 10 → 1000）。
    ftrace 沒有 Thermal Status，所以 throttling_status 維持 None。
    """
    # 只認 trace_processor 標成 thermal_temperature 的 track。曾經用名稱提示去撈所有 counter，
    # 結果把 "GPU Memory"（process_gpu_memory，單位 bytes）當成 GPU 溫度：61440 ÷ 1000 = 61.44°C，
    # 還因此判定「ftrace 有溫度」而丟掉 thermalservice 的真實讀數。
    tracks = [t for t in query(COUNTER_TRACKS_SQL) if t.get("type") == "thermal_temperature"]
    if not tracks:
        return None
    # sec -> type_id -> max °C
    per_sec: dict[int, dict[int, float]] = {}
    for track in tracks:
        type_id = _thermal_type(str(track.get("name") or ""))
        if type_id < 0:
            continue
        for row in query(COUNTER_VALUES_SQL.format(track_id=int(track["id"]))):
            celsius = normalize_sysfs_temp(int(float(row["value"])))
            if celsius is None:
                continue
            sec = int((int(row["ts"]) - t0) // 1_000_000_000)
            slot = per_sec.setdefault(sec, {})
            slot[type_id] = max(slot.get(type_id, celsius), celsius)
    if not per_sec:
        return None
    last: dict[int, float] = {}
    for sample in samples:
        sec = int(sample.elapsed) - 1
        if sec in per_sec:
            last.update(per_sec[sec])
            sample.thermal_stale = False
        else:
            sample.thermal_stale = True
        sample.cpu_c, sample.gpu_c = last.get(0), last.get(1)
        sample.battery_c, sample.skin_c = last.get(2), last.get(3)
    notes.append("溫度取自 ftrace thermal_temperature（與逐幀資料同一時間軸）；ftrace 無節流狀態")
    return "exact"


def _attach_polled_thermal(
    samples: list[Sample], points: list[dict[str, Any]], *, only_status: bool = False
) -> None:
    """把 thermalservice 的輪詢點對到每秒 Sample。

    only_status 時只補節流狀態，溫度已由 ftrace 提供。
    """
    ordered = sorted(points, key=lambda p: float(p["elapsed"]))
    idx = 0
    current: dict[str, Any] | None = None
    for sample in samples:
        fresh = False
        while idx < len(ordered) and float(ordered[idx]["elapsed"]) <= sample.elapsed:
            current = ordered[idx]
            idx += 1
            fresh = True
        if current is None:
            continue
        if not only_status:
            sample.cpu_c, sample.gpu_c = current.get("cpu_c"), current.get("gpu_c")
            sample.skin_c, sample.battery_c = current.get("skin_c"), current.get("battery_c")
            sample.npu_c = current.get("npu_c")
        status = current.get("status")
        if status is not None:
            parsed = ThrottlingStatus.parse(int(status))
            sample.throttling_status = int(status)
            sample.throttling_label = parsed.label if parsed is not None else f"STATUS_{status}"
        if not only_status:
            sample.thermal_stale = not fresh


_SYS_FIELDS = (
    "cpu_total_pct",
    "cpu_app_pct",
    "cpu_app_norm_pct",
    "cpu_freq_mhz",
    "mem_pss_mb",
    "mem_rss_mb",
    "mem_swap_mb",
    "mem_available_mb",
    "gpu_busy_pct",
    "gpu_freq_mhz",
    "gpu_mem_mb",
    "wakeups",
    "cswitch",
    "net_rx_kbps",
    "net_tx_kbps",
    "mem_detail_mb",
    "battery_voltage_v",
    "battery_current_ma",
    "battery_power_w",
    "battery_plugged",
    "battery_level_pct",
    "battery_charge_mah",
    "battery_status",
)


def _attach_polled_sys(samples: list[Sample], points: list[dict[str, Any]]) -> None:
    ordered = sorted(points, key=lambda p: float(p["elapsed"]))
    idx = 0
    current: dict[str, Any] | None = None
    for sample in samples:
        while idx < len(ordered) and float(ordered[idx]["elapsed"]) <= sample.elapsed:
            current = ordered[idx]
            idx += 1
        if current is None:
            continue
        for key in _SYS_FIELDS:
            setattr(
                sample,
                key,
                current.get(key) if key != "cpu_freq_mhz" else list(current.get(key) or []),
            )
        if sample.battery_plugged is False and sample.battery_power_w and sample.fps:
            sample.fpower_mw = round(abs(sample.battery_power_w) * 1000 / sample.fps, 2)


def build_record_summary(
    result: AnalysisResult,
    *,
    session_id: str,
    device: DeviceInfo,
    package: str,
    started_at: datetime,
    extra_notes: list[str] | None = None,
) -> SessionSummary:
    # App 層級幀數若不到顯示層級的一半，代表 FrameTimeline 對這個 SurfaceView 只抓到零星資料，
    # 以顯示層級為準（jank 歸因仍保留）
    use_app = bool(result.app and result.app.total_frames > 0.5 * result.display.total_frames)
    stats = result.app if use_app and result.app else result.display
    summary = SessionSummary(
        session_id=session_id,
        device=device,
        package=package,
        layer_name="(perfetto: app 層級)" if use_app else "(perfetto: 顯示層級)",
        mode="record",
        started_at=started_at,
        duration_s=result.duration_s,
        total_frames=stats.total_frames,
        average_fps=stats.average_fps,
        p90_fps=stats.p90_fps,
        p99_fps=stats.p99_fps,
        dropped_frames=sum(
            v for k, v in (result.jank_breakdown or {}).items() if k not in ("None", "")
        ),
        jank_breakdown=result.jank_breakdown,
        app_level_available=result.app_level_available,
        thermal_timeline=result.thermal_timeline,  # type: ignore[arg-type]
        notes=list(dict.fromkeys([*(extra_notes or []), *result.notes])),  # 重新分析時去重
        samples=result.samples,
    )
    apply_thermal_summary(summary, result.samples)
    apply_sys_summary(summary, result.samples)
    apply_jank_summary(summary, result.samples)
    apply_stability_summary(summary, result.samples)
    return summary
