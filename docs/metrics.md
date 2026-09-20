# 指標

| 類別 | 指標 | 來源 |
|---|---|---|
| 幀率 | FPS、P90、P99、掉幀率、frame time 直方圖 | timestats 差分（即時）／FrameTimeline（record） |
| 卡頓 | Jank、BigJank、Stutter（> 前 3 幀平均 2 倍且 > 83ms／125ms） | record 逐幀精確；即時只有直方圖，改用絕對門檻近似並標 `histogram` |
| 穩定度 | Std(FPS)、Avg/Std(FTime)、Drop(FPS)、>100ms 幀數 | summary 層級 |
| 溫度 | CPU / GPU / 電池 / 機身、Thermal Status、time_to_throttle | `dumpsys thermalservice` 等四層備援 |
| CPU | 整機 %、App %、App 正規化 %（依頻率加權）、各核心頻率 | `/proc/stat`、`/proc/<pid>/stat`、cpufreq sysfs |
| 記憶體 | PSS / RSS / Swap、整機可用 | `/proc/<pid>/smaps_rollup`；PSS 對 shell 不可讀時改用 statm 的 RSS 並在 notes 標明 |
| GPU | 使用率（僅 Adreno）、頻率、GPU 記憶體 | kgsl `gpubusy`／`clock_mhz`、devfreq `*gpu*/cur_freq`、`dumpsys gpu`；PowerVR 無可讀使用率節點 |
| Memory Detail | Java／Native／Code／Graphics／GL mtrack 等 | `dumpsys meminfo`，每 2 秒；smaps 不可讀時也拿它的 TOTAL PSS |
| 功耗 | 電壓、電流、功耗、FPower（mW／幀） | sysfs `current_now`（µA）優先，改用 `dumpsys battery`（Samsung 的 `current now` 為 mA）；接電源時標示 |
| 程序 | Wakeups、CSwitch | `/proc/<pid>/task/*/status` 差分 |
| 網路 | 整機收發 KB/s | `/proc/net/dev`；Android 12+ 無 root 拿不到 per-app |
| 啟動 | 冷／溫啟動 TotalTime、WaitTime | `am start -W`（`frameprobe launch`） |
| 截圖 | 每 N 秒 `screencap` | `--screenshot-interval`，預設關，會影響效能 |
| 場景 | 場景標籤與分段統計、門檻超標區間、多 session 疊圖 | `POST /api/sessions/<id>/markers`；門檻存於瀏覽器 |

## 指標定義的參考來源

多數指標是 Android／Linux 的原生定義（timestats、FrameTimeline、thermalservice、procfs），以下這些採用的是 PerfDog（騰訊 WeTest）公開文件的定義或演算法，方便與該工具的數字對照：

| 指標 | 採用的定義 |
|---|---|
| Jank / BigJank | 幀耗時 > 前 3 幀平均的 2 倍，且 > 83.3ms（BigJank：> 125ms） |
| Stutter | Jank 幀耗時總和 ÷ 總時長 |
| Jank / 10min | Jank 次數換算成每 10 分鐘 |
| Drop(FPS) | 相鄰兩個 FPS 點下降超過 8 的次數 |
| Delta(FTime) | 幀間隔 > 100ms 的幀數 |
| Std(FPS) / Std(FTime) | 標準差（通用統計，PerfDog 7.1 起也列出） |
| App CPU 正規化 | CPU 使用率 × (目前頻率 ÷ 最高頻率)，對應 CPU Usage(Normalized) |
| FPower | 功耗 ÷ FPS，每幀能耗 |
| Wakeups / CSwitch | 執行緒喚醒與 context switch 計數；本工具以 procfs 的 ctxt_switches 近似 |

Smooth（稳帧指数）與 SmallJank 為 PerfDog 未公開的演算法，本工具沒有對應指標。

1% Low 採用的是 PC／主機評測慣用定義（最慢 1% 幀的平均），與 PerfDog 無關。

本專案為獨立實作，未使用 PerfDog 的任何程式碼，與騰訊／WeTest／PerfDog 無任何關聯，亦未獲其背書。

PerfDog 為其所有者之商標，此處僅用於說明指標定義的出處。
