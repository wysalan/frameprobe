# fixtures

| 檔案 | 來源 | 備註 |
|---|---|---|
| `timestats_android17.txt` | 專案提供（Android 17 真機輸出） | |
| `timestats_presenttopresent.txt` | 由 `timestats_android17.txt` 以 sed 把 `present2present`/`post2present` 改成 Google 文件的 `presentToPresent`/`postToPresent` 拼法 | 僅驗證鍵名正規化 |
| `thermalservice_pixel.txt` | 專案提供（Pixel 真機輸出） | |
| `sysfs_zones.txt` | 專案提供（ThermalCollector.SYSFS_COMMAND 的輸出格式） | |
| `hardware_properties.txt` | 依 AOSP `HardwarePropertiesManagerService.dump()` 的輸出格式撰寫 | **非真機輸出**，待補 |
| `sysstats_a.txt` / `sysstats_b.txt` | 依 kernel proc(5)／cpufreq sysfs 文件格式手寫的兩次連續快照（b 的 smaps_rollup 模擬 Permission denied）；`#gpubusy` 段落依真機 kgsl 輸出，`#gpuclk` 的頻率值為假設 | **非真機輸出**，待補 |
| `meminfo_samsung_s23.txt` | 真機（Samsung S23 / Snapdragon 8 Gen 2，`dumpsys meminfo <package>`），DATABASES 段落截短；套件名與資料庫檔名已替換為 `com.example.game`／`example-db`，數值未動 | |
| `gpu_kgsl_gpubusy_idle.txt` | 真機（同上，`cat /sys/class/kgsl/kgsl-3d0/gpubusy`，GPU 閒置時） | |
| `power_pixel11pro.txt` | 真機（Pixel 11 Pro / Tensor + PowerVR，`POWER_COMMAND` 的合併輸出：sysfs current_now/voltage_now + dumpsys battery） | |
| `dumpsys_gpu_pixel11pro.txt` | 真機（Pixel 11 Pro，`dumpsys gpu` 節錄） | |
| `devinfo_samsung_s23.txt` / `devinfo_pixel11pro.txt` | 真機（`DEVINFO_COMMAND` 的合併輸出，dumpsys display 只保留 grep 到的三行） | |
| `dumpsys_battery_samsung_s23.txt` | 真機（Samsung S23，`dumpsys battery`，log buffer 截短；Samsung 特有 `current now:` 為 mA） | |

尚缺：Android 12 / 14 / 15 的 timestats 真機輸出（SPEC §10）。依 CLAUDE.md 規則 3，未取得真實樣本前不憑空撰寫。

`am start -W` 的輸出格式（`frameprobe/launch.py` 與 `tests/test_launch.py` 內的 `AM_OUT`）尚未用真機輸出驗證，屬 ActivityManager shell 的固定格式。
