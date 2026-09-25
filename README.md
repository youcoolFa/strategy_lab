# strategy_lab

> **核心是教學用沙盒(TEACHING SANDBOX)。** `broker/`、`plugins/`、
> `engine/`、`rules/`、`dsl/` 這些不會呼叫任何真實交易所 API、不持有
> 任何 API key,只會跟這個 repo 自己定義的記憶體內模擬交易所
> (paper broker)+ 合成價格產生器交易。
>
> **`strategy_lab/live/` 是刻意分開的例外**——會連真實 Redis(接收
> `Fa_Successful_trade` 廣播的市場/帳戶資料)、需要真實 Bybit API key
> 才能真的下單。細節見 [docs/ARCHITECTURE.md §6](docs/ARCHITECTURE.md)。

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

## 執行 YAML 策略(Phase 3)

```bash
/opt/anaconda3/bin/python3 -m demo.run_from_yaml --strategy strategies/weekend_mean_reversion.yaml
/opt/anaconda3/bin/python3 -m demo.run_from_yaml --strategy strategies/ma_crossover_bracket.yaml
```

`strategies/*.yaml` 只需要 `{type, params}` 就能組出完整的策略——每個
plugin 會自己從 `params` 組出觸發邏輯,不需要在 YAML 裡另外描述一棵
Condition 樹。

## Live 執行(真實 Bybit 帳戶,不是沙盒)

**照順序做,每一步都要親自確認,不要跳過:**

1. 複製範本、填入真實值(這兩個檔案都已經在 `.gitignore` 排除,不會
   進 git):
   ```bash
   cp .env.example .env                                          # 填入 BYBIT_API_KEY / BYBIT_API_SECRET / REDIS_URL
   cp live_execution_config.example.yaml live_execution_config.yaml
   ```
2. **先確認 `live_execution_config.yaml` 裡 `dry_run: true`、
   `testnet: true`**(範本預設值,先不要改),用這個安全設定跑一次,
   確認整個流程(讀 YAML 策略、連 Bybit 測試網查價、log 輸出)正常。
3. 確認沒問題之後,才把 `dry_run`/`testnet` 改成 `false`——這是你自己
   明確的動作,程式碼本身的預設值永遠是安全的。
4. 執行:
   ```bash
   /opt/anaconda3/bin/python3 -m strategy_lab.live.main
   ```
   `Ctrl+C`(SIGINT)或 `kill`(SIGTERM)會觸發 `StrategyRunner.request_stop()`,
   讓它正常收攤(取消未成交單、平掉未平倉部位)再結束,不是直接砍掉
   process。

## 進度

- [x] Phase 1 —— Plugin 層
- [x] Phase 2 —— Rule engine
- [x] Phase 3 —— DSL
- [ ] Phase 4 —— 總結演練(Capstone)
- [x] Live 遷移 Stage 1 —— port `account_feed`/`market_feed`
- [x] Live 遷移 Stage 2 —— `bybit_client.py`(pybit,取代 ccxt)
- [x] Live 遷移 Stage 3.1-3.3 —— `Broker` Protocol、`LiveBroker`、`dry_run`
- [x] Live 遷移 Stage 3.4 —— `live/config.py`、`live/main.py`、`request_stop()`(**程式碼完成,實際執行需要你自己填入真實 API key**)

Phase 4(Capstone)還沒做——三個 YAML 策略熱切換驗證、計時演練那個部分。
