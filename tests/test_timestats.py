from __future__ import annotations

import pytest

from frameprobe.timestats import Histogram, LayerStats, TimestatsParseError, parse_timestats

from .conftest import load

GAME = "SurfaceView[com.example.game/com.unity3d.player.UnityPlayerActivity]@0(BLAST)#132833"


@pytest.mark.parametrize("name", ["timestats_android17.txt", "timestats_presenttopresent.txt"])
def test_parse_both_spellings(name: str) -> None:
    dump = parse_timestats(load(name))
    assert [layer.layer_name for layer in dump.layers][0] == GAME
    assert len(dump.layers) == 3
    game = dump.find_layer(GAME)
    assert game is not None
    assert game.total_frames == 1000
    assert game.dropped_frames == 12
    assert game.average_fps == pytest.approx(57.421)
    # 跨行、非等距 bucket 全部收齊，且 0 計數的 bucket 不保留
    assert game.present_to_present.buckets == {16: 850, 33: 100, 50: 35, 66: 10, 102: 5}
    assert game.post_to_present.buckets == {1: 990, 2: 10}
    assert dump.globals["totalFrames"] == "3600"


def test_percentiles_match_official_definition() -> None:
    game = parse_timestats(load("timestats_android17.txt")).find_layer(GAME)
    assert game is not None
    hist = game.present_to_present
    assert hist.percentile_fps(0.90) == pytest.approx(1000 / 33)
    assert hist.percentile_fps(0.99) == pytest.approx(1000 / 66)
    assert hist.mean_ms() == pytest.approx(19.82)


def test_effective_fps_falls_back_to_histogram() -> None:
    layer = LayerStats("x", average_fps=0, histograms={"presenttopresent": Histogram({20: 10})})
    assert layer.effective_fps() == pytest.approx(50.0)
    assert LayerStats("x").effective_fps() is None


def test_candidates_and_auto_selection() -> None:
    dump = parse_timestats(load("timestats_android17.txt"))
    candidates = dump.candidates_for("com.example.game")
    # Splash 被排除、NavigationBar 不屬於該 package
    assert [c.layer_name for c in candidates] == [GAME]
    assert not dump.is_ambiguous(candidates)


def test_ambiguous_when_frame_counts_close() -> None:
    a = LayerStats("a", total_frames=100)
    b = LayerStats("b", total_frames=95)
    c = LayerStats("c", total_frames=50)
    dump = parse_timestats(load("timestats_android17.txt"))
    assert dump.is_ambiguous([a, b])
    assert not dump.is_ambiguous([a, c])
    assert not dump.is_ambiguous([a])


def test_subtract_gives_interval_delta() -> None:
    prev = LayerStats(
        "l",
        total_frames=100,
        dropped_frames=1,
        histograms={"presenttopresent": Histogram({16: 100})},
    )
    curr = LayerStats(
        "l",
        total_frames=160,
        dropped_frames=3,
        average_fps=59.0,
        histograms={"presenttopresent": Histogram({16: 150, 33: 10})},
    )
    delta = curr.subtract(prev)
    assert delta.total_frames == 60
    assert delta.dropped_frames == 2
    assert delta.average_fps is None
    assert delta.present_to_present.buckets == {16: 50, 33: 10}
    # 對方 -clear 過導致負值 → 視為 0
    assert prev.subtract(curr).present_to_present.buckets == {}


# --- 邊界 -------------------------------------------------------------------


def test_percentile_edge_cases() -> None:
    assert Histogram().percentile_fps(0.9) is None  # total_frames == 0
    assert Histogram({0: 500}).percentile_fps(0.9) is None  # 全部落在 0ms
    assert Histogram({8: 1}).percentile_fps(0.99) == pytest.approx(125.0)  # 只有一個 bucket
    assert Histogram({0: 10, 16: 90}).percentile_fps(0.05) == pytest.approx(1000 / 16)
    with pytest.raises(ValueError):
        Histogram({8: 1}).percentile_fps(0)


def test_empty_or_garbage_input() -> None:
    with pytest.raises(TimestatsParseError):
        parse_timestats("")
    with pytest.raises(TimestatsParseError):
        parse_timestats("   \n\n")
    with pytest.raises(TimestatsParseError):
        parse_timestats("this is not timestats at all")


def test_dropped_ratio_and_blocklist() -> None:
    assert LayerStats("x", total_frames=90, dropped_frames=10).dropped_ratio() == pytest.approx(0.1)
    assert LayerStats("x").dropped_ratio() == 0.0
    assert LayerStats("com.example.game/SplashActivity#0").is_blocklisted()
    assert LayerStats("Status Bar#0").is_blocklisted()
    assert not LayerStats(GAME).is_blocklisted()


def test_low_percent_fps() -> None:
    # 1000 幀：最慢 1% = 10 幀 → 5 幀 102ms + 5 幀 66ms → 平均 84ms → 11.9 FPS
    hist = Histogram({16: 850, 33: 100, 50: 35, 66: 10, 102: 5})
    assert hist.low_percent_fps(0.01) == pytest.approx(1000 / 84)
    # 10% Low = 100 幀：5×102 + 10×66 + 35×50 + 50×33 = 4570 → 平均 45.7ms
    assert hist.low_percent_fps(0.10) == pytest.approx(1000 / 45.7)
    assert Histogram().low_percent_fps(0.01) is None
    assert Histogram({0: 100}).low_percent_fps(0.01) is None
    # 幀數少於 100 時至少取 1 幀
    assert Histogram({16: 9, 100: 1}).low_percent_fps(0.01) == pytest.approx(10.0)
    with pytest.raises(ValueError):
        hist.low_percent_fps(0)
