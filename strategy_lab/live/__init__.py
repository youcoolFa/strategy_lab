"""
真實環境專用的元件:連接真實 Redis(接收 Fa_Successful_trade 廣播的
市場/帳戶資料)、真實 Bybit API(下單執行)。

跟 strategy_lab 的其他部分(interfaces/registry/rules/plugins/engine/
broker)刻意分開成獨立套件——那些是「教學沙盒」,不連任何真實服務、不
持有任何憑證;這個套件會連真實 Redis、需要真實 Bybit API key,兩者的
風險等級完全不同,不應該混在同一個命名空間裡讓人分不清楚。
"""
