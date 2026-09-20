from __future__ import annotations

from frameprobe.adb import parse_devices_output

SAMPLE = """List of devices attached
R5CT00000XX            device usb:1-1.4 product:dm1qzcx model:SM_S9110 device:dm1q transport_id:3
192.168.1.23:5555      device product:grizzly model:Pixel_11_Pro device:grizzly transport_id:5
adb-3A091FDJH000XY-vWx7Yz._adb-tls-connect._tcp. device model:Pixel_11_Pro transport_id:6
0123456789ABCDEF       unauthorized transport_id:7
emulator-5554          device product:sdk_gphone64_arm64 model:sdk_gphone64_arm64 transport_id:1
* daemon started successfully
"""


def test_parse_devices_transport() -> None:
    devices = parse_devices_output(SAMPLE)
    by_serial = {d["serial"]: d for d in devices}
    assert by_serial["R5CT00000XX"]["transport"] == "usb"
    assert by_serial["R5CT00000XX"]["model"] == "SM_S9110"
    assert by_serial["192.168.1.23:5555"]["transport"] == "wireless"
    assert by_serial["adb-3A091FDJH000XY-vWx7Yz._adb-tls-connect._tcp."]["transport"] == "wireless"
    assert by_serial["0123456789ABCDEF"]["state"] == "unauthorized"
    assert by_serial["0123456789ABCDEF"]["transport"] == "unknown"
    assert by_serial["emulator-5554"]["transport"] == "unknown"
    assert parse_devices_output("List of devices attached\n\n") == []
