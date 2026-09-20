from __future__ import annotations

import time
from contextlib import contextmanager
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from frameprobe.server import app as app_mod
from frameprobe.storage import SessionStore

from .conftest import load
from .test_analysis import FakeQuery
from .test_perfetto_runner import RecordingAdb
from .test_session import GrowingAdb


def make_client(tmp_path: Path, adb) -> TestClient:
    adb.props.setdefault("ro.build.version.sdk", "37")
    adb.responses.setdefault(r"^id$", ("uid=2000(shell)", 0))
    app = app_mod.create_app(SessionStore(tmp_path), adb_factory=lambda serial: adb)
    return TestClient(app)


def wait_stopped(client: TestClient, sid: str, timeout: float = 5.0) -> dict:
    deadline = time.time() + timeout
    while time.time() < deadline:
        body = client.get(f"/api/sessions/{sid}").json()
        if body["status"] in ("stopped", "error"):
            return body
        time.sleep(0.05)
    raise AssertionError("session 沒有停止")


def test_devices_doctor_packages_layers(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    adb = GrowingAdb()
    adb.responses[r"dumpsys power"] = ("mWakefulness=Awake", 0)
    adb.responses[r"perfetto --version"] = ("v1", 0)
    adb.responses[r"pm list packages"] = ("package:com.example.game\npackage:com.b\n", 0)
    adb.responses[r"dumpsys activity"] = (
        "    mResumedActivity: ActivityRecord{1 u0 com.example.game/.Main t2}",
        0,
    )
    monkeypatch.setattr(app_mod.Adb, "list_devices", staticmethod(lambda: [{"serial": "X"}]))
    import frameprobe.probe as probe_mod

    monkeypatch.setattr(probe_mod.time, "sleep", lambda _s: None)
    import frameprobe.sampler as sampler_mod

    monkeypatch.setattr(sampler_mod.time, "sleep", lambda _s: None)

    with make_client(tmp_path, adb) as client:
        assert client.get("/api/devices").json() == [{"serial": "X"}]
        adb.responses[r"#props"] = (load("devinfo_pixel11pro.txt"), 0)
        info = client.get("/api/devices/X/info").json()
        assert info["specs"]["soc"] == "Google Tensor G6" and "raw" not in info["specs"]
        assert info["specs"]["cpu_summary"].startswith("7 核")
        doctor = client.get("/api/devices/X/doctor").json()
        assert doctor["device"]["specs"]["refresh_rate_hz"] == 120
        assert doctor["device"]["sdk_int"] == 37
        assert {c["name"] for c in doctor["checks"]} >= {"timestats", "thermal"}

        pk = client.get("/api/devices/X/packages").json()
        assert pk["packages"] == ["com.b", "com.example.game"]
        assert pk["foreground"] == "com.example.game"

        layers = client.get("/api/devices/X/layers", params={"package": "com.example.game"}).json()
        assert layers["selected"] == "SurfaceView[com.example.game/x]#1"
        assert client.get("/", follow_redirects=False).status_code == 200


def test_realtime_session_lifecycle(tmp_path: Path) -> None:
    adb = GrowingAdb()
    with make_client(tmp_path, adb) as client:
        assert client.get("/api/sessions/nope").status_code == 404
        created = client.post(
            "/api/sessions", json={"serial": "X", "package": "com.example.game", "interval": 0.5}
        )
        assert created.status_code == 201, created.text
        sid = created.json()["session_id"]
        assert client.post("/api/sessions", json={"serial": "X", "package": "x"}).status_code == 409
        assert (
            client.post(
                "/api/sessions", json={"serial": "X", "package": "x", "interval": 0.1}
            ).status_code
            == 422
        )

        with client.websocket_connect(f"/ws/sessions/{sid}") as ws:
            first = ws.receive_json()
            messages = [first]
            while len([m for m in messages if m["type"] == "sample"]) < 2:
                messages.append(ws.receive_json())
            assert any(m["type"] == "state" and m["data"]["status"] == "running" for m in messages)
            sample = next(m for m in messages if m["type"] == "sample")["data"]
            assert sample["frames"] == 60 and sample["cpu_c"] == 61.9

            running = client.get("/api/sessions").json()["running"]
            assert running and running[0]["session_id"] == sid
            assert client.get(f"/api/sessions/{sid}").json()["summary"] is None
            assert len(client.get(f"/api/sessions/{sid}/samples").json()) >= 2

            marker = client.post(f"/api/sessions/{sid}/markers", json={"label": "boss"})
            assert marker.status_code == 201 and marker.json()["label"] == "boss"
            msg = ws.receive_json()
            while msg["type"] != "marker":
                msg = ws.receive_json()
            assert msg["data"]["label"] == "boss"

            stopped = client.delete(f"/api/sessions/{sid}")
            assert stopped.status_code == 200
            final = ws.receive_json()
            while final["type"] != "state" or final["data"]["status"] == "running":
                final = ws.receive_json()
            assert final["data"]["status"] == "stopped"

        body = wait_stopped(client, sid)
        assert body["summary"]["mode"] == "realtime" and body["summary"]["total_frames"] >= 120
        assert body["summary"]["markers"][0][1] == "boss"
        # 事後補標籤：寫回 summary.json，且要排序
        assert (
            client.post(f"/api/sessions/{sid}/markers", json={"label": "late"}).status_code == 422
        )
        client.post(f"/api/sessions/{sid}/markers", json={"label": "start", "elapsed": 0.1})
        markers = client.get(f"/api/sessions/{sid}").json()["summary"]["markers"]
        assert [m[1] for m in markers] == ["start", "boss"]
        assert client.get(f"/api/sessions/{sid}/export", params={"format": "csv"}).text.startswith(
            "ts,"
        )
        assert (
            client.get(f"/api/sessions/{sid}/export", params={"format": "xml"}).status_code == 422
        )
        listed = client.get("/api/sessions").json()
        assert listed["running"] == [] and listed["sessions"][0]["session_id"] == sid

        # 斷線重連：歷史 session 也能透過 WS 補送
        with client.websocket_connect(f"/ws/sessions/{sid}?replay=1") as ws:
            assert ws.receive_json()["type"] == "sample"
            assert ws.receive_json()["data"]["status"] == "stopped"
        assert client.delete(f"/api/sessions/{sid}").status_code == 409
        assert client.delete("/api/sessions/nope").status_code == 404

        # 移除：整個資料夾搬到 removed/，不刪檔；列表不再出現；再移一次 404
        moved = client.post(f"/api/sessions/{sid}/remove")
        assert moved.status_code == 200
        dest = Path(moved.json()["moved_to"])
        assert dest.parent.name == "removed" and (dest / "summary.json").exists()
        assert client.get("/api/sessions").json()["sessions"] == []
        assert client.post(f"/api/sessions/{sid}/remove").status_code == 404


def test_realtime_session_error_state(tmp_path: Path) -> None:
    adb = GrowingAdb()
    with make_client(tmp_path, adb) as client:
        sid = client.post("/api/sessions", json={"package": "com.nothere", "interval": 0.5}).json()[
            "session_id"
        ]
        body = wait_stopped(client, sid)
        assert body["status"] == "error" and "找不到" in body["message"]


def test_record_session(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    adb = RecordingAdb()

    @contextmanager
    def fake_open_trace(path):
        assert Path(path).exists()
        yield FakeQuery(app_frames=True, thermal_tracks=False)

    import frameprobe.session as session_mod

    monkeypatch.setattr(session_mod, "open_trace", fake_open_trace)
    adb.responses[r"timestats -clear -enable"] = ("", 1)  # timestats 不可用 → 改用 poll，仍可錄
    with make_client(tmp_path, adb) as client:
        created = client.post(
            "/api/sessions",
            json={"serial": "X", "package": "com.example.game", "mode": "record", "duration": 0.3},
        )
        assert created.status_code == 201, created.text
        sid = created.json()["session_id"]
        body = wait_stopped(client, sid)
        assert body["status"] == "stopped", body
        assert (
            body["summary"]["mode"] == "record" and body["summary"]["jank_breakdown"]["None"] == 170
        )
        assert len(client.get(f"/api/sessions/{sid}/samples").json()) == 3
        with client.websocket_connect(f"/ws/sessions/{sid}") as ws:
            msgs = [ws.receive_json() for _ in range(4)]
        assert [m["type"] for m in msgs] == ["sample", "sample", "sample", "state"]


def test_ws_unknown_session(tmp_path: Path) -> None:
    with make_client(tmp_path, GrowingAdb()) as client:
        with client.websocket_connect("/ws/sessions/nope") as ws:
            assert ws.receive_json()["data"]["status"] == "error"
        assert load("dumpsys_battery_samsung_s23.txt")  # fixture loader 可用（避免未使用警告）


def test_spa_fallback(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    dist = tmp_path / "dist"
    (dist / "_nuxt").mkdir(parents=True)
    (dist / "index.html").write_text("<html>spa</html>")
    (dist / "_nuxt" / "a.js").write_text("js")
    (dist / "favicon.ico").write_bytes(b"ico")
    monkeypatch.setattr(app_mod, "WEB_DIST", dist)
    with make_client(tmp_path / "sessions", GrowingAdb()) as client:
        assert client.get("/").text == "<html>spa</html>"
        assert client.get("/sessions/whatever").text == "<html>spa</html>"
        assert client.get("/_nuxt/a.js").text == "js"
        assert client.get("/favicon.ico").content == b"ico"
        assert client.get("/api/sessions/nope").status_code == 404  # API 路由不被 fallback 吃掉
        assert client.get("/../etc/passwd").text == "<html>spa</html>"


def test_shots_and_launch_routes(tmp_path: Path) -> None:
    from .test_launch import AM_OUT

    adb = GrowingAdb()
    adb.responses[r"resolve-activity .* 'com.example.game'"] = ("com.example.game/.Main\n", 0)
    adb.responses[r"^am force-stop"] = ("", 0)
    adb.responses[r"^am start -W"] = (AM_OUT, 0)
    import frameprobe.launch as launch_mod

    launch_mod.time.sleep = lambda _s: None  # type: ignore[assignment]
    store = SessionStore(tmp_path)
    store.create("sid")
    (tmp_path / "sid" / "shots").mkdir()
    (tmp_path / "sid" / "shots" / "000001.png").write_bytes(b"PNG")
    with make_client(tmp_path, adb) as client:
        assert client.get("/api/sessions/sid/shots/000001.png").content == b"PNG"
        assert client.get("/api/sessions/sid/shots/nope.png").status_code == 404
        traversal = client.get("/api/sessions/sid/shots/..%2Fsummary.json")
        assert "application/json" not in traversal.headers.get("content-type", "")
        assert b"session_id" not in traversal.content
        res = client.post(
            "/api/devices/X/launch", json={"package": "com.example.game", "repeat": 2}
        )
        assert res.status_code == 200, res.text
        assert res.json()["total_ms"] == 812 and len(res.json()["runs"]) == 2
        assert (
            client.post("/api/devices/X/launch", json={"package": "com.nothere"}).status_code == 502
        )


def test_long_recording_transfer_gate(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """錄超過門檻：停止後先問傳輸連線；無線失敗 → 再選 USB 成功。"""
    from frameprobe.adb import AdbTimeoutError, CommandResult

    class WirelessAdb(RecordingAdb):
        def pull(self, remote: str, local: str, *, timeout: float = 120.0) -> CommandResult:
            raise AdbTimeoutError("adb 指令逾時（900.0s）")

    wireless = WirelessAdb()
    usb = RecordingAdb()
    for a in (wireless, usb):
        a.props["ro.build.version.sdk"] = "37"
        a.responses[r"^id$"] = ("uid=2000(shell)", 0)
        a.responses[r"timestats -clear -enable"] = ("", 0)
        a.responses[r"timestats -disable"] = ("", 0)
        a.responses[r"timestats -dump"] = (load("timestats_android17.txt"), 0)
    factory = {"adb-XYZ._adb-tls-connect._tcp": wireless, "USB123": usb}

    @contextmanager
    def fake_open_trace(path):
        yield FakeQuery(app_frames=True, thermal_tracks=False)

    import frameprobe.session as session_mod

    monkeypatch.setattr(session_mod, "open_trace", fake_open_trace)
    monkeypatch.setattr(app_mod, "LONG_RECORDING_S", 0.0)
    monkeypatch.setattr(
        app_mod.Adb,
        "list_devices",
        staticmethod(
            lambda: [
                {
                    "serial": "adb-XYZ._adb-tls-connect._tcp",
                    "state": "device",
                    "model": "Pixel",
                    "device": "g",
                    "transport": "wireless",
                },
                {
                    "serial": "USB123",
                    "state": "device",
                    "model": "Pixel",
                    "device": "g",
                    "transport": "usb",
                },
                {
                    "serial": "OTHER",
                    "state": "device",
                    "model": "S23",
                    "device": "dm1q",
                    "transport": "usb",
                },
            ]
        ),
    )
    app = app_mod.create_app(
        SessionStore(tmp_path), adb_factory=lambda serial: factory[serial or "USB123"]
    )
    with TestClient(app) as client:
        created = client.post(
            "/api/sessions",
            json={
                "serial": "adb-XYZ._adb-tls-connect._tcp",
                "package": "com.example.game",
                "mode": "record",
                "interval": 0.5,
            },
        )
        assert created.status_code == 201, created.text
        sid = created.json()["session_id"]
        with client.websocket_connect(f"/ws/sessions/{sid}") as ws:
            time.sleep(1.2)
            assert client.delete(f"/api/sessions/{sid}").status_code == 200
            msg = ws.receive_json()
            while msg["type"] != "transfer":
                assert not (
                    msg["type"] == "state" and msg["data"]["status"] in ("stopped", "error")
                ), msg
                msg = ws.receive_json()
            info = msg["data"]
            assert info["error"] is None
            assert {d["serial"] for d in info["options"]} == {
                "adb-XYZ._adb-tls-connect._tcp",
                "USB123",
            }
            # 執行中清單要看得到等待傳輸的 session
            running = client.get("/api/sessions").json()["running"]
            assert (
                running and running[0]["status"] == "awaiting_transfer" and running[0]["transfer"]
            )
            # 停止已停止的 session → 409
            assert client.delete(f"/api/sessions/{sid}").status_code == 409

            # 1) 選無線 → pull 逾時 → 再送一次 transfer（帶錯誤）
            assert (
                client.post(
                    f"/api/sessions/{sid}/transfer",
                    json={"serial": "adb-XYZ._adb-tls-connect._tcp"},
                ).status_code
                == 200
            )
            msg = ws.receive_json()
            while msg["type"] != "transfer":
                assert not (
                    msg["type"] == "state" and msg["data"]["status"] in ("stopped", "error")
                ), msg
                msg = ws.receive_json()
            assert "逾時" in msg["data"]["error"]

            # 2) 選 USB → 成功 → 分析完成
            assert (
                client.post(f"/api/sessions/{sid}/transfer", json={"serial": "USB123"}).status_code
                == 200
            )
            msg = ws.receive_json()
            while not (msg["type"] == "state" and msg["data"]["status"] in ("stopped", "error")):
                msg = ws.receive_json()
            assert msg["data"]["status"] == "stopped", msg
        body = wait_stopped(client, sid)
        assert body["summary"]["mode"] == "record" and body["summary"]["total_frames"] == 180
        assert any(c.startswith("rm -f /data/misc/perfetto-traces") for c in usb.calls)
        assert (
            client.post(f"/api/sessions/{sid}/transfer", json={"serial": "USB123"}).status_code
            == 409
        )
