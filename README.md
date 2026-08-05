# strategy_lab

> **教學用沙盒(TEACHING SANDBOX)。** 不會呼叫任何真實交易所 API,不
> 持有任何 API key,只會跟這個 repo 自己定義的記憶體內模擬交易所
> (paper broker)+ 合成價格產生器交易。跟 `sat_strategy` 或任何真實
> 帳號都沒有連接。

一個把交易策略拆成三層來組合的示範專案,採漸進式建構,每一層都先能
跑、能測試,才加下一層:

1. **Plugin 層** —— 可獨立測試的 entry/exit/time-window 模組,背後接
   一個小型註冊表。
2. **Rule engine(規則引擎)** —— 可組合的條件(`And`/`Or`/`Not`),
   決定 plugin *什麼時候* 觸發。
3. **DSL** —— 策略用 YAML 資料描述,而不是寫 Python 程式碼,新策略
   只要改設定檔就能組出來,不用寫程式。

每個階段都用兩個示範策略來驅動,確保模組邊界是真的被用到、而不是只
是換幾個常數:

- **週末均值回歸** —— 從 `/Users/mac/sat_strategy` 移植過來。價格跌破
  固定參考價 X% 時進場,回到參考價時出場,每週 HKT 週六 04:00 →
  週一 06:00 的時間窗。
- **MA 均線交叉 + 止盈止損括號單** —— 快/慢 SMA 交叉時進場,相對進場
  價的停利或停損任一觸發即出場,每日盤中場次的時間窗。

## 文件

架構圖、`engine/runner.py` 狀態機、資料流追蹤、已知限制,見
[docs/ARCHITECTURE.md](docs/ARCHITECTURE.md)。

## 安裝

```bash
/opt/anaconda3/bin/python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/pytest -v
```

## 進度

- [x] Phase 1 —— Plugin 層
- [ ] Phase 2 —— Rule engine
- [ ] Phase 3 —— DSL
- [ ] Phase 4 —— 總結演練(Capstone)

把真正的 `sat_strategy/app/bot.py` 移植到這套架構上,是另外一個獨立的
後續工作,要等這裡的四個階段都對兩個示範策略驗證過之後才會開始。
