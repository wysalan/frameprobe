"""`dumpsys SurfaceFlinger --timestats` 的解析器。

這個模組是純函式，沒有任何 I/O。輸入是 dumpsys 的原始文字，輸出是資料結構。

設計重點（對應 SPEC.md §2.2）：
- 直方圖鍵名在不同 Android 版本／文件中有 present2present 與 presentToPresent
  兩種拼法，一律正規化後比對。
- 直方圖 bucket 會跨行，且間距非等距，必須一路收集 token 到下一個區段標題為止。
- 百分位依 Google 官方定義：累積到 N% 的 bucket，換算成 1000/bucket_ms。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from .adb import FrameprobeError


class TimestatsParseError(FrameprobeError):
    pass


# `16ms=850` 這種 token
_BUCKET_RE = re.compile(r"(\d+)ms=(\d+)")
# `key = value` 這種行
_KV_RE = re.compile(r"^\s*([A-Za-z0-9_]+)\s*=\s*(.*)$")
# `xxx histogram is as below:` 這種區段標題
_HISTOGRAM_HEADER_RE = re.compile(r"^\s*(.+?)\s+histogram is as below:\s*$", re.IGNORECASE)

# 不該被當成遊戲主畫面的 layer
_LAYER_BLOCKLIST = (
    "splash",
    "starting",
    "dim",
    "wallpaper",
    "navigationbar",
    "statusbar",
    "inputmethod",
    "screenshot",
    "toast",
)


def _normalize_key(raw: str) -> str:
    """把 'presentToPresent' / 'present2present' / 'Present To Present' 收斂成同一個 key。

    做法：轉小寫、移除所有非英數字元、再把阿拉伯數字 2 視為 'to'。
    """
    collapsed = re.sub(r"[^a-z0-9]", "", raw.lower())
    # present2present -> presenttopresent
    collapsed = collapsed.replace("2", "to")
    return collapsed


@dataclass
class Histogram:
    """frame time 直方圖。key 是 ms bucket，value 是落在該 bucket 的幀數。"""

    buckets: dict[int, int] = field(default_factory=dict)

    @property
    def total(self) -> int:
        return sum(self.buckets.values())

    def mean_ms(self) -> float | None:
        """以 bucket 中心（這裡直接用 bucket 值）估算平均 frame time。"""
        total = self.total
        if total == 0:
            return None
        weighted = sum(ms * count for ms, count in self.buckets.items())
        return weighted / total

    def percentile_fps(self, percentile: float) -> float | None:
        """依 Google 官方定義計算 PXX FPS。

        percentile 傳 0.90 代表 P90。回傳 None 表示資料不足以計算。

        注意：bucket 為 0ms 時不能除零，遇到就繼續往下一個 bucket 找。
        這在「所有幀都落在 0ms bucket」的退化情況下會回傳 None。
        """
        if not 0 < percentile <= 1:
            raise ValueError("percentile 必須落在 (0, 1]")

        total = self.total
        if total == 0:
            return None

        target = total * percentile
        cumulative = 0
        for ms_bucket in sorted(self.buckets):
            cumulative += self.buckets[ms_bucket]
            if cumulative >= target:
                if ms_bucket <= 0:
                    # 0ms bucket 無法換算 FPS，往下一個 bucket 繼續找
                    continue
                return 1000.0 / ms_bucket
        return None

    def low_percent_fps(self, fraction: float) -> float | None:
        """「N% Low」：最慢 fraction 比例的幀，其幀間隔的平均換算成 FPS（遊戲評測慣用的 1% Low）。

        與 percentile_fps 的差別：P99 是單一個百分位點，1% Low 是最慢 1% 幀的平均，
        更能反映最差那一段的實際體感。以 bucket 值代表該 bucket 內的幀間隔。
        """
        if not 0 < fraction <= 1:
            raise ValueError("fraction 必須落在 (0, 1]")
        total = self.total
        if total == 0:
            return None
        remaining = max(1.0, total * fraction)
        weighted = 0.0
        counted = 0.0
        for ms_bucket in sorted(self.buckets, reverse=True):
            if remaining <= 0:
                break
            take = min(self.buckets[ms_bucket], remaining)
            weighted += ms_bucket * take
            counted += take
            remaining -= take
        if counted == 0 or weighted <= 0:
            return None
        return 1000.0 / (weighted / counted)

    def subtract(self, other: Histogram) -> Histogram:
        """本次累積值減去上次累積值，得到區間增量。

        累積計數器理論上只增不減；若出現負值代表對方做過 -clear，
        此時把該 bucket 視為 0（而非負數），並由呼叫端判斷是否要重置基準。
        """
        keys = set(self.buckets) | set(other.buckets)
        delta = {k: max(0, self.buckets.get(k, 0) - other.buckets.get(k, 0)) for k in keys}
        return Histogram({k: v for k, v in delta.items() if v > 0})


@dataclass
class LayerStats:
    layer_name: str
    package_name: str = ""
    total_frames: int = 0
    dropped_frames: int = 0
    average_fps: float | None = None
    histograms: dict[str, Histogram] = field(default_factory=dict)

    @property
    def present_to_present(self) -> Histogram:
        """唯一該用來算 FPS 的直方圖。找不到就回空的。"""
        return self.histograms.get("presenttopresent", Histogram())

    @property
    def post_to_present(self) -> Histogram:
        return self.histograms.get("posttopresent", Histogram())

    def effective_fps(self) -> float | None:
        """優先用 dumpsys 給的 averageFPS；沒有或為 0 時自行從直方圖算。"""
        if self.average_fps:
            return self.average_fps
        mean = self.present_to_present.mean_ms()
        if mean and mean > 0:
            return 1000.0 / mean
        return None

    def dropped_ratio(self) -> float:
        denom = self.total_frames + self.dropped_frames
        return self.dropped_frames / denom if denom else 0.0

    def matches_package(self, package: str) -> bool:
        return package.lower() in self.layer_name.lower() or package == self.package_name

    def is_blocklisted(self) -> bool:
        collapsed = re.sub(r"[^a-z]", "", self.layer_name.lower())
        return any(word in collapsed for word in _LAYER_BLOCKLIST)

    def subtract(self, other: LayerStats) -> LayerStats:
        """區間差分。用於即時模式。"""
        histograms = {
            key: hist.subtract(other.histograms.get(key, Histogram()))
            for key, hist in self.histograms.items()
        }
        delta = LayerStats(
            layer_name=self.layer_name,
            package_name=self.package_name,
            total_frames=max(0, self.total_frames - other.total_frames),
            dropped_frames=max(0, self.dropped_frames - other.dropped_frames),
            average_fps=None,  # 差分後 averageFPS 無意義，交給 effective_fps 重算
            histograms=histograms,
        )
        return delta


@dataclass
class TimestatsDump:
    layers: list[LayerStats] = field(default_factory=list)
    globals: dict[str, str] = field(default_factory=dict)
    raw: str = ""

    def find_layer(self, layer_name: str) -> LayerStats | None:
        for layer in self.layers:
            if layer.layer_name == layer_name:
                return layer
        return None

    def candidates_for(self, package: str) -> list[LayerStats]:
        """依 SPEC §2.3 挑出該 package 的候選 layer，由幀數多到少排序。"""
        matched = [
            layer
            for layer in self.layers
            if layer.matches_package(package)
            and layer.total_frames > 0
            and not layer.is_blocklisted()
        ]
        matched.sort(key=lambda layer: layer.total_frames, reverse=True)
        return matched

    def is_ambiguous(self, candidates: list[LayerStats]) -> bool:
        """前兩名幀數差距小於 10% 時視為無法自動判定，應請使用者選。"""
        if len(candidates) < 2:
            return False
        top, second = candidates[0].total_frames, candidates[1].total_frames
        if top == 0:
            return True
        return (top - second) / top < 0.10


def parse_timestats(text: str) -> TimestatsDump:
    """解析 `dumpsys SurfaceFlinger --timestats -dump` 的完整輸出。

    解析失敗不會拋例外（除非輸入完全不像 timestats），而是回傳盡可能多的資料，
    並保留 raw 供除錯。這是刻意的：新機型上寧可拿到部分資料也不要整個炸掉。
    """
    if not text or not text.strip():
        raise TimestatsParseError("timestats 輸出為空。請確認已執行過 `--timestats -enable`。")

    dump = TimestatsDump(raw=text)
    current_layer: LayerStats | None = None
    current_histogram_key: str | None = None

    for line in text.splitlines():
        stripped = line.strip()

        # --- 直方圖區段標題 ---
        header_match = _HISTOGRAM_HEADER_RE.match(line)
        if header_match:
            current_histogram_key = _normalize_key(header_match.group(1))
            target = current_layer.histograms if current_layer else None
            if target is not None:
                target.setdefault(current_histogram_key, Histogram())
            continue

        # --- 直方圖內容（可能跨多行）---
        if current_histogram_key and _BUCKET_RE.search(line):
            if current_layer is not None:
                hist = current_layer.histograms.setdefault(current_histogram_key, Histogram())
                for ms_str, count_str in _BUCKET_RE.findall(line):
                    ms, count = int(ms_str), int(count_str)
                    if count:
                        hist.buckets[ms] = hist.buckets.get(ms, 0) + count
            continue

        # 空行或新的 key=value 行代表直方圖結束
        if not stripped:
            current_histogram_key = None
            continue

        kv_match = _KV_RE.match(line)
        if not kv_match:
            current_histogram_key = None
            continue

        key, value = kv_match.group(1), kv_match.group(2).strip()
        current_histogram_key = None

        if key == "layerName":
            current_layer = LayerStats(layer_name=value)
            dump.layers.append(current_layer)
            continue

        if current_layer is None:
            dump.globals[key] = value
            continue

        if key == "packageName":
            current_layer.package_name = value
        elif key == "totalFrames":
            current_layer.total_frames = _to_int(value)
        elif key in ("droppedFrames", "missedFrames"):
            current_layer.dropped_frames = _to_int(value)
        elif key == "averageFPS":
            current_layer.average_fps = _to_float(value)

    if not dump.layers and "totalFrames" not in dump.globals:
        raise TimestatsParseError(
            "無法從輸出中解析出任何 layer 或全域統計。\n"
            "可能原因：timestats 未啟用、裝置不支援、或輸出格式已變更。\n"
            "請保留原始輸出並執行 `frameprobe doctor` 檢查。"
        )

    return dump


def _to_int(value: str) -> int:
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return 0


def _to_float(value: str) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None
