"""損益表的 AG Grid 設定(2026-10-14)。用的是 AG Grid **免費版 Community**(`streamlit-aggrid`):
像 Excel 一樣點欄位標題 → 選單裡排序 / 篩選(文字「包含 / 等於」、數字「大於 / 小於 / 介於」、日期區間)。
Excel 那種勾選清單式篩選是付費版(Enterprise)才有,這裡不用。

只產生 gridOptions,不 import streamlit,可以單獨測試。"""

from __future__ import annotations

from typing import Any, Dict, List

from st_aggrid import JsCode

EDITABLE = ("預估時長", "目標價", "停損價", "備註")
HIDDEN = ("run_id", "event_index", "實際秒數", "預估秒數")
NUMBER_DIGITS = {"數量": None, "建倉價格": 2, "平倉價格": 2, "真實損益": 0, "手續費": 0, "手續費佔比(%)": 2,
                 "目標價": None, "停損價": None}
SECONDS_OF = {"實際時長": "實際秒數", "預估時長": "預估秒數"}
# 每欄寬度(px):AG Grid 的自動欄寬量不準中文,會把日期、幣種、方向縮成一個字(2026-10-14 實際看到)
WIDTH = {"日期": 110, "幣種": 100, "方向": 75, "數量": 75, "建倉價格": 95, "建倉時間": 115, "平倉價格": 95,
         "真實損益": 90, "手續費": 80, "手續費佔比(%)": 115, "預估時長": 135, "實際時長": 140, "目標價": 120,
         "停損價": 120, "備註": 200}
EDIT_HINT = {"預估時長": "預估時長(可後補)", "目標價": "目標價(可後補)", "停損價": "停損價(可後補)", "備註": "備註(可後補)"}

# 日期欄是 "2026/10/13" 文字:日期篩選要自己比
_DATE_COMPARATOR = JsCode("""
function(filterDate, cell) {
    if (!cell) return -1;
    const p = cell.split('/');
    const d = new Date(Number(p[0]), Number(p[1]) - 1, Number(p[2]));
    if (d < filterDate) return -1;
    if (d > filterDate) return 1;
    return 0;
}""")


def _fixed(digits: int) -> JsCode:
    return JsCode(f"function(p) {{ return (p.value === null || p.value === undefined || p.value === '')"
                  f" ? '' : Number(p.value).toFixed({digits}); }}")


def _by_seconds(field: str) -> JsCode:
    """時長照秒數排(22 天不會排在 1 小時後面);空的排最後。"""
    return JsCode(f"""
function(a, b, nodeA, nodeB, isDescending) {{
    const x = nodeA.data['{field}'], y = nodeB.data['{field}'];
    if (x === null || x === undefined) return isDescending ? -1 : 1;
    if (y === null || y === undefined) return isDescending ? 1 : -1;
    return x - y;
}}""")


def pnl_grid_options(columns: List[str]) -> Dict[str, Any]:
    defs: List[Dict[str, Any]] = []
    for col in columns:
        d: Dict[str, Any] = {"field": col, "headerName": EDIT_HINT.get(col, col), "sortable": True,
                             "resizable": True, "filter": "agTextColumnFilter", "width": WIDTH.get(col, 100)}
        if col == "日期":
            d.update(filter="agDateColumnFilter", filterParams={"comparator": _DATE_COMPARATOR}, sort="desc",
                     pinned="left")
        elif col in NUMBER_DIGITS:
            d.update(filter="agNumberColumnFilter", type=["numericColumn"])
            if NUMBER_DIGITS[col] is not None:
                d["valueFormatter"] = _fixed(NUMBER_DIGITS[col])
        if col in SECONDS_OF:
            d["comparator"] = _by_seconds(SECONDS_OF[col])
        if col in EDITABLE:
            d.update(editable=True, cellStyle={"backgroundColor": "rgba(255, 215, 0, 0.08)"})
            if col in ("目標價", "停損價"):
                d["valueParser"] = JsCode("function(p) { const v = p.newValue; "
                                          "return (v === '' || v === null) ? null : Number(v); }")
        defs.append(d)
    defs += [{"field": c, "hide": True} for c in HIDDEN]
    return {
        "columnDefs": defs,
        "defaultColDef": {"filter": True, "sortable": True, "resizable": True, "floatingFilter": False},
        "singleClickEdit": True,
        "stopEditingWhenCellsLoseFocus": True,
        "animateRows": False,
    }
