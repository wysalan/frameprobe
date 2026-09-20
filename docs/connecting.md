# 連接裝置（有線／無線）

手機先開啟「開發人員選項 → USB 偵錯」。`frameprobe devices` 或儀表板的裝置列會標示目前是 USB 還是無線連線。

**有線（USB）**

```bash
adb devices -l          # 第一次接上手機會跳「允許 USB 偵錯」，勾選「一律允許」後按確定
```

出現 `device` 就可以用；顯示 `unauthorized` 代表還沒在手機上按允許。

**無線：Wi-Fi 偵錯配對（Android 11 以上，建議方式，不需先接線）**

1. 手機：設定 → 開發人員選項 → 開啟「無線偵錯」→ 點進去 → 「使用配對碼配對裝置」，畫面會顯示 6 位配對碼與 `IP:配對埠`。
2. 電腦與手機在同一個 Wi-Fi：

```bash
adb pair 192.168.1.23:37099      # 用「配對埠」，接著輸入 6 位配對碼；配對只需做一次
adb connect 192.168.1.23:42631   # 用「無線偵錯」主畫面上的 IP:埠（連線埠，與配對埠不同）
adb devices -l                   # 序號會是 IP:埠 或 adb-XXXX._adb-tls-connect._tcp
```

連線埠每次重新開啟無線偵錯都會變，要回手機畫面確認；之後每次只要 `adb connect`。

**無線：先接 USB 再切 TCP（任何版本）**

```bash
adb tcpip 5555                   # 接著 USB 線時執行
adb connect 192.168.1.23:5555    # 拔線後連；IP 在「關於手機 → 狀態」或 adb shell ip -4 addr show wlan0
```

重開機後會失效，需重做。

**斷線與排除**

```bash
adb disconnect                   # 斷開所有無線連線
adb kill-server && adb start-server
```

連不上時依序確認：兩端在同一網段、路由器沒開 AP 隔離、`adb pair` 用的是配對埠而非連線埠。

無線連線時每秒的 dumpsys 與截圖 pull 都走 Wi-Fi，延遲比 USB 大；`--interval` 建議維持 1 秒以上，發現取樣間隔不穩就改接線。
