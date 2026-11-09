"""
タイムシリーズファイル閲覧 Tool。

実装 Tool（仕様書 §10.1 より Phase 1 対象）:
  6. inspect_timeseries_file  [A / read_only / P0]
     対応形式: .sto, .mot, .trc, .c3d, .csv
"""

from __future__ import annotations

import csv
import logging
from pathlib import Path
from typing import TYPE_CHECKING, Any

from ..schemas import error_result, tool_result
from ..workspace import resolve_path

if TYPE_CHECKING:
    from mcp.server.fastmcp import FastMCP

logger = logging.getLogger(__name__)

_SUPPORTED_EXTENSIONS = frozenset({".sto", ".mot", ".trc", ".c3d", ".csv"})


def register_file_tools(mcp: "FastMCP") -> None:
    """ファイル系 Tool を mcp インスタンスに登録する。"""

    @mcp.tool()
    def inspect_timeseries_file(file_path: str) -> dict[str, Any]:
        """
        .sto / .mot / .trc / .c3d / .csv の要約情報を返す。

        Args:
            file_path: workspace 内のファイルパス（相対 or 絶対）。

        Returns:
            file_type / time_range / num_rows / num_columns / column_names / preview
        """
        return inspect_file(file_path)


def inspect_file(file_path: str) -> dict[str, Any]:
    """inspect_timeseries_file の本体。Resource opensim://file/summary/{path} からも使う。"""
    try:
        p = resolve_path(file_path)
    except ValueError as exc:
        return error_result("PATH_ERROR", str(exc))

    if not p.exists():
        return error_result("PATH_ERROR", f"ファイルが見つかりません: {file_path}")

    ext = p.suffix.lower()
    if ext not in _SUPPORTED_EXTENSIONS:
        return error_result(
            "INPUT_ERROR",
            f"'{ext}' は対応していません。対応: {', '.join(sorted(_SUPPORTED_EXTENSIONS))}",
        )

    if ext in (".sto", ".mot"):
        return _inspect_storage(p)
    elif ext == ".trc":
        return _inspect_trc(p)
    elif ext == ".c3d":
        return _inspect_c3d(p)
    else:  # .csv
        return _inspect_csv(p)


# ---------------------------------------------------------------------------
# 形式別パーサー
# ---------------------------------------------------------------------------


def _inspect_storage(p: Path) -> dict[str, Any]:
    """OpenSim Storage (.sto / .mot) の要約を返す。"""
    import opensim  # noqa: PLC0415

    try:
        sto = opensim.Storage(str(p))
        num_rows = sto.getSize()

        labels = sto.getColumnLabels()
        col_names = [labels.get(i) for i in range(labels.getSize())]

        first_time = sto.getFirstTime() if num_rows > 0 else None
        last_time  = sto.getLastTime()  if num_rows > 0 else None

        # 統計情報の計算 (CPU負荷を抑えるため代表的な列のみ or 先頭N件検討も可能だが、現状全件)
        stats = {}
        if num_rows > 0:
            for name in col_names:
                if name == "time":
                    continue
                try:
                    data_arr = opensim.ArrayDouble()
                    sto.getDataColumn(name, data_arr)
                    vals = [data_arr.get(i) for i in range(data_arr.getSize())]
                    stats[name] = {
                        "min": min(vals),
                        "max": max(vals),
                        "avg": sum(vals) / len(vals)
                    }
                except:
                    continue

        return tool_result(
            status="completed",
            summary=(
                f"{p.name}: {num_rows} 行 × {len(col_names)} 列,"
                f" {_fmt_t(first_time)}s ~ {_fmt_t(last_time)}s"
            ),
            structured_data={
                "file_type": p.suffix.lstrip(".").upper(),
                "time_range": [first_time, last_time],
                "num_rows": num_rows,
                "num_columns": len(col_names),
                "column_names": col_names,
                "statistics": stats,
                "preview": None,
            },
        )
    except Exception as exc:
        return error_result("EXECUTION_ERROR", f"ファイル読込失敗: {exc}")


def _inspect_trc(p: Path) -> dict[str, Any]:
    """TRC マーカーファイルの要約を返す。"""
    import opensim  # noqa: PLC0415

    try:
        # TRCFileAdapter().read() は型の無い StdMapStringAbstractDataTable を返すので、
        # TimeSeriesTableVec3 で直接読む
        table = opensim.TimeSeriesTableVec3(str(p))

        num_rows = table.getNumRows()
        num_cols = table.getNumColumns()
        labels = table.getColumnLabels()
        col_names = [labels[i] for i in range(len(labels))]

        time_col = table.getIndependentColumn()
        first_time = float(time_col[0])  if num_rows > 0 else None
        last_time  = float(time_col[-1]) if num_rows > 0 else None

        # TRC 統計 (ノルムで計算)
        stats = {}
        if num_rows > 0:
            for name in col_names:
                try:
                    col_data = table.getDependentColumn(name)
                    norms = [float(col_data[i].norm()) for i in range(len(col_data))]
                    stats[name] = {
                        "min_norm": min(norms),
                        "max_norm": max(norms),
                        "avg_norm": sum(norms) / len(norms)
                    }
                except:
                    continue

        return tool_result(
            status="completed",
            summary=(
                f"{p.name}: {num_rows} フレーム × {num_cols} マーカー,"
                f" {_fmt_t(first_time)}s ~ {_fmt_t(last_time)}s"
            ),
            structured_data={
                "file_type": "TRC",
                "time_range": [first_time, last_time],
                "num_rows": num_rows,
                "num_columns": num_cols,
                "column_names": col_names,
                "statistics": stats,
                "preview": None,
            },
        )
    except Exception as exc:
        return error_result("EXECUTION_ERROR", f"TRC ファイル読込失敗: {exc}")


def _inspect_c3d(p: Path) -> dict[str, Any]:
    """C3D ファイルの要約を返す。"""
    import opensim  # noqa: PLC0415

    try:
        adapter = opensim.C3DFileAdapter()
        tables = adapter.read(str(p))
        # tables[...] は AbstractDataTable なので、型付きテーブルは getter で取り出す
        marker_table = adapter.getMarkersTable(tables)
        force_table = adapter.getForcesTable(tables)

        num_rows = marker_table.getNumRows()
        num_cols = marker_table.getNumColumns()
        labels = marker_table.getColumnLabels()
        col_names = [labels[i] for i in range(len(labels))]

        time_col = marker_table.getIndependentColumn()
        first_time = float(time_col[0])  if num_rows > 0 else None
        last_time  = float(time_col[-1]) if num_rows > 0 else None

        # 力データが含まれるか確認
        has_forces = force_table.getNumColumns() > 0
        extra = " (力データあり)" if has_forces else ""

        return tool_result(
            status="completed",
            summary=(
                f"{p.name}: {num_rows} フレーム × {num_cols} マーカー,"
                f" {_fmt_t(first_time)}s ~ {_fmt_t(last_time)}s{extra}"
            ),
            structured_data={
                "file_type": "C3D",
                "time_range": [first_time, last_time],
                "num_rows": num_rows,
                "num_columns": num_cols,
                "column_names": col_names,
                "has_forces": has_forces,
                "preview": None,
            },
        )
    except Exception as exc:
        return error_result("EXECUTION_ERROR", f"C3D ファイル読込失敗: {exc}")


def _inspect_csv(p: Path) -> dict[str, Any]:
    """CSV ファイルの要約を返す。"""
    try:
        with open(p, newline="", encoding="utf-8-sig") as f:
            reader = csv.reader(f)
            rows = list(reader)

        if not rows:
            return tool_result(
                status="completed",
                summary=f"{p.name}: 空ファイル",
                structured_data={
                    "file_type": "CSV", "num_rows": 0, "num_columns": 0,
                    "column_names": [], "time_range": None, "preview": [],
                },
            )

        header = rows[0]
        data_rows = rows[1:]
        preview = data_rows[:3]

        return tool_result(
            status="completed",
            summary=f"{p.name}: {len(data_rows)} 行 × {len(header)} 列",
            structured_data={
                "file_type": "CSV",
                "time_range": None,
                "num_rows": len(data_rows),
                "num_columns": len(header),
                "column_names": header,
                "preview": preview,
            },
        )
    except Exception as exc:
        return error_result("EXECUTION_ERROR", f"CSV 読込失敗: {exc}")


def _fmt_t(t: float | None) -> str:
    return f"{t:.3f}" if t is not None else "N/A"
