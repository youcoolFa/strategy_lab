"""啟動前估算。分兩層:

1. plugin 描述自己的事實——進場 plugin 的 `entry_price()`/`resting`,出場
   plugin 的 `planned_exit()`(止盈/停損價位)。
2. 計算元件(metric)像 plugin 一樣註冊(`@register("metric", ...)`),只讀
   這些事實,不寫死任何策略。

策略 = plugin 的組合,所以每個策略自動得到自己正確的 PnL/最大虧損公式:
有停損的(bracket)最大虧損有上限,沒停損的(weekend_*)顯示為沒有上限並
列出情境。新增一種計算 = estimates/metrics/ 新增一個檔案 + 一行 import。
"""
