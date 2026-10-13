"""strategy_lab 介面(Streamlit,2026-10-09)。

    cd /Users/mac/strategy_lab && .venv/bin/streamlit run strategy_lab/ui/app.py --server.address 127.0.0.1

- 只綁本機 127.0.0.1(頁面上有會寫資料庫的按鈕,不能開到區域網路)。
- 總覽 / 交易紀錄 / Log 全部只讀:不下單、不取消、不動 daemon。
- 策略分頁(2026-10-09 改版):選策略(free style 是其中一個)→ 填參數 → 開始;上面的進行中清單
  每一列可以改參數 / 停止(打 STOP)/ 脫離(打 DETACH)/ 結束 free style。一般策略開始前要先
  「寫入參數並預覽」(preflight),再打 yes;頁面只做真正的交易環境(沒有 dry-run / 測試網)。背後全部走 strategy_lab/ui/actions.py,
  跟終端機同一套檢查;設定檔(live_*.yaml)自動找或自動建立,使用者不用管。
- 關掉這個頁面不影響任何正在跑的實盤程式(daemon 是獨立的背景程式)。
"""

from __future__ import annotations

import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:  # streamlit run 用檔案路徑執行,要自己加專案根目錄
    sys.path.insert(0, str(ROOT))
os.chdir(ROOT)  # 設定檔裡的 strategies/*.yaml 是相對專案根目錄的路徑

import pandas as pd  # noqa: E402
import streamlit as st  # noqa: E402

from strategy_lab.live import free_style as fs  # noqa: E402
from strategy_lab.ui import actions, data  # noqa: E402

HKT = ZoneInfo("Asia/Hong_Kong")


def hkt(dt):
    return dt.astimezone(HKT).strftime("%m-%d %H:%M") if dt else "—"


def ago(dt, now):
    if not dt:
        return ""
    minutes = int((now - dt).total_seconds() // 60)
    return f"{minutes} 分鐘前" if minutes < 120 else f"{minutes // 60} 小時前"


data.load_env()
st.set_page_config(page_title="strategy_lab", page_icon="📈", layout="wide")
DB_URL = data.db_url()
now = datetime.now(timezone.utc)
head = data.git_head()

top_l, top_r = st.columns([4, 1])
top_l.title("📈 strategy_lab")
top_l.caption(f"現在 {hkt(now)} HKT|程式 HEAD {head or '?'}|總覽 / 交易紀錄 / Log 只讀;策略分頁的動作都要你自己確認")
if top_r.button("🔄 重新整理", key="refresh"):
    st.cache_data.clear()
    st.rerun()

def _backfill_command(u) -> str:
    """終端機補記指令:開始 = 第一筆的那一分鐘,結束 = 最後一筆的下一分鐘(HKT)。"""
    start = u.first.astimezone(HKT).replace(second=0, microsecond=0)
    end = (u.last.astimezone(HKT) + timedelta(minutes=1)).replace(second=0, microsecond=0)
    return (f".venv/bin/python -m strategy_lab.live.free_style backfill --account {u.account} "
            f"--symbol {u.symbol} --start \"{start:%Y-%m-%d %H:%M}\" --end \"{end:%Y-%m-%d %H:%M}\"")


def _unrecorded_section():
    """每日對帳(2026-10-13)找到的交易會自動記成 free style(2026-10-14);這裡只列記不了的,附終端機補記指令
    (補記分頁 10-14 拿掉)。"""
    from strategy_lab.live import reconcile as rc

    st.subheader("未記錄的交易(每日對帳,08:00 HKT)")
    last = rc.last_run()
    left, right = st.columns([4, 1])
    left.caption(f"上次對帳:{hkt(last) if last else '還沒跑過'}。Bybit 上有成交、DB 卻沒有紀錄的交易會自動記成 "
                 "free style(到「損益表」補填);策略紀錄漏記的會自動補回。這裡只列**自動記不了**的(例:時間跟已有紀錄重疊)。")
    if right.button("🔄 立即對帳", key="reconcile_now"):
        with st.spinner("對帳中(只讀 Bybit)…"):
            rc.run_daily(DB_URL, datetime.now(timezone.utc))
        st.success("對帳完成,有問題會發 Telegram 系統警報")
    items = sorted(rc.load_unrecorded(), key=lambda u: u.first, reverse=True)  # 最近的排最前面(使用者要求)
    if not items:
        st.caption("沒有未記錄的交易")
        return
    st.markdown(f"**最新更新:{hkt(last) if last else '還沒對帳'} HKT**(由新到舊)")
    st.caption("訂單 = 你在 Bybit 送出的買 / 賣;一張訂單可以拆成好幾筆成交(跟幾個對手撮合就幾筆)。")
    st.dataframe(pd.DataFrame([{"帳戶": u.account, "幣種": u.symbol, "第一筆(HKT)": hkt(u.first),
                                "最後一筆(HKT)": hkt(u.last), "訂單數": u.orders, "成交筆數": u.count} for u in items]),
                 hide_index=True, width="stretch")
    st.caption("記不了的原因在 Telegram 警告裡;處理好之後,用終端機補記(或下次對帳會再試一次):")
    st.code("\n".join(_backfill_command(u) for u in items), language="bash")

# 分頁順序(2026-10-14 使用者指定):損益表、策略、交易紀錄、Log;總覽放最後
tab_pnl, tab_strategy, tab_records, tab_log, tab_overview = st.tabs(["損益表", "策略", "交易紀錄", "Log", "總覽"])

# ---------------------------------------------------------------------------
with tab_overview:
    st.subheader("背景程式(daemon)")
    try:
        daemons = data.list_daemons()
        unfinished = data.unfinished_runs(DB_URL) if DB_URL else {}
    except Exception as e:  # noqa: BLE001
        daemons, unfinished = [], {}
        st.error(f"讀取狀態失敗:{e}")
    if daemons:
        rows = []
        for d in daemons:
            run = unfinished.get((data.account_key(d.account), d.symbol)) if d.alive else None
            plan = data.schedule(data.config_path(d.config), run["started_at"]) if run else None
            rows.append({
                "設定檔": d.config, "帳戶": d.account, "幣種": d.symbol or "—",
                "狀態": "🟢 運作中" if d.alive else "⚪ 沒在跑", "PID": d.pid if d.alive else None,
                "策略": run["strategy_name"] if run else "—",
                "啟動時間": data.fmt_time(plan["start"]) if plan else "—",
                "預估結束": data.fmt_time(plan["end"]) if plan else "—",
                "預估長度": data.fmt_duration(plan["duration"]) if plan else "—",
                "程式版本": data.code_version_label(run.get("git_commit") if run else None, head) if d.alive else "—",
                "最後寫 log": f"{hkt(d.last_log_at)}({ago(d.last_log_at, now)})" if d.last_log_at else "—",
                "提醒": data.stale_warning(d.alive, d.last_log_at, now) or "",
            })
        st.dataframe(pd.DataFrame(rows), hide_index=True, width="stretch")
        st.caption("預估結束 = 策略時間窗的強制收尾時間(取消掛單、市價平倉);預估長度 = 預估結束 − 啟動時間。"
                   "「舊碼」= 正在跑的程式不是最新 commit,新功能要下次啟動才生效。"
                   "每小時一定會寫 ⚫ 狀態,超過 70 分鐘沒寫 log 通常是 Mac 睡著了。")
    else:
        st.info("沒有 live_*.yaml 執行設定")

    st.subheader("Bybit 帳戶(只讀,30 秒快取)")
    snap_account = st.selectbox("帳戶", data.list_accounts(), key="snap_account")

    @st.cache_data(ttl=30, show_spinner="查詢 Bybit…")
    def snapshot(account):
        return data.account_snapshot(data.make_client(account))

    try:
        snap = snapshot(snap_account)
        st.metric("帳戶權益(USDT)", f"{snap['equity']:.2f}")
        c1, c2 = st.columns(2)
        c1.markdown("**持倉**")
        if snap["positions"]:
            c1.dataframe(pd.DataFrame(snap["positions"]), hide_index=True, width="stretch")
        else:
            c1.caption("沒有持倉")
        c2.markdown("**掛單**")
        if snap["orders"]:
            orders = pd.DataFrame(snap["orders"])
            orders["下單時間"] = orders["下單時間"].map(hkt)
            c2.dataframe(orders, hide_index=True, width="stretch")
        else:
            c2.caption("沒有掛單")
    except Exception as e:  # noqa: BLE001
        st.error(f"查詢 Bybit 失敗:{e}")

    _unrecorded_section()

# ---------------------------------------------------------------------------
with tab_records:
    if not DB_URL:
        st.warning(".env 沒有 TRADING_DB_URL")
    else:
        try:
            runs = data.recent_runs(DB_URL)
            events = data.recent_events(DB_URL)
        except Exception as e:  # noqa: BLE001
            runs, events = [], []
            st.error(f"讀取 trading 資料庫失敗:{e}")
        st.subheader("最近的執行(sl_run)")
        if runs:
            df = pd.DataFrame([{
                "開始": hkt(r["started_at"]), "結束": hkt(r["ended_at"]) if r["ended_at"] else "進行中",
                "策略": r["strategy_name"], "幣種": r["symbol"], "結束原因": r["end_reason"] or "",
                "輪數": r["events_count"], "淨利": r["net_pnl"], "commit": (r["git_commit"] or "")[:7],
                "run_id": r["run_id"][:8],
            } for r in runs])
            st.dataframe(df, hide_index=True, width="stretch")
        else:
            st.caption("沒有紀錄")
        st.subheader("最近完成的輪(sl_event)")
        if events:
            df = pd.DataFrame([{
                "結束": hkt(e["end_time"]), "方向": e["direction"], "成交次數": e["fills"],
                "建倉均價": e["avg_entry"], "平倉均價": e["avg_exit"], "毛利": e["realized_pnl"],
                "手續費": e["fees"], "資金費": e["funding"], "淨利": e["net_pnl"],
                "強制平倉": "是" if e["forced"] else "", "run_id": e["run_id"][:8],
            } for e in events])
            st.dataframe(df, hide_index=True, width="stretch")
        else:
            st.caption("沒有紀錄")

# ---------------------------------------------------------------------------
with tab_log:
    try:
        daemons = data.list_daemons()
    except Exception:  # noqa: BLE001
        daemons = []
    if daemons:
        names = [d.config for d in daemons]
        choice = st.selectbox("設定檔", names, key="log_config")
        lines = st.slider("行數", 10, 300, 40, step=10, key="log_lines")
        row = daemons[names.index(choice)]
        st.code(data.log_tail(row.console_log, lines) or "(沒有 log)", language=None)
    else:
        st.info("沒有 live_*.yaml 執行設定")

# ---------------------------------------------------------------------------
# 策略:進行中清單 + 選策略 → 填參數 → 開始
# ---------------------------------------------------------------------------
import yaml  # noqa: E402

FREE = "free style"


def _parse_prices(text):
    return [float(x) for x in text.replace(",", ",").split(",") if x.strip()]


def _parse_loop(text):
    text = text.strip().lower()
    return None if text in ("null", "none", "") else int(text)


def _read_yaml(path):
    try:
        return yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except Exception:  # noqa: BLE001
        return {}


def _strategy_info(root, strategy_path):
    try:
        return actions.strategy_info(actions._strategy_file(root, strategy_path))
    except Exception:  # noqa: BLE001
        return {"scale_in": False, "lots": 0, "distance": None, "loop": None, "direction": "?"}


def _running_strategy_row(row, root):
    """在跑的策略:改參數(control)、停止、脫離。所有 key 都加上設定檔名,一頁可以有好幾列。"""
    c = row["config"]
    path = data.config_path(c)
    cfg = _read_yaml(path)
    info = _strategy_info(root, cfg.get("strategy_path", ""))
    word = "y" if cfg.get("dry_run", True) else "yes"
    st.caption(f"設定檔 {c}.yaml")
    band = bool(info.get("band"))
    if band:
        st.caption("區間策略:運作中不能改參數,也不提供脫離(脫離後不能接手,單會留在交易所沒人管);只能停止並收尾。")
    else:
        _running_params(c, path, cfg, info, word)
    _stop_buttons(c, path, allow_detach=not band)


def _running_params(c, path, cfg, info, word):
    st.markdown("**改參數**(預覽 → 輸入確認字 → 立刻套用到正在跑的程式)")
    changes = {}
    overrides = cfg.get("strategy_overrides") or {}
    try:
        if info.get("scale_in"):
            prices_text = st.text_input(f"建倉價({info['lots']} 注,逗號分隔;不用的注填 0)",
                                        value=",".join(f"{p:g}" for p in (cfg.get("entry_prices") or [])), key=f"ctl_prices_{c}")
            dist_default = overrides.get("exit_distance") or info.get("distance") or {}
            dist = st.number_input(f"平倉距離({dist_default.get('unit', 'pct')})", value=float(dist_default.get("value") or 0),
                                   format="%g", key=f"ctl_distance_{c}")
            prices = _parse_prices(prices_text)
            if prices != [float(p) for p in (cfg.get("entry_prices") or [])]:
                changes["entry_prices"] = prices
            if dist and float(dist) != float(dist_default.get("value") or 0):
                changes["distance"] = float(dist)
        loop_default = overrides.get("loop", info.get("loop"))
        loop_text = st.text_input("loop(重複次數;null = 不限)", value="null" if loop_default is None else str(loop_default),
                                  key=f"ctl_loop_{c}")
        new_loop = _parse_loop(loop_text)
        if new_loop != loop_default:
            changes["loop"] = new_loop
    except ValueError:
        st.error("建倉價要是逗號分隔的數字,loop 要是整數或 null")
        changes = {}
    if st.button("🔍 預覽變更", key=f"ctl_preview_{c}"):
        try:
            st.session_state[f"ctl_pending_{c}"] = (changes, actions.control_preview(path, changes))
        except ValueError as e:
            st.session_state.pop(f"ctl_pending_{c}", None)
            st.warning(str(e))
    pending = st.session_state.get(f"ctl_pending_{c}")
    if pending:
        st.code(pending[1], language=None)
        if pending[0] != changes:
            st.warning("參數在預覽之後改過了,請重新預覽")
        else:
            typed = st.text_input(f"確認套用:輸入 {word}", key=f"ctl_typed_{c}")
            if st.button("✅ 套用變更", key=f"ctl_apply_{c}"):
                with st.spinner("套用中(等背景程式回應,最多 90 秒)…"):
                    ok, text = actions.control_apply(path, changes, typed)
                (st.success if ok else st.error)("完成" if ok else "沒有套用")
                st.code(text, language=None)
                st.session_state.pop(f"ctl_pending_{c}", None)



def _stop_buttons(c, path, allow_detach=True):
    left, right = st.columns(2)
    with left:
        st.markdown("**停止(收尾)**:取消所有掛單、**市價平倉**後結束")
        typed_stop = st.text_input("輸入 STOP", key=f"stop_typed_{c}")
        if st.button("⏹️ 停止並收尾", key=f"stop_btn_{c}"):
            try:
                with st.spinner("等收尾完成(最多 90 秒)…"):
                    st.success(actions.stop(path, typed_stop))
            except ValueError as e:
                st.error(str(e))
            except Exception as e:  # noqa: BLE001  DaemonError 等
                st.error(f"停止失敗:{e}")
    if not allow_detach:
        return
    with right:
        st.markdown("**脫離**:直接結束、**不收尾**,掛單與持倉留在交易所(下次用「接手現有持倉」開始)")
        typed_detach = st.text_input("輸入 DETACH", key=f"detach_typed_{c}")
        if st.button("⏏️ 脫離", key=f"detach_btn_{c}"):
            try:
                st.success(actions.detach(path, typed_detach))
            except ValueError as e:
                st.error(str(e))
            except Exception as e:  # noqa: BLE001
                st.error(f"脫離失敗:{e}")


def _free_style_row(row):
    sym, account = row["symbol"], row.get("account")
    ok = st.checkbox("我確認要結束(從 Bybit 拉回訂單寫進資料庫、發 Telegram)", key=f"fs_stop_confirm_{account}_{sym}")
    if st.button("⏹️ 結束 free style", key=f"fs_stop_{account}_{sym}"):
        if not ok:
            st.warning("請先勾「我確認」")
            return
        try:
            with st.spinner("從 Bybit 拉回訂單與成交…"):
                result = fs.stop(DB_URL, data.make_client(account), sym, datetime.now(timezone.utc), account=account)
            text = fs.summary_message(result)
            fs.send_telegram(text)
            st.success("完成")
            st.code(text, language=None)
        except fs.FreeStyleError as e:
            st.error(f"❌ {e}")


def _symbol_input(placeholder: str, key: str) -> str:
    """只填幣名,USDT 自動補上(全部都是 USDT 永續);回傳 Bybit 交易對,例 ETH → ETHUSDT。"""
    symbol = actions.perp_symbol(st.text_input("幣種(只填幣名,USDT 自動補上)", placeholder=placeholder, key=key))
    if symbol:
        st.caption(f"交易對:{symbol}")
    return symbol


def _start_free_style(accounts):
    st.caption("自己在 Bybit 手動交易,這裡只負責記帳:開始只記時間,結束時從 Bybit 拉回這段期間的單寫進資料庫。不會下單或取消。"
               "這裡是**還沒結束**的交易;已經結束、忘了記的交易,每天 08:00 對帳時會自動記成 free style。")
    symbol = _symbol_input("ETH", key="fs_symbol")
    # 開始時間用選的(不用打字):預設是打開這頁的這一刻;往前選 = 交易其實之前就開始了
    now = datetime.now(HKT).replace(second=0, microsecond=0)
    day_col, time_col = st.columns(2)
    since_day = day_col.date_input("開始日期(HKT,預設現在,可往前選)", value=now.date(), max_value=now.date(), format="YYYY-MM-DD",
                                   key="fs_since_day")
    since_time = time_col.time_input("開始時間(HKT)", value=now.time(), step=60, key="fs_since_time")
    ok = st.checkbox("我確認要開始記錄(會寫進 trading 資料庫)", key="fs_start_confirm")
    if st.button("▶️ 開始 free style", key="fs_start"):
        if not symbol:
            st.warning("請填幣種")
        elif not ok:
            st.warning("請先勾「我確認」")
        else:
            when = datetime.combine(since_day, since_time, tzinfo=HKT).astimezone(timezone.utc)
            if when > datetime.now(timezone.utc):
                st.error("開始時間不能在未來")
                return
            for account in accounts:  # 每個帳戶各記一段;某個失敗不影響其他
                try:
                    run_id = fs.start(DB_URL, data.make_client(account), symbol, when, account=account)
                    st.success(f"✅ {symbol} free style 開始(帳戶 {account},{hkt(when)} HKT,run_id {run_id[:8]})")
                except Exception as e:  # noqa: BLE001  FreeStyleError / AccountError
                    st.error(f"❌ 帳戶 {account}:{e}")


def _start_strategy(choice, root, accounts):
    strategy_path = f"strategies/{choice}.yaml"
    info = _strategy_info(root, strategy_path)
    st.caption(f"方向 {info.get('direction')}" + (f"|分注 {info['lots']} 注" if info.get("scale_in") else ""))
    symbol = _symbol_input("WLD", key="new_symbol")
    if not symbol:
        st.caption("填幣種後繼續")
        return
    paths = {}
    for account in accounts:
        path = actions.config_for(root, strategy_path, symbol, account)
        if path.exists() and actions.daemon_running(path):
            st.info(f"{choice} / {symbol} / 帳戶 {account} 已經在跑(設定檔 {path.name});要改參數或停止,請用上面的進行中清單")
        else:
            paths[account] = path
    if not paths:
        return
    accounts = list(paths)
    many = len(accounts) > 1
    path = paths[accounts[0]]
    cfg = _read_yaml(path) if path.exists() else {}
    st.caption("設定檔:" + "、".join(f"{p.name}({'沿用' if p.exists() else '新建'})" for p in paths.values()))

    st.warning("🔴 這裡開始的策略一律是**真正的交易環境**(Bybit 正式站、真實資金);dry-run / 測試網請用終端機")
    band = bool(info.get("band"))
    if band:
        st.caption("區間策略:上下各掛一張,成交一張就在對面價位補一張同數量的單,持倉在 ±數量 之間切換,"
                   "直到收尾。不支援接手現有持倉:交易所上這個幣要沒有持倉、沒有掛單。")
        adopt = False
    else:
        adopt = st.checkbox("接手現有持倉(交易所上這個幣已有的持倉 / 掛單,不平倉直接接著管理;只支援分注策略)",
                            value=bool(cfg.get("adopt_existing_position", False)), key="new_adopt")
    sizing = cfg.get("position_sizing") or {}
    modes = ["fixed_qty", "fixed_quote_amount", "account_percentage"]
    if many:  # 帳戶大小差很多,多個帳戶一律用權益百分比(使用者決定)
        mode = "account_percentage"
        st.caption(f"選了 {len(accounts)} 個帳戶:數量一律用**權益百分比**,每個帳戶各自依自己的權益計算")
        default = float(sizing.get("value") or 0) if sizing.get("mode") == mode else 0.0
        value = st.number_input("權益百分比 %(分注 = 第一注)", value=default, format="%g", key="new_value")
    else:
        mode = st.selectbox("數量模式", modes, index=modes.index(sizing.get("mode", "fixed_qty")), key="new_mode")
        value = st.number_input("數量(分注 = 第一注)", value=float(sizing.get("value") or 0), format="%g", key="new_value")

    params = {}
    try:
        overrides = cfg.get("strategy_overrides") or {}
        if band:
            band_default = overrides.get("band") or {}
            b1, b2 = st.columns(2)
            buy_pct = b1.number_input("買單:起點價往下多少 %", value=float(band_default.get("buy_pct", info.get("buy_pct") or 0.1)),
                                      format="%g", key="new_buy_pct")
            sell_pct = b2.number_input("賣單:起點價往上多少 %", value=float(band_default.get("sell_pct", info.get("sell_pct") or 0.1)),
                                       format="%g", key="new_sell_pct")
            params["band"] = {"buy_pct": float(buy_pct), "sell_pct": float(sell_pct)}
        if info.get("scale_in"):
            prices_text = st.text_input(f"建倉價({info['lots']} 注,逗號分隔;不用的注填 0)",
                                        value=",".join(f"{p:g}" for p in (cfg.get("entry_prices") or [])), key="new_prices")
            dist_default = overrides.get("exit_distance") or info.get("distance") or {}
            dist = st.number_input(f"平倉距離({dist_default.get('unit', 'pct')})", value=float(dist_default.get("value") or 0),
                                   format="%g", key="new_distance")
            params["entry_prices"] = _parse_prices(prices_text) or None
            params["distance"] = float(dist) if dist else None
        if not band:  # 區間策略固定做到收尾
            loop_default = overrides.get("loop", info.get("loop"))
            loop_text = st.text_input("loop(重複次數;null = 不限)", value="null" if loop_default is None else str(loop_default),
                                      key="new_loop")
            params["loop"] = _parse_loop(loop_text)
    except ValueError:
        st.error("建倉價要是逗號分隔的數字,loop 要是整數或 null")
        return
    settings = {"position_sizing.mode": mode, "position_sizing.value": float(value)}
    if not band:
        settings["adopt_existing_position"] = adopt
    signature = (tuple(paths[a].name for a in accounts), settings, params)

    if st.button("💾 寫入參數並預覽", key="new_prepare"):
        try:
            with st.spinner("寫入設定檔、查詢市場資料…"):
                plans = actions.prepare_many(root, strategy_path, symbol, settings, params, accounts)
            st.session_state["new_preview"] = {"sig": signature, "plans": plans}
        except ValueError as e:
            st.session_state.pop("new_preview", None)
            st.error(str(e))
    pending = st.session_state.get("new_preview")
    if not pending:
        return
    if pending["sig"] != signature:
        st.warning("參數在預覽之後改過了,請重新按「寫入參數並預覽」")
        return
    plans = pending["plans"]
    for plan in plans:
        st.markdown(f"**帳戶 {plan.account}**" + ("" if plan.ready else " ✗ 不會開始"))
        if plan.error:
            st.error(plan.error)
        else:
            st.code(plan.preview.text, language=None)
            if not plan.preview.ready:
                st.error(f"帳戶 {plan.account} 的預覽檢查沒有通過(看上面的 ✗ 原因)")
    ready = [p.account for p in plans if p.ready]
    if not ready:
        st.error("沒有任何帳戶的預覽通過,不能開始")
        return
    word, label = "yes", "以真實資金開始"
    who = "、".join(ready)
    typed = st.text_input(f"確認{label}(帳戶 {who}):輸入 {word}", key="new_typed")
    if st.button(f"🚀 {label}", key="new_start"):
        try:
            with st.spinner("啟動中(會再查一次市場、確認背景程式沒有立刻結束)…"):
                results = actions.start_many(plans, typed)
        except ValueError as e:
            st.error(str(e))
            return
        for r in results:
            (st.success if r.ok else st.error)(f"帳戶 {r.account}:" + ("已在背景開始" if r.ok else "沒有開始"))
            st.code(r.text, language=None)
        if any(r.ok for r in results):  # 打錯字之類全部沒開始:保留預覽,可以直接再試
            st.session_state.pop("new_preview", None)


with tab_strategy:
    root = data.project_root()
    st.subheader("進行中")
    try:
        active = data.active_runs(DB_URL, root)
    except Exception as e:  # noqa: BLE001
        active = []
        st.error(f"讀取進行中清單失敗:{e}")
    if not active:
        st.caption("沒有進行中的策略")
    for row in active:
        title = f"{row['mode']}|帳戶 {row.get('account') or '—'}|{row['symbol']}|{row['strategy']}" + (
            f"|{hkt(row['started_at'])} 開始" if row.get("started_at") else "")
        with st.expander(title):
            if row["kind"] == FREE:
                _free_style_row(row)
            else:
                _running_strategy_row(row, root)

    st.subheader("開始新的")
    account_names = data.list_accounts()
    accounts = st.multiselect("帳戶(可以選多個,每個帳戶各跑一支)", account_names, default=account_names[:1],
                              key="new_accounts")
    names = [p.stem for p in actions.list_strategies(ROOT)] + [FREE]
    choice = st.radio("① 選策略", names, key="new_strategy", horizontal=True)
    st.markdown("**② 填參數 → ③ 開始**")
    if not accounts:
        st.caption("請至少選一個帳戶")
    elif choice == FREE:
        _start_free_style(accounts)
    else:
        _start_strategy(choice, root, accounts)



# ---------------------------------------------------------------------------
_PNL_SHOWN = ["日期", "幣種", "方向", "數量", "建倉價格", "建倉時間", "平倉價格", "真實損益", "手續費", "手續費佔比(%)",
              "預估時長", "實際時長", "目標價", "停損價", "備註"]


@st.cache_data(ttl=30, show_spinner=False)
def _fee_points(account):
    return data.fee_points(data.make_client(account))


def _fee_points_metric(col, account):
    """手續費點數(使用者指定):ETHUSDT 現價 × 這個帳戶的 maker 費率 × 2(買 + 賣),30 秒快取。"""
    label = "手續費點數(ETHUSDT,maker 買+賣)"
    try:
        f = _fee_points(account)
    except Exception as e:  # noqa: BLE001  查不到 Bybit 不影響損益表
        col.metric(label, "—", help=f"查詢 Bybit 失敗:{e}")
        return
    col.metric(label, f"{f['points']:.2f} 點",
               help=f"現價 {f['price']:g} × maker {f['maker_rate'] * 100:.4f}% × 2(買一次 + 賣一次)= "
                    f"用 maker 開倉、maker 平倉,價格要往有利方向走這麼多點才賺回手續費(帳戶 {account})")


def _pnl_section():
    """每個策略的損益表(2026-10-14):每一對交易(持倉 0 → 0)一列。表格是 AG Grid 免費版:像 Excel 一樣
    點欄位標題排序 / 篩選;預估時長 / 目標價 / 停損價 / 備註(淡黃底)可以直接在格子裡改,按儲存寫回 DB。
    上方合計和最底總結列跟著表格目前的篩選。"""
    import st_aggrid

    from strategy_lab.ui import grid

    if not DB_URL:
        st.warning(".env 沒有 TRADING_DB_URL")
        return
    accounts = data.list_accounts()
    main = next((i for i, a in enumerate(accounts) if a.upper() == "MAIN"), 0)
    c1, c2 = st.columns(2)
    account = c1.selectbox("帳戶", accounts, index=main, key="pnl_account")
    try:
        strategy = c2.selectbox("策略", data.pnl_strategies(DB_URL, account), key="pnl_strategy")
        rows = data.pnl_rows(DB_URL, account, strategy)
    except Exception as e:  # noqa: BLE001  資料庫連不上不能讓整頁掛掉
        st.error(f"讀取 trading 資料庫失敗:{e}")
        return
    if not rows:
        st.caption(f"帳戶 {account} 的 {strategy} 還沒有任何交易紀錄(全手動交易會在每天 08:00 對帳時自動記成 free style)")
        return
    m1, m2, m3, m4 = st.columns(4)  # 先佔位置,等表格回傳篩選後的列再填
    _fee_points_metric(m4, account)
    st.caption("點欄位標題右邊的 ☰ → 排序 / 篩選(像 Excel);淡黃底的欄位可以直接點進去改。")
    table = pd.DataFrame(rows).rename(columns={"手續費佔比": "手續費佔比(%)"})
    table = table[_PNL_SHOWN + [c for c in grid.HIDDEN if c in table.columns]]
    response = st_aggrid.AgGrid(
        table, gridOptions=grid.pnl_grid_options(_PNL_SHOWN), key=f"pnl_grid_{account}_{strategy}",
        height=min(80 + 36 * len(table), 520), theme="streamlit", allow_unsafe_jscode=True,
        data_return_mode="FILTERED_AND_SORTED", update_on=["filterChanged", "sortChanged", "cellValueChanged"])
    shown = response.data if response is not None and response.data is not None else table
    shown_rows = [{k: (None if isinstance(v, float) and v != v else v) for k, v in r.items()}
                  for r in pd.DataFrame(shown).to_dict("records")]
    m1.metric("交易對數", len(shown_rows))
    m2.metric("真實損益合計(USDT)", f"{sum(r.get('真實損益') or 0 for r in shown_rows):.0f}")
    m3.metric("手續費合計(USDT)", f"{sum(r.get('手續費') or 0 for r in shown_rows):.0f}")
    if not shown_rows:
        st.caption("篩選後沒有交易,放寬欄位標題上的篩選看看")
        return
    summary = data.pnl_summary([{**r, "手續費佔比": r.get("手續費佔比(%)")} for r in shown_rows])
    shown_summary = {  # 總結列(使用者指定的幾欄);全部用文字,空的留白
        "日期": summary["日期"],
        "數量": f"{summary['數量']:.2f}" if summary["數量"] is not None else "",
        "真實損益": f"{summary['真實損益']:.0f}", "手續費": f"{summary['手續費']:.0f}",
        "手續費佔比(%)": f"{summary['手續費佔比']:.2f}" if summary["手續費佔比"] is not None else "",
        "實際時長": summary["實際時長"], "預估時長": summary["預估時長"],
    }
    st.dataframe(pd.DataFrame([{c: shown_summary.get(c, "") for c in _PNL_SHOWN}]), hide_index=True, width="stretch",
                 column_config={"數量": "數量(平均)", "真實損益": "真實損益(總和)", "手續費": "手續費(總和)",
                                "手續費佔比(%)": "手續費佔比(%,平均)", "實際時長": "實際時長(平均)",
                                "預估時長": "預估時長(平均)"})
    st.caption("真實損益 = 淨利(已扣手續費、資金費);手續費佔比 = 手續費 ÷ 價差毛利;日期 = 平倉日(HKT);"
               "數量 = 這一對交易的最大持倉。儲存只會存目前表格看得到(沒被篩掉)的列。")
    if st.button("💾 儲存後補欄位", key="pnl_save"):
        changes = actions.pnl_changes(rows, shown_rows)
        if not changes:
            st.info("沒有修改")
            return
        try:
            st.success(f"已儲存 {actions.save_pnl_edits(DB_URL, changes)} 列")
        except ValueError as e:
            st.error(f"沒有儲存:{e}")


with tab_pnl:
    st.subheader("損益表")
    _pnl_section()

