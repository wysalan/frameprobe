"""Jank / BigJank / Stutter。純函式。

定義（採用 PerfDog 公開文件的演算法，見 docs/metrics.md「指標定義的參考來源」）：
  Jank    ：當前幀耗時 > 前 3 幀平均的 2 倍，且 > 83.33ms（電影幀 ×2）
  BigJank ：當前幀耗時 > 前 3 幀平均的 2 倍，且 > 125ms（電影幀 ×3）
  Stutter ：卡頓時長（Jank 幀耗時總和）÷ 總時長

「前 3 幀」需要逐幀序列，只有 record 模式（Perfetto）有。
即時模式的 timestats 只有直方圖，做不到相對比較，只能用絕對門檻近似：
  幀耗時 > 83.33ms 視為 Jank、> 125ms 視為 BigJank。
近似版一律標 method="histogram"，讓使用者知道兩者不可直接比較。
"""

from __future__ import annotations

from dataclasses import dataclass

JANK_MS = 1000 / 12  # 83.33
BIG_JANK_MS = 125.0


@dataclass
class JankStats:
    jank: int = 0
    big_jank: int = 0
    stutter_ms: float = 0.0
    method: str = "exact"  # "exact" | "histogram"

    def merge(self, other: JankStats) -> JankStats:
        return JankStats(
            self.jank + other.jank,
            self.big_jank + other.big_jank,
            self.stutter_ms + other.stutter_ms,
            self.method if self.method == other.method else "mixed",
        )


def jank_from_frame_times(frame_ms: list[float]) -> JankStats:
    """逐幀精確版。frame_ms 依時間順序。"""
    stats = JankStats(method="exact")
    for i in range(3, len(frame_ms)):
        ft = frame_ms[i]
        avg3 = (frame_ms[i - 1] + frame_ms[i - 2] + frame_ms[i - 3]) / 3
        if ft > 2 * avg3 and ft > JANK_MS:
            stats.jank += 1
            stats.stutter_ms += ft
            if ft > BIG_JANK_MS:
                stats.big_jank += 1
    return stats


def jank_flags(frame_ms: list[float]) -> list[int]:
    """每一幀的 jank 等級：0 無、1 Jank、2 BigJank。供 record 模式切成每秒統計。"""
    flags = [0] * len(frame_ms)
    for i in range(3, len(frame_ms)):
        ft = frame_ms[i]
        avg3 = (frame_ms[i - 1] + frame_ms[i - 2] + frame_ms[i - 3]) / 3
        if ft > 2 * avg3 and ft > JANK_MS:
            flags[i] = 2 if ft > BIG_JANK_MS else 1
    return flags


def jank_from_histogram(buckets: dict[int, int]) -> JankStats:
    """直方圖近似版（即時模式）。"""
    stats = JankStats(method="histogram")
    for ms, count in buckets.items():
        if ms > JANK_MS and count > 0:
            stats.jank += count
            stats.stutter_ms += ms * count
            if ms > BIG_JANK_MS:
                stats.big_jank += count
    return stats
