# 已知限制與設計取捨

## 為什麼不用 `dumpsys SurfaceFlinger --latency`

`--latency` 只回傳最近 127 幀，而且要先用 `--list` 取得完整 layer name。

layer name 的輸出格式自 Android 15 起已變動、各家 ROM 也不一致，遊戲類 App 經常抓不到。

timestats 的 dump 本身就帶 `layerName`，是累積計數器，差分後就是該區間的真實幀資料。

因此本工具**刻意不提供** `--latency` 作為 FPS 來源或備援。唯一的例外是 `frameprobe diagnose` 會把它的原始輸出印出來供人眼對照，且標註 `(unreliable)`。

## 已知限制

- **FrameTimeline 對 SurfaceView 的支援不完整。** 純 SurfaceView 遊戲在 `record` 模式下 App 層級查詢可能回 0 列，此時自動改用顯示層級（SurfaceFlinger 實際顯示的幀），summary 會標 `app_level_available: false`，jank 歸因不可用。這是預期行為，不是錯誤。
- **timestats 格式在未知 ROM 上可能不同。** 解析器會正規化鍵名（`present2present` / `presentToPresent`）並容忍跨行直方圖，但無法保證吸收所有變體。解析失敗時原始文字會留在 `sessions/<id>/raw/`。
- **sysfs 溫度單位是推斷值。** 只有在 `dumpsys thermalservice` 與 `hardware_properties` 都不可用時才會走 `/sys/class/thermal`，此時單位（度／十分之一度／毫度）靠帶寬推斷；讀數落在 10～120°C 之外、或某顆感測器本身就是 <10°C 的環境溫度時會判錯或被丟棄。來源會寫在 summary 的 `notes`。
- **部分 ROM 沒有 Thermal HAL 2.0**，拿不到 `Thermal Status`，因此 `time_to_throttle_s` 等節流指標會是 null；溫度本身仍可能有。`doctor` 的 thermal 那列會標 ⚠️。
- **ftrace 沒有節流狀態。** `record` 模式若溫度取自 ftrace（`thermal_timeline: exact`），只有溫度曲線；若 kernel 無 `thermal_temperature` 事件，改以每 2 秒 poll `dumpsys thermalservice`（`thermal_timeline: approximate`），時間軸為近似對齊。
- **即時模式的 Jank 是近似值。** timestats 只有直方圖，做不到「前 3 幀平均 2 倍」的相對判定，即時模式只以 83ms / 125ms 絕對門檻計數，summary 的 `jank_method` 會標 `histogram`；要精確值用 `record`。
- **record 模式的 CPU／記憶體／溫度是每 2 秒輪詢**，與逐幀資料為近似對齊。
- **不預設 60Hz。** 取不到 refresh rate 一律顯示 unknown。
- Android 17 尚未被任何社群工具公開驗證，請先跑 `frameprobe doctor`。
