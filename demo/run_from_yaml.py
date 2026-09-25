"""
Phase 3 demo:用 `--strategy <path>` 指定一個 YAML 策略檔,不用改任何
Python 就能換策略——這是 DSL 層真正要交付的東西。不給 `--strategy` 的話,
會改成互動式選單,列出 strategies/*.yaml 讓你輸入數字挑一個。

跟 demo_weekend_phase1.py / demo_crossover_phase1.py 的差別:那兩個是把
plugin 物件用 Python 手動 `import` + 建構;這裡是透過
dsl.loader.load_strategy() 讀 YAML,plugin 是用字串名稱透過
registry.get() 動態查出來的。

下單數量(qty)不再直接用策略 YAML 的 order_qty——改成讀
demo/sandbox_order.yaml 的 position_sizing,用 dsl.order_config 換算出
真正的 qty。PaperBroker 本身不追蹤帳戶餘額,這個換算只發生在這個檔案
裡,算好的數字才傳進 StrategyRunner,runner.py/PaperBroker 完全不用改。

執行方式:
  /opt/anaconda3/bin/python3 -m demo.run_from_yaml --strategy strategies/weekend_mean_reversion.yaml
  /opt/anaconda3/bin/python3 -m demo.run_from_yaml --strategy strategies/ma_crossover_bracket.yaml
  /opt/anaconda3/bin/python3 -m demo.run_from_yaml                      # 互動式選單
"""

from __future__ import annotations

import argparse
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from strategy_lab.broker.synthetic_feed import SyntheticFeed
from strategy_lab.dsl.discovery import list_strategy_files, prompt_strategy_choice
from strategy_lab.dsl.loader import load_strategy
from strategy_lab.dsl.order_config import compute_qty, load_order_config
from strategy_lab.engine.runner import StrategyRunner

HKT = ZoneInfo("Asia/Hong_Kong")
STRATEGIES_DIR = Path(__file__).resolve().parents[1] / "strategies"
SANDBOX_ORDER_CONFIG_PATH = Path(__file__).resolve().parent / "sandbox_order.yaml"

# 兩個示範策略各自需要不同的起始時間(週末策略要從星期六開始、crossover
# 策略要從平日開始)才跑得出有意義的結果,用策略名稱對應,demo 用途足夠。
START_TIMES = {
    "weekend_mean_reversion": datetime(2026, 8, 1, 4, 0, tzinfo=HKT),  # 星期六
    "ma_crossover_bracket": datetime(2026, 8, 3, 9, 0, tzinfo=HKT),  # 星期一
}
TICK_INTERVALS = {
    "weekend_mean_reversion": timedelta(minutes=5),
    "ma_crossover_bracket": timedelta(minutes=1),
}


def main() -> None:
    parser = argparse.ArgumentParser(description="從 YAML 檔載入並執行一個 strategy_lab 策略")
    parser.add_argument("--strategy", default=None, help="YAML 策略檔路徑;不給的話會跳出互動式選單")
    parser.add_argument("--seed", type=int, default=42, help="SyntheticFeed 亂數種子")
    args = parser.parse_args()

    if args.strategy:
        strategy_path = args.strategy
    else:
        strategy_files = list_strategy_files(STRATEGIES_DIR)
        strategy_path = prompt_strategy_choice(strategy_files)

    strategy = load_strategy(strategy_path)
    print(f"載入策略:{strategy.name}({strategy.symbol})")
    print(f"  entry       = {strategy.entry!r}")
    print(f"  exit        = {strategy.exit!r}")
    print(f"  time_window = {strategy.time_window!r}")
    print(f"  kill_switch = {strategy.kill_switch!r}")

    start_price = 60000.0
    order_config = load_order_config(SANDBOX_ORDER_CONFIG_PATH)
    order_qty = compute_qty(order_config, current_price=start_price)
    print(f"  order_qty   = {order_qty:.6f}(依 {SANDBOX_ORDER_CONFIG_PATH.name} 的 position_sizing 換算,起始價 {start_price:.2f})")
    print()

    runner = StrategyRunner(
        entry=strategy.entry,
        exit=strategy.exit,
        time_window=strategy.time_window,
        order_qty=order_qty,
        kill_switch=strategy.kill_switch,
    )

    start = START_TIMES.get(strategy.name, datetime(2026, 8, 1, 4, 0, tzinfo=HKT))
    tick_interval = TICK_INTERVALS.get(strategy.name, timedelta(minutes=5))
    feed = SyntheticFeed(start_price=start_price, volatility_pct=0.3, seed=args.seed)
    runner.run(now=start, feed=feed, tick_interval=tick_interval)

    print(f"trades filled: {len(runner.trades)}")
    for i, trade in enumerate(runner.trades, 1):
        pnl = (trade.exit_price - trade.entry_price) * trade.qty
        print(f"  #{i}: entry={trade.entry_price:.2f} exit={trade.exit_price:.2f} qty={trade.qty:.4f} pnl={pnl:.2f}")
    print(f"final state: {runner.state.name}")
    print(f"position left over after cleanup: {runner.broker.position_qty():.4f}")


if __name__ == "__main__":
    main()
