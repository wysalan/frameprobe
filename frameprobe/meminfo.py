"""`dumpsys meminfo <pkg>` 解析（Memory Detail）。純函式。

格式依真機樣本 tests/fixtures/meminfo_samsung_s23.txt（Samsung S23 / Android 14）。
取兩個區段：
  - 上方明細表的 `Pss Total` 欄（Native Heap、Dalvik Heap、EGL mtrack、GL mtrack …）
  - `App Summary` 的分類（Java Heap、Native Heap、Code、Stack、Graphics、Private Other、System）
    與 TOTAL PSS / RSS / SWAP PSS
單位 KB → 回傳 MB。
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass, field

from .adb import AdbClient, FrameprobeError


class MeminfoError(FrameprobeError):
    pass


_TABLE_ROW_RE = re.compile(
    r"^\s*([A-Za-z.][A-Za-z. ]*?)\s{2,}(\d+)\s+(\d+)\s+(\d+)\s+(\d+)\s+(\d+)"
)
_SUMMARY_ROW_RE = re.compile(r"^\s+([A-Za-z ]+?):\s+(\d+)(?:\s+(\d+))?\s*$")
_TOTALS_RE = re.compile(r"TOTAL PSS:\s+(\d+)\s+TOTAL RSS:\s+(\d+)(?:\s+TOTAL SWAP PSS:\s+(\d+))?")

_TABLE_KEYS = {
    "Native Heap": "native_heap",
    "Dalvik Heap": "dalvik_heap",
    ".so mmap": "so_mmap",
    "Other mmap": "other_mmap",
    "EGL mtrack": "egl_mtrack",
    "GL mtrack": "gl_mtrack",
    "Unknown": "unknown",
}
_SUMMARY_KEYS = {
    "Java Heap": "java_heap",
    "Native Heap": "native_heap",
    "Code": "code",
    "Stack": "stack",
    "Graphics": "graphics",
    "Private Other": "private_other",
    "System": "system",
}


@dataclass
class MemDetail:
    ts: float
    pid: int | None = None
    total_pss_mb: float | None = None
    total_rss_mb: float | None = None
    total_swap_pss_mb: float | None = None
    summary_mb: dict[str, float] = field(default_factory=dict)
    """App Summary 的分類（PSS）。"""
    table_mb: dict[str, float] = field(default_factory=dict)
    """明細表選定列的 Pss Total。"""
    raw: str = ""

    def to_dict(self) -> dict[str, float]:
        out = {f"{k}": v for k, v in self.summary_mb.items()}
        for key in ("egl_mtrack", "gl_mtrack"):
            if key in self.table_mb:
                out[key] = self.table_mb[key]
        if self.total_pss_mb is not None:
            out["total_pss"] = self.total_pss_mb
        if self.total_swap_pss_mb is not None:
            out["total_swap_pss"] = self.total_swap_pss_mb
        return out


def parse_meminfo(text: str, *, ts: float | None = None) -> MemDetail:
    detail = MemDetail(ts=ts or time.time(), raw=text)
    pid_match = re.search(r"\*\* MEMINFO in pid (\d+)", text)
    if pid_match:
        detail.pid = int(pid_match.group(1))

    in_summary = False
    for line in text.splitlines():
        if "App Summary" in line:
            in_summary = True
            continue
        if in_summary:
            totals = _TOTALS_RE.search(line)
            if totals:
                detail.total_pss_mb = int(totals.group(1)) / 1024
                detail.total_rss_mb = int(totals.group(2)) / 1024
                if totals.group(3):
                    detail.total_swap_pss_mb = int(totals.group(3)) / 1024
                in_summary = False
                continue
            row = _SUMMARY_ROW_RE.match(line)
            if row and row.group(1).strip() in _SUMMARY_KEYS:
                detail.summary_mb[_SUMMARY_KEYS[row.group(1).strip()]] = int(row.group(2)) / 1024
            continue
        row = _TABLE_ROW_RE.match(line)
        if row and row.group(1).strip() in _TABLE_KEYS:
            detail.table_mb[_TABLE_KEYS[row.group(1).strip()]] = int(row.group(2)) / 1024

    if detail.total_pss_mb is None and not detail.table_mb:
        raise MeminfoError(
            "無法從 dumpsys meminfo 解析出任何記憶體數字。"
            "可能是 package 不在執行中（輸出為 'No process found'）或格式不同。"
        )
    return detail


class MemDetailCollector:
    """每 min_interval 秒才真正打一次 dumpsys meminfo（它會向 App 程序要資料，比 procfs 重）。"""

    def __init__(self, adb: AdbClient, package: str, *, min_interval: float = 2.0) -> None:
        self.adb = adb
        self.command = f"dumpsys meminfo '{package.replace(chr(39), '')}'"
        self.min_interval = min_interval
        self._last: MemDetail | None = None
        self._last_fetch = 0.0
        self.failures = 0
        self.raw_snapshots: list[str] = []

    def read(self, *, force: bool = False) -> MemDetail | None:
        now = time.time()
        if not force and self._last and (now - self._last_fetch) < self.min_interval:
            return self._last
        result = self.adb.shell(self.command, timeout=20.0)
        if not result.ok or not result.stdout.strip():
            self.failures += 1
            return self._last
        self.raw_snapshots.append(result.stdout)
        try:
            self._last = parse_meminfo(result.stdout)
            self._last_fetch = now
            self.failures = 0
        except MeminfoError:
            self.failures += 1
        return self._last
