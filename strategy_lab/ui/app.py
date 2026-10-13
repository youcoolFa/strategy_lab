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

def _unrecorded_section():
    """每日對帳(2026-10-13)留下、還沒補記的成交;一鍵帶入「補記」分頁的帳戶、幣種、時間。"""
    from strategy_lab.live import reconcile as rc

    st.subheader("未記錄的交易(每日對帳,08:00 HKT)")
    last = rc.last_run()
    left, right = st.columns([4, 1])
    left.caption(f"上次對帳:{hkt(last) if last else '還沒跑過'}。Bybit 上有成交、DB 卻沒有任何紀錄的時段"
                 "(全手動交易沒記);策略紀錄漏記的會自動補回。")
    if right.button("🔄 立即對帳", key="reconcile_now"):
        with st.spinner("對帳中(只讀 Bybit)…"):
            rc.run_daily(DB_URL, datetime.now(timezone.utc))
        st.success("對帳完成,有問題會發 Telegram 系統警報")
    items = rc.load_unrecorded()
    if not items:
        st.caption("沒有未記錄的交易")
        return
    st.dataframe(pd.DataFrame([{"帳戶": u.account, "幣種": u.symbol, "第一筆(HKT)": hkt(u.first),
                                "最後一筆(HKT)": hkt(u.last), "成交筆數": u.count} for u in items]),
                 hide_index=True, width="stretch")
    for i, u in enumerate(items):
        if st.button(f"📝 補記 {u.account} {u.symbol} {hkt(u.first)} → {hkt(u.last)}", key=f"unrec_fill_{i}"):
            start = u.first.astimezone(HKT).replace(second=0, microsecond=0)
            end = (u.last.astimezone(HKT) + timedelta(minutes=1)).replace(second=0, microsecond=0)
            st.session_state.update(
                bf_account=u.account, bf_symbol=u.symbol.removesuffix("USDT"), bf_start_date=start.date(),
                bf_start_time=start.time(), bf_end_date=end.date(), bf_end_time=end.time())
            st.session_state.pop("bf_pending", None)
            st.success("已帶入,請切到「補記」分頁:先看有沒有紅字重疊,再預覽、寫入。"
                       "開始時已有持倉會被擋下,這時把開始時間往前移到建倉之前。")


tab_overview, tab_records, tab_log, tab_strategy, tab_backfill = st.tabs(["總覽", "交易紀錄", "Log", "策略", "補記"])

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
               "這裡是**還沒結束**的交易;已經結束的交易請到「補記」分頁。")
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


_HOLD_UNITS = {"分鐘": "minutes", "小時": "hours", "天": "days"}


def _backfill_overlap(account, symbol, start, end, start_cols, end_cols) -> bool:
    """列出同帳戶同幣種已有的紀錄時段(只查 DB,馬上有結果);開始 / 結束落在某段裡就在那個欄位下面標紅字。
    回傳有沒有重疊(有就不能預覽)。"""
    if not symbol or not DB_URL:
        return False
    try:
        segments = fs.existing_segments(DB_URL, symbol, account, datetime.now(timezone.utc))
    except fs.FreeStyleError as e:
        st.error(f"❌ {e}")
        return True
    if segments:
        st.caption(f"帳戶 {account} {symbol} 已有的紀錄(不能重疊):\n" + "\n".join(f"• {g.label()}" for g in segments))
    else:
        st.caption(f"帳戶 {account} {symbol} 還沒有任何紀錄")
    marks = fs.overlap_marks(segments, start, end)
    if marks["start"]:
        start_cols[1].error(f"🔴 開始重疊:{marks['start'].label()}")
    if marks["end"]:
        end_cols[1].error(f"🔴 結束重疊:{marks['end'].label()}")
    for g in marks["inside"]:
        st.error(f"🔴 重疊:這段時間包住了 {g.label()}")
    return bool(marks["start"] or marks["end"] or marks["inside"])


def _backfill_section():
    """補記過去一段(2026-10-13):開始、結束都在過去;從 Bybit 拉回那段期間的單與成交,
    預覽 → 勾確認 → 一次寫進 DB。策略名稱可填(預設 free style),例如把 sat_strategy 的交易記進來。"""
    st.caption("忘了按開始的手動交易、或沒寫進這個資料庫的程式交易,都可以補記。只讀 Bybit,不下單或取消。"
               "跟同帳戶同幣種已有的紀錄時間重疊會被擋下(不然成交會算兩次)。")
    c1, c2, c3 = st.columns(3)
    account = c1.selectbox("帳戶", data.list_accounts(), key="bf_account")
    with c2:
        symbol = _symbol_input("ETH", key="bf_symbol")
    name = c3.text_input("策略名稱", value=fs.FREE_STYLE, key="bf_name").strip()
    now = datetime.now(HKT).replace(second=0, microsecond=0)
    s1, s2, e1, e2 = st.columns(4)
    for key, default in (("bf_start_date", now.date()), ("bf_start_time", now.time()),
                         ("bf_end_date", now.date()), ("bf_end_time", now.time())):
        st.session_state.setdefault(key, default)  # 預設現在;總覽「未記錄的交易」一鍵帶入時會先寫好
    start_day = s1.date_input("開始日期(HKT)", max_value=now.date(), format="YYYY-MM-DD", key="bf_start_date")
    start_time = s2.time_input("開始時間(HKT)", step=60, key="bf_start_time")
    end_day = e1.date_input("結束日期(HKT)", max_value=now.date(), format="YYYY-MM-DD", key="bf_end_date")
    end_time = e2.time_input("結束時間(HKT)", step=60, key="bf_end_time")
    start = datetime.combine(start_day, start_time, tzinfo=HKT).astimezone(timezone.utc)
    end = datetime.combine(end_day, end_time, tzinfo=HKT).astimezone(timezone.utc)
    overlapped = _backfill_overlap(account, symbol, start, end, (s1, s2), (e1, e2))

    st.markdown("**選填(只填你確定的;會標明是事後補填)**")
    h1, h2, h3 = st.columns(3)
    hold_value = h1.number_input("預計持倉時間(0 = 不填)", min_value=0.0, value=0.0, format="%g", key="bf_hold_value")
    hold_unit = h2.selectbox("單位", list(_HOLD_UNITS), index=1, key="bf_hold_unit")
    direction = h3.selectbox("方向", ["不填", *fs.BACKFILL_DIRECTIONS], key="bf_direction")
    note = st.text_area("備註", max_chars=fs.MAX_NOTE_CHARS, key="bf_note").strip()
    user = {
        "expected_hold": timedelta(**{_HOLD_UNITS[hold_unit]: hold_value}) if hold_value else None,
        "direction": None if direction == "不填" else direction,
        "note": note or None,
    }
    signature = (account, symbol, name, start, end, tuple(user.items()))

    if st.button("🔍 預覽(從 Bybit 拉回,不寫入)", key="bf_preview", disabled=overlapped):
        if not symbol:
            st.warning("請填幣種")
        else:
            try:
                with st.spinner("從 Bybit 拉回訂單與成交…"):
                    preview = fs.preview_backfill(DB_URL, data.make_client(account), symbol, start, end,
                                                  datetime.now(timezone.utc), account=account, strategy_name=name,
                                                  **user)
                st.session_state["bf_pending"] = {"sig": signature, "preview": preview}
            except Exception as e:  # noqa: BLE001  FreeStyleError / AccountError
                st.session_state.pop("bf_pending", None)
                st.error(f"❌ {e}")
    pending = st.session_state.get("bf_pending")
    if not pending:
        return
    if pending["sig"] != signature:
        st.warning("條件在預覽之後改過了,請重新預覽")
        return
    p = pending["preview"]
    st.dataframe(pd.DataFrame([{
        "帳戶": p.account, "幣種": p.symbol, "策略名稱": p.strategy_name,
        "期間(HKT)": f"{hkt(p.start)} → {hkt(p.end)}", "訂單": p.orders, "完成幾輪": p.events,
        "毛利": round(p.gross_pnl, 4), "成交手續費": round(p.trade_fees, 4), "結束時持倉": p.open_position,
        "預計持倉時間": f"{hold_value:g} {hold_unit}" if user["expected_hold"] else "—",
        "方向": user["direction"] or "—", "備註": user["note"] or "—",
    }]), hide_index=True, width="stretch")
    st.caption("淨利(含資金費)寫入後計算。")
    for w in p.warnings:
        st.warning(w)
    ok = st.checkbox("我確認要寫進 trading 資料庫", key="bf_confirm")
    if st.button("💾 寫入資料庫", key="bf_write"):
        if not ok:
            st.warning("請先勾「我確認」")
            return
        try:
            with st.spinner("重新拉資料並寫入…"):
                result = fs.backfill(DB_URL, data.make_client(account), symbol, start, end, datetime.now(timezone.utc),
                                     account=account, strategy_name=name, **user)
            text = fs.summary_message(result)
            fs.send_telegram(text)
            st.success("已寫入")
            st.code(text, language=None)
            st.session_state.pop("bf_pending", None)
        except Exception as e:  # noqa: BLE001
            st.error(f"❌ {e}")


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

with tab_backfill:
    st.subheader("補記已經結束的交易")
    _backfill_section()
