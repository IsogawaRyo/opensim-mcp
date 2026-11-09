"""
GUI/デバッグ支援用 Resources。

実装 Resources:
  opensim://timeseries/{path} - 時系列データ (.sto, .mot, .trc) を JSON 形式で詳細配信
  opensim://timeseries/{path}/frames?start={n}&end={m} - 範囲指定した時系列データ
  opensim://model/visual - モデルの 3D 可視化データ (全節の Transform と Mesh 情報)
  opensim://model/visual/marker_errors/{path} - IK マーカー誤差データを詳細配信
  opensim://model/geometry/{filename} - メッシュファイルをバイナリ/テキストで配信
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import TYPE_CHECKING, Any
from urllib.parse import unquote

from ..model_service import (
    get_current_model,
    get_model_visual_data,
    get_model_visual_data_at_state,
)
from ..schemas import resource_result
from ..config import WORKSPACE_DIR
from ..workspace import resolve_path, resolve_within

# opensim://model/geometry/{filename} が返してよいメッシュの拡張子
_GEOMETRY_EXTENSIONS = frozenset({".vtp", ".obj", ".stl", ".ply"})

if TYPE_CHECKING:
    from mcp.server.fastmcp import FastMCP

logger = logging.getLogger(__name__)


def _decode_resource_path(path: str) -> str:
    return unquote(path)


def register_gui_resources(mcp: "FastMCP") -> None:
    """GUI 支援系 Resource を mcp インスタンスに登録する。"""

    # ------------------------------------------------------------------
    # opensim://model/visual
    # ------------------------------------------------------------------

    @mcp.resource("opensim://model/visual")
    def model_visual() -> str:
        """
        現在ロードされているモデルの 3D 可視化データ。
        全体節の Ground に対する位置・回転、および付随するメッシュ情報を返す。
        """
        source = "opensim://model/visual"
        model = get_current_model()
        if model is None:
            return json.dumps(
                resource_result(source, "モデル未ロード", warnings=["先に load_model を実行してください"]),
                ensure_ascii=False,
            )

        try:
            vis_data = get_model_visual_data(model)
            return json.dumps(
                resource_result(source, f"{model.getName()}: {len(vis_data)} elements", structured_data=vis_data),
                ensure_ascii=False,
            )
        except Exception as exc:
            return json.dumps(resource_result(source, f"Error: {exc}"), ensure_ascii=False)

    # ------------------------------------------------------------------
    # opensim://timeseries/{path}
    # ------------------------------------------------------------------

    @mcp.resource("opensim://timeseries/{path}")
    def timeseries_data(path: str) -> str:
        """
        指定した時系列ファイル (.sto, .mot, .trc) の中身を JSON で返す。
        GUI でのグラフ描画や詳細なデータ確認に使用する。
        """
        source = f"opensim://timeseries/{path}"
        try:
            p = resolve_path(_decode_resource_path(path))
        except ValueError as exc:
            return json.dumps(resource_result(source, str(exc), warnings=[str(exc)]),
                              ensure_ascii=False)

        if not p.exists():
            return json.dumps(resource_result(source, f"File not found: {path}"),
                              ensure_ascii=False)

        ext = p.suffix.lower()
        try:
            if ext in (".sto", ".mot"):
                data = _read_storage_full(p)
            elif ext == ".trc":
                data = _read_trc_full(p)
            else:
                return json.dumps(resource_result(source, f"Unsupported extension: {ext}"),
                                  ensure_ascii=False)

            return json.dumps(
                resource_result(
                    source,
                    f"{p.name} ({len(data['time'])} rows)",
                    structured_data=data
                ),
                ensure_ascii=False
            )
        except Exception as exc:
            return json.dumps(resource_result(source, f"Read error: {exc}"),
                              ensure_ascii=False)

    # ------------------------------------------------------------------
    # opensim://timeseries/{path}/frames
    # ------------------------------------------------------------------

    @mcp.resource("opensim://timeseries/{path}/frames")
    def timeseries_frames(path: str) -> str:
        """
        指定した範囲の時系列データを JSON で返す。
        """
        source = f"opensim://timeseries/{path}/frames"
        # 現状の FastMCP は URI パラメータのみ関数の引数に取れるため、
        # start/end 指定機能は将来的な拡張とし、一旦全件返す。
        try:
            p = resolve_path(_decode_resource_path(path))
            ext = p.suffix.lower()
            if ext in (".sto", ".mot"):
                data = _read_storage_full(p)
            elif ext == ".trc":
                data = _read_trc_full(p)
            else:
                return json.dumps(resource_result(source, f"Unsupported extension: {ext}"), ensure_ascii=False)

            return json.dumps(
                resource_result(source, f"{p.name} (full)", structured_data=data),
                ensure_ascii=False
            )
        except Exception as exc:
            return json.dumps(resource_result(source, f"Error: {exc}"), ensure_ascii=False)

    # ------------------------------------------------------------------
    # opensim://model/geometry/{filename}
    # ------------------------------------------------------------------

    @mcp.resource("opensim://model/geometry/{filename}")
    def model_geometry(filename: str) -> bytes:
        """
        メッシュファイル (.vtp 等) を配信する。
        """
        # 探す順: 読み込み中のモデルと同じフォルダの Geometry/(OpenSim の標準)→ models/original/Geometry/
        geom_dirs = []
        current = get_current_model()
        if current is not None and current.getInputFileName():
            geom_dirs.append(Path(current.getInputFileName()).parent / "Geometry")
        geom_dirs.append(WORKSPACE_DIR / "models/original/Geometry")

        name = _decode_resource_path(filename)
        for geom_dir in geom_dirs:
            # "../" を URL エンコードして Geometry の外(ワークスペース外を含む)を読まれないよう、
            # Geometry の中に限って解決し、メッシュの拡張子だけ返す
            try:
                target = resolve_within(resolve_path(str(geom_dir)), name)
            except ValueError:
                continue
            if target.suffix.lower() in _GEOMETRY_EXTENSIONS and target.is_file():
                return target.read_bytes()
        return b"File not found"

    # ------------------------------------------------------------------
    # opensim://model/visual/frames
    # ------------------------------------------------------------------

    @mcp.resource("opensim://model/visual/frames/{path}")
    def model_visual_frames(path: str) -> str:
        """
        指定した運動ファイル (.mot) に基づく 3D アニメーションデータを JSON で返す。
        """
        source = f"opensim://model/visual/frames"
        model = get_current_model()
        if model is None:
            return json.dumps(resource_result(source, "モデル未ロード"), ensure_ascii=False)

        try:
            p = resolve_path(_decode_resource_path(path))
            data = _read_storage_full(p)
            
            # 各フレームの全節トランスフォームを計算
            frames_vis = []
            num_frames = len(data["time"])

            for i in range(num_frames):
                row_values = [data["columns"][col][i] for col in data["labels"] if col in data["columns"]]
                labels = [col for col in data["labels"] if col in data["columns"]]
                vis = get_model_visual_data_at_state(model, labels, row_values)
                frames_vis.append({
                    "time": data["time"][i],
                    "bodies": vis
                })

            return json.dumps(
                resource_result(
                    source,
                    f"{p.name} ({len(frames_vis)} frames)",
                    structured_data={
                        "path": str(p),
                        "frame_count": len(frames_vis),
                        "frames": frames_vis,
                    },
                ),
                ensure_ascii=False
            )
        except Exception as exc:
            return json.dumps(resource_result(source, f"Error: {exc}"), ensure_ascii=False)

    # ------------------------------------------------------------------
    # opensim://model/visual/marker_errors/{path}
    # ------------------------------------------------------------------

    @mcp.resource("opensim://model/visual/marker_errors/{path}")
    def model_marker_errors(path: str) -> str:
        """
        指定した指定した IK 誤差ファイル (*_ik_marker_errors.sto) を JSON で返す。
        """
        source = f"opensim://model/visual/marker_errors/{path}"
        try:
            p = resolve_path(_decode_resource_path(path))
            if not p.exists():
                return json.dumps(resource_result(source, f"File not found: {path}"), ensure_ascii=False)
            
            data = _read_storage_full(p)
            return json.dumps(
                resource_result(source, f"{p.name} (marker errors)", structured_data=data),
                ensure_ascii=False
            )
        except Exception as exc:
            return json.dumps(resource_result(source, f"Error: {exc}"), ensure_ascii=False)


# ---------------------------------------------------------------------------
# 内部ヘルパー (データの全件読み取り)
# ---------------------------------------------------------------------------


def _read_storage_full(p: Any) -> dict[str, Any]:
    """OpenSim Storage ファイルを全件読み込む。"""
    import opensim  # noqa: PLC0415
    sto = opensim.Storage(str(p))
    labels = sto.getColumnLabels()
    col_names = [labels.get(i) for i in range(labels.getSize())]
    
    # データの抽出
    time = []
    columns: dict[str, list[float]] = {name: [] for name in col_names if name != "time"}
    
    for i in range(sto.getSize()):
        row = sto.getStateVector(i)
        time.append(row.getTime())
        data = row.getData()
        # Storage のデータ列は time を除いた順番
        data_idx = 0
        for name in col_names:
            if name == "time":
                continue
            columns[name].append(data.get(data_idx))
            data_idx += 1
            
    return {
        "time": time,
        "columns": columns,
        "labels": col_names
    }


def _read_trc_full(p: Any) -> dict[str, Any]:
    """TRC ファイルを全件読み込む。"""
    import opensim  # noqa: PLC0415
    adapter = opensim.TRCFileAdapter()
    table = adapter.read(str(p))
    
    time_col = table.getIndependentColumn()
    time = [float(time_col[i]) for i in range(len(time_col))]
    
    labels = table.getColumnLabels()
    col_names = [labels[i] for i in range(len(labels))]
    
    columns: dict[str, list[list[float]]] = {}
    for i, name in enumerate(col_names):
        col_data = table.getDependentColumn(name)
        # TRC は各セルが Vec3 (x, y, z)
        columns[name] = [
            [col_data[j][0], col_data[j][1], col_data[j][2]]
            for j in range(len(col_data))
        ]
        
    return {
        "time": time,
        "columns": columns,
        "labels": col_names
    }
