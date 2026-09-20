"""Session 儲存層。

目錄結構：
    sessions/<session_id>/
        summary.json     SessionSummary（不含 samples）
        samples.jsonl    每行一個 Sample（僅 realtime）
        raw/             每次 dumpsys 的原始文字、trace.pb。新機型除錯全靠這個。
"""

from __future__ import annotations

import csv
import io
import json
import os
import re
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Literal

from pydantic import TypeAdapter

from .adb import FrameprobeError
from .probe import DeviceInfo
from .sampler import Sample


class SessionNotFoundError(FrameprobeError):
    pass


@dataclass
class SessionSummary:
    session_id: str
    device: DeviceInfo
    package: str
    layer_name: str
    mode: Literal["realtime", "record"]
    started_at: datetime
    duration_s: float = 0.0
    total_frames: int = 0
    average_fps: float | None = None
    p90_fps: float | None = None
    p99_fps: float | None = None
    dropped_frames: int = 0
    jank_breakdown: dict[str, int] | None = None  # 僅 record
    app_level_available: bool | None = None  # 僅 record（SPEC §4.3）
    # --- 熱（SPEC §12.6）---
    peak_cpu_c: float | None = None
    peak_gpu_c: float | None = None
    peak_skin_c: float | None = None
    peak_npu_c: float | None = None
    time_to_throttle_s: float | None = None
    throttle_duration_s: float = 0.0
    throttle_timeline: list[tuple[float, int]] = field(default_factory=list)
    thermal_timeline: Literal["exact", "approximate"] | None = None  # 僅 record（SPEC §12.7）
    # --- CPU / 記憶體 ---
    avg_cpu_app_pct: float | None = None
    peak_cpu_app_pct: float | None = None
    avg_cpu_total_pct: float | None = None
    peak_mem_pss_mb: float | None = None
    peak_mem_rss_mb: float | None = None
    avg_gpu_busy_pct: float | None = None
    peak_gpu_busy_pct: float | None = None
    peak_gpu_mem_mb: float | None = None
    avg_cpu_freq_mhz: list[float] = field(
        default_factory=list
    )  # 每核心平均，順序同 Sample.cpu_freq_mhz
    avg_gpu_freq_mhz: float | None = None
    peak_gpu_freq_mhz: float | None = None
    avg_wakeups: float | None = None
    avg_cswitch: float | None = None
    avg_net_rx_kbps: float | None = None
    avg_net_tx_kbps: float | None = None
    # --- 功耗：只統計未接電源的 sample；全程接著 USB 則為 None 並在 notes 說明 ---
    avg_power_w: float | None = None
    avg_fpower_mw: float | None = None
    plugged_ratio: float | None = None
    battery_level_start: int | None = None
    battery_level_end: int | None = None
    battery_consumed_mah: float | None = None
    """Charge counter 開始減結束；放電為正，接著電源時可能為負。"""
    battery_status: int | None = None
    """這段期間最常見的 BatteryManager 狀態（2 充電、3 放電、4 未充電、5 已充滿）。"""
    # --- Jank / Stutter（定義見 jank.py）---
    jank_total: int = 0
    big_jank_total: int = 0
    jank_per_10min: float | None = None
    big_jank_per_10min: float | None = None
    stutter_ratio: float | None = None
    jank_method: str | None = None  # "exact"（record）| "histogram"（即時近似）
    # --- 穩定度：Std(FPS)、Std(FTime)、Drop(FPS)、Delta(FTime) ---
    low_1_fps: float | None = None
    """1% Low：最慢 1% 幀的平均幀間隔換算成 FPS。"""
    fps_std: float | None = None
    ftime_avg_ms: float | None = None
    ftime_std_ms: float | None = None
    drop_fps: int = 0
    """相鄰兩筆 sample 的 FPS 下降超過 8 的次數。"""
    delta_ftime: int = 0
    """幀耗時 > 100ms 的幀數。"""
    markers: list[tuple[float, str]] = field(default_factory=list)
    """場景標籤 (elapsed, label)，由使用者在採集中或事後加上。"""
    notes: list[str] = field(default_factory=list)
    """任何 fallback 都要寫在這裡讓使用者看得到（禁止靜默降級）。"""
    samples: list[Sample] = field(default_factory=list)  # 僅 realtime；不寫進 summary.json


_SUMMARY = TypeAdapter(SessionSummary)
_SAMPLE = TypeAdapter(Sample)


def summary_to_dict(summary: SessionSummary, *, with_samples: bool = False) -> dict[str, Any]:
    data: dict[str, Any] = _SUMMARY.dump_python(
        summary, mode="json", exclude={"samples": True, "device": {"specs": {"raw": True}}}
    )
    if summary.device.specs is not None:
        data["device"]["specs"] = summary.device.specs.to_dict()
    if with_samples:
        data["samples"] = [s.to_dict() for s in summary.samples]
    return data


SAMPLE_COLUMNS = (
    "ts",
    "elapsed",
    "frames",
    "fps",
    "p90_fps",
    "p99_fps",
    "dropped",
    "dropped_ratio",
    "cpu_c",
    "gpu_c",
    "battery_c",
    "skin_c",
    "npu_c",
    "throttling_status",
    "throttling_label",
    "thermal_stale",
    "cpu_total_pct",
    "cpu_app_pct",
    "cpu_app_norm_pct",
    "mem_pss_mb",
    "mem_rss_mb",
    "mem_swap_mb",
    "mem_available_mb",
)


def export_session(summary: SessionSummary, fmt: str) -> str:
    """csv：每行一筆 sample（直方圖省略）；json：summary 含 samples。"""
    if fmt == "json":
        return json.dumps(summary_to_dict(summary, with_samples=True), ensure_ascii=False, indent=2)
    if fmt == "csv":
        buf = io.StringIO()
        writer = csv.DictWriter(buf, fieldnames=SAMPLE_COLUMNS, extrasaction="ignore")
        writer.writeheader()
        for sample in summary.samples:
            writer.writerow(sample.to_dict())
        return buf.getvalue()
    raise ValueError(f"不支援的格式：{fmt}（可用 csv / json）")


def slugify(text: str) -> str:
    """layer name 含 / @ # 等字元，不能直接當檔名。"""
    return re.sub(r"[^A-Za-z0-9._-]+", "_", text).strip("_")[:80] or "unnamed"


def new_session_id(package: str, now: datetime | None = None) -> str:
    stamp = (now or datetime.now()).strftime("%Y%m%d-%H%M%S")
    return f"{stamp}-{slugify(package)}-{uuid.uuid4().hex[:6]}"


class SessionStore:
    def __init__(self, root: Path | str | None = None) -> None:
        self.root = Path(root or os.environ.get("FRAMEPROBE_SESSIONS", "sessions"))

    def path(self, session_id: str) -> Path:
        if "/" in session_id or session_id in ("", ".", ".."):
            raise SessionNotFoundError(f"不合法的 session id：{session_id!r}")
        return self.root / session_id

    def create(self, session_id: str) -> Path:
        path = self.path(session_id)
        (path / "raw").mkdir(parents=True, exist_ok=True)
        return path

    # --- 寫入 ------------------------------------------------------------- #

    def write_raw(self, session_id: str, name: str, text: str) -> Path:
        target = self.path(session_id) / "raw" / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8")
        return target

    def append_sample(self, session_id: str, sample: Sample) -> None:
        with (self.path(session_id) / "samples.jsonl").open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(sample.to_dict(), ensure_ascii=False) + "\n")

    def write_summary(self, session_id: str, summary: SessionSummary) -> Path:
        target = self.path(session_id) / "summary.json"
        target.write_text(
            json.dumps(summary_to_dict(summary), ensure_ascii=False, indent=2), encoding="utf-8"
        )
        return target

    # --- 讀取 ------------------------------------------------------------- #

    def load_samples(self, session_id: str) -> list[Sample]:
        target = self.path(session_id) / "samples.jsonl"
        if not target.exists():
            return []
        with target.open(encoding="utf-8") as fh:
            return [_SAMPLE.validate_json(line) for line in fh if line.strip()]

    def load_summary(self, session_id: str) -> SessionSummary:
        target = self.path(session_id) / "summary.json"
        if not target.exists():
            raise SessionNotFoundError(f"找不到 session：{session_id}（{target}）")
        return _SUMMARY.validate_json(target.read_text(encoding="utf-8"))

    def load_session(self, session_id: str) -> SessionSummary:
        summary = self.load_summary(session_id)
        summary.samples = self.load_samples(session_id)
        return summary

    def remove_session(self, session_id: str) -> Path:
        """把整個 session 資料夾移到 root/removed/ 下；不刪任何檔案，要釋放空間得手動刪。"""
        src = self.path(session_id)
        if not src.is_dir():
            raise SessionNotFoundError(f"找不到 session：{session_id}")
        dest_dir = self.root / "removed"
        dest_dir.mkdir(parents=True, exist_ok=True)
        dest = dest_dir / session_id
        if dest.exists():  # 同名（例如之前移過又復原）就加序號
            n = 2
            while (dest_dir / f"{session_id}-{n}").exists():
                n += 1
            dest = dest_dir / f"{session_id}-{n}"
        src.rename(dest)
        return dest

    def list_sessions(self) -> list[SessionSummary]:
        """由新到舊。沒有 summary.json 的（例如採集中或中斷）跳過。"""
        if not self.root.exists():
            return []
        found = []
        for child in self.root.iterdir():
            if (child / "summary.json").exists():
                try:
                    found.append(self.load_summary(child.name))
                except (ValueError, FrameprobeError):
                    continue
        found.sort(key=lambda s: s.started_at, reverse=True)
        return found
