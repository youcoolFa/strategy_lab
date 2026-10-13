"""損益表的 AG Grid(免費版 Community)設定(2026-10-14):像 Excel 一樣點欄位標題排序 / 篩選。
只測設定(gridOptions),不開頁面。"""

from strategy_lab.ui import grid

COLS = ["日期", "幣種", "方向", "數量", "建倉價格", "建倉時間", "平倉價格", "真實損益", "手續費", "手續費佔比(%)",
        "預估時長", "實際時長", "目標價", "停損價", "備註"]


def defs():
    return {c["field"]: c for c in grid.pnl_grid_options(COLS)["columnDefs"]}


def test_every_column_sorts_and_filters_from_its_header():
    d = defs()
    for c in COLS:
        assert d[c].get("sortable", True) and d[c]["filter"], c
    assert d["日期"]["filter"] == "agDateColumnFilter"
    assert d["真實損益"]["filter"] == d["數量"]["filter"] == "agNumberColumnFilter"
    assert d["幣種"]["filter"] == d["備註"]["filter"] == "agTextColumnFilter"


def test_only_the_backfill_fields_are_editable():
    d = defs()
    assert {c for c in COLS if d[c].get("editable")} == {"預估時長", "目標價", "停損價", "備註"}


def test_hidden_keys_and_seconds_columns():
    d = defs()
    for c in ("run_id", "event_index", "實際秒數", "預估秒數"):
        assert d[c]["hide"] is True


def test_number_formats_and_duration_sort_by_seconds():
    d = defs()
    assert "toFixed(2)" in d["建倉價格"]["valueFormatter"].js_code and "toFixed(2)" in d["平倉價格"]["valueFormatter"].js_code
    assert "toFixed(0)" in d["真實損益"]["valueFormatter"].js_code and "toFixed(0)" in d["手續費"]["valueFormatter"].js_code
    assert "實際秒數" in d["實際時長"]["comparator"].js_code and "預估秒數" in d["預估時長"]["comparator"].js_code


def test_default_sort_is_newest_first():
    d = defs()
    assert d["日期"]["sort"] == "desc"


def test_every_column_has_a_fixed_width():
    """自動欄寬量不準中文(日期、幣種、方向被縮成一個字,2026-10-14 實際看到)→ 每欄指定寬度"""
    opts = grid.pnl_grid_options(COLS)
    assert "autoSizeStrategy" not in opts
    d = defs()
    assert all(isinstance(d[c].get("width"), int) and d[c]["width"] >= 70 for c in COLS)
    assert d["日期"]["width"] >= 100 and d["備註"]["width"] >= 160
