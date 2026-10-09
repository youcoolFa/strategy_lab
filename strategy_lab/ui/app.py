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
from datetime import datetime, timezone
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

tab_overview, tab_records, tab_log, tab_strategy = st.tabs(["總覽", "交易紀錄", "Log", "策略"])

# ---------------------------------------------------------------------------
with tab_overview:
    st.subheader("背景程式(daemon)")
    try:
        daemons = data.list_daemons()
        unfinished = data.unfinished_by_symbol(DB_URL) if DB_URL else {}
    except Exception as e:  # noqa: BLE001
        daemons, unfinished = [], {}
        st.error(f"讀取狀態失敗:{e}")
    if daemons:
        rows = []
        for d in daemons:
            run = unfinished.get(d.symbol) if d.alive else None
            plan = data.schedule(data.config_path(d.config), run["started_at"]) if run else None
            rows.append({
                "設定檔": d.config, "幣種": d.symbol or "—",
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
                   "每小時一定會寫 ⚪ 狀態,超過 70 分鐘沒寫 log 通常是 Mac 睡著了。")
    else:
        st.info("沒有 live_*.yaml 執行設定")

    st.subheader("Bybit 帳戶(只讀,30 秒快取)")

    @st.cache_data(ttl=30, show_spinner="查詢 Bybit…")
    def snapshot():
        return data.account_snapshot(data.make_client())

    try:
        snap = snapshot()
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
    sym = row["symbol"]
    ok = st.checkbox("我確認要結束(從 Bybit 拉回訂單寫進資料庫、發 Telegram)", key=f"fs_stop_confirm_{sym}")
    if st.button("⏹️ 結束 free style", key=f"fs_stop_{sym}"):
        if not ok:
            st.warning("請先勾「我確認」")
            return
        try:
            with st.spinner("從 Bybit 拉回訂單與成交…"):
                result = fs.stop(DB_URL, data.make_client(), sym, datetime.now(timezone.utc))
            text = fs.summary_message(result)
            fs.send_telegram(text)
            st.success("完成")
            st.code(text, language=None)
        except fs.FreeStyleError as e:
            st.error(f"❌ {e}")


def _start_free_style():
    st.caption("自己在 Bybit 手動交易,這裡只負責記帳:開始只記時間,結束時從 Bybit 拉回這段期間的單寫進資料庫。不會下單或取消。")
    symbol = st.text_input("幣種(Bybit 格式)", placeholder="ETHUSDT", key="fs_symbol").strip().upper()
    since = st.text_input("補記開始時間(HKT,選填)", placeholder="2026-10-09 14:00", key="fs_since").strip()
    ok = st.checkbox("我確認要開始記錄(會寫進 trading 資料庫)", key="fs_start_confirm")
    if st.button("▶️ 開始 free style", key="fs_start"):
        if not symbol:
            st.warning("請填幣種")
        elif not ok:
            st.warning("請先勾「我確認」")
        else:
            try:
                when = fs._parse_since(since) if since else datetime.now(timezone.utc)
                run_id = fs.start(DB_URL, data.make_client(), symbol, when)
                st.success(f"✅ {symbol} free style 開始({hkt(when)} HKT,run_id {run_id[:8]})")
            except fs.FreeStyleError as e:
                st.error(f"❌ {e}")
            except ValueError:
                st.error("補記時間格式要像 2026-10-09 14:00")


def _start_strategy(choice, root):
    strategy_path = f"strategies/{choice}.yaml"
    info = _strategy_info(root, strategy_path)
    st.caption(f"方向 {info.get('direction')}" + (f"|分注 {info['lots']} 注" if info.get("scale_in") else ""))
    symbol = st.text_input("幣種(Bybit 格式)", placeholder="WLDUSDT", key="new_symbol").strip().upper()
    if not symbol:
        st.caption("填幣種後繼續")
        return
    path = actions.config_for(root, strategy_path, symbol)
    existing = path.exists()
    if existing and actions.daemon_running(path):
        st.info(f"{choice} / {symbol} 已經在跑(設定檔 {path.name});要改參數或停止,請用上面的進行中清單")
        return
    cfg = _read_yaml(path) if existing else {}
    st.caption(f"設定檔:{path.name}({'沿用現有的' if existing else '新建,從範本建立'})")

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
    signature = (path.name, settings, params)

    if st.button("💾 寫入參數並預覽", key="new_prepare"):
        try:
            with st.spinner("寫入設定檔、查詢市場資料…"):
                written = actions.prepare_config(root, strategy_path, symbol, settings, params)
                preview = actions.preflight_preview(written)
            st.session_state["new_preview"] = {"sig": signature, "path": str(written), "preview": preview}
        except ValueError as e:
            st.session_state.pop("new_preview", None)
            st.error(str(e))
    pending = st.session_state.get("new_preview")
    if not pending:
        return
    if pending["sig"] != signature:
        st.warning("參數在預覽之後改過了,請重新按「寫入參數並預覽」")
        return
    preview = pending["preview"]
    st.code(preview.text, language=None)
    if not preview.ready:
        st.error("預覽的檢查沒有通過,不能開始(看上面的 ✗ 原因)")
        return
    word, label = "yes", "以真實資金開始"
    typed = st.text_input(f"確認{label}:輸入 {word}", key="new_typed")
    if st.button(f"🚀 {label}", key="new_start"):
        try:
            with st.spinner("啟動中(會再查一次市場、確認背景程式沒有立刻結束)…"):
                ok, text = actions.preflight_start(Path(pending["path"]), preview, typed)
            (st.success if ok else st.error)("已在背景開始" if ok else "沒有開始")
            st.code(text, language=None)
            if ok:  # 打錯字之類沒開始:保留預覽,可以直接再試
                st.session_state.pop("new_preview", None)
        except ValueError as e:
            st.error(str(e))


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
        title = f"{row['mode']}|{row['symbol']}|{row['strategy']}" + (
            f"|{hkt(row['started_at'])} 開始" if row.get("started_at") else "")
        with st.expander(title):
            if row["kind"] == FREE:
                _free_style_row(row)
            else:
                _running_strategy_row(row, root)

    st.subheader("開始新的")
    names = [p.stem for p in actions.list_strategies(ROOT)] + [FREE]
    choice = st.radio("① 選策略", names, key="new_strategy", horizontal=True)
    st.markdown("**② 填參數 → ③ 開始**")
    if choice == FREE:
        _start_free_style()
    else:
        _start_strategy(choice, root)
