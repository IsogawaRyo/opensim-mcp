"""
構造編集系 Tools（Phase 3 / v2）。

実装 Tools（仕様書 §10.3 より）:
  13. add_component        [D / build / P2]
  14. remove_component     [D / build / P2]
  15. set_socket_connection [D / build / P2]
  16. export_model         [C / build / P2]

設計原則:
  - 元ファイルは変更しない（clone ベースの操作）
  - 構造変更は必ず output_model_path に書き出す
  - add/remove は clone 後の作業コピーに対してのみ行う
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import TYPE_CHECKING, Any

from ..config import WORKSPACE_DIR
from ..model_service import AmbiguousComponentError, get_component_by_name
from ..model_service import load_model as svc_load_model
from ..schemas import error_result, tool_result
from ..workspace import resolve_output_path, resolve_path

if TYPE_CHECKING:
    from mcp.server.fastmcp import FastMCP

logger = logging.getLogger(__name__)


def register_structural_tools(mcp: "FastMCP") -> None:
    """Phase 3 構造編集系 Tool を mcp インスタンスに登録する。"""

    # ------------------------------------------------------------------
    # 13) add_component  [D / build / P2]
    # ------------------------------------------------------------------

    @mcp.tool()
    def add_component(
        target_model_path: str,
        component_kind: str,
        component_spec: dict[str, Any],
        output_model_path: str,
    ) -> dict[str, Any]:
        """
        モデルにコンポーネント（Force / ProbeReporter / PathActuator 等）を追加する。

        Args:
            target_model_path: 編集対象のモデルパス（clones/ 配下推奨）。
            component_kind: 追加するコンポーネント種別。
                以下の値をサポートします:
                - "PointActuator"
                - "CoordinateActuator"
                - "PathActuator"
                解析("ProbeReporter" / "JointReaction" / "BodyKinematics" / "PointKinematics")は
                OpenSim が .osim に保存しないためエラーになる。write_setup_xml(analyze) の analyses で指定する。
            component_spec: コンポーネントのパラメータ辞書。
                例 (CoordinateActuator):
                  {"name": "act_shoulder", "coordinate": "r_shoulder_elev",
                   "optimal_force": 500.0}
            output_model_path: 追加後のモデルを保存するパス（.osim）。

        Note:
            - 元ファイルは変更しません。必ず output_model_path に保存します。
            - models/clones/ 配下でのみ変更を推奨します（clone_model 参照）。
        """
        import opensim  # noqa: PLC0415

        # パス解決
        try:
            src = resolve_path(target_model_path)
        except ValueError as exc:
            return error_result("PATH_ERROR", str(exc))
        if not src.exists():
            return error_result("PATH_ERROR", f"モデルが見つかりません: {target_model_path}")

        try:
            dest = resolve_output_path(output_model_path, (src,))
        except ValueError as exc:
            return error_result("PATH_ERROR", str(exc))
        if dest.suffix.lower() != ".osim":
            return error_result("INPUT_ERROR", "output_model_path は .osim にしてください")

        # モデルをロード
        try:
            model = opensim.Model(str(src))
            model.initSystem()
        except Exception as exc:
            return error_result("EXECUTION_ERROR", f"モデルロード失敗: {exc}")

        # 解析(Analysis)は OpenSim が .osim に書き出さない(保存して読み直すと消える)ので受け付けない
        if component_kind in _ANALYSIS_KINDS:
            return error_result(
                "INPUT_ERROR",
                f"{component_kind} は解析(Analysis)で、OpenSim はモデルファイル(.osim)に保存しません",
                suggested_fix=(
                    "解析は setup 側で指定してください: "
                    f"write_setup_xml(tool_type='analyze', params={{..., 'analyses': ['{component_kind}']}})"
                ),
            )

        # コンポーネント生成(spec の誤りは「未サポート」ではなく作成失敗として返す)
        try:
            component, added_name = _create_component(opensim, component_kind, component_spec, model)
        except Exception as exc:
            return error_result("INPUT_ERROR", f"{component_kind} の作成に失敗しました: {exc}",
                                suggested_fix="component_spec の内容(body / coordinate / path_points など)を確認してください")
        if component is None:
            return error_result(
                "INPUT_ERROR",
                f"component_kind '{component_kind}' は未サポートです",
                suggested_fix=(
                    "サポートする component_kind: "
                    "PointActuator, CoordinateActuator, PathActuator"
                ),
            )

        # モデルに追加して保存。addComponent だとモデル直下に入り remove_component で消せないので ForceSet に入れる
        try:
            model.addForce(component)
            model.initSystem()
            dest.parent.mkdir(parents=True, exist_ok=True)
            model.printToXML(str(dest))
        except Exception as exc:
            return error_result("EXECUTION_ERROR", f"コンポーネント追加失敗: {exc}")

        return tool_result(
            status="completed",
            summary=f"'{added_name}' ({component_kind}) を追加しました",
            artifacts=[{"path": str(dest.relative_to(WORKSPACE_DIR)), "type": "model"}],
            structured_data={
                "output_model_path": str(dest.relative_to(WORKSPACE_DIR)),
                "added_component_name": added_name,
                "component_kind": component_kind,
            },
        )

    # ------------------------------------------------------------------
    # 14) remove_component  [D / build / P2]
    # ------------------------------------------------------------------

    @mcp.tool()
    def remove_component(
        target_model_path: str,
        component_name: str,
        output_model_path: str,
    ) -> dict[str, Any]:
        """
        モデルから指定コンポーネントを削除して別名保存する。

        Args:
            target_model_path: 編集対象のモデルパス。
            component_name: 削除するコンポーネント名。
            output_model_path: 削除後のモデルを保存するパス（.osim）。

        Note:
            - 元ファイルは変更しません。
            - ForceSet / AnalysisSet / ProbeSet のコンポーネントを対象とします。
        """
        import opensim  # noqa: PLC0415

        try:
            src = resolve_path(target_model_path)
        except ValueError as exc:
            return error_result("PATH_ERROR", str(exc))
        if not src.exists():
            return error_result("PATH_ERROR", f"モデルが見つかりません: {target_model_path}")

        try:
            dest = resolve_output_path(output_model_path, (src,))
        except ValueError as exc:
            return error_result("PATH_ERROR", str(exc))

        try:
            model = opensim.Model(str(src))
            model.initSystem()
        except Exception as exc:
            return error_result("EXECUTION_ERROR", f"モデルロード失敗: {exc}")

        removed = False
        # ForceSet から探す
        force_set = model.getForceSet()
        for i in range(force_set.getSize()):
            if force_set.get(i).getName() == component_name:
                force_set.remove(i)
                removed = True
                break

        if not removed:
            # AnalysisSet から探す
            analysis_set = model.getAnalysisSet()
            for i in range(analysis_set.getSize()):
                if analysis_set.get(i).getName() == component_name:
                    analysis_set.remove(i)
                    removed = True
                    break

        if not removed:
            return error_result(
                "INPUT_ERROR",
                f"'{component_name}' が見つかりません（ForceSet / AnalysisSet を検索）",
                suggested_fix="list_components で名前を確認してください",
            )

        try:
            model.initSystem()
            dest.parent.mkdir(parents=True, exist_ok=True)
            model.printToXML(str(dest))
        except Exception as exc:
            return error_result("EXECUTION_ERROR", f"保存失敗: {exc}")

        return tool_result(
            status="completed",
            summary=f"'{component_name}' を削除しました",
            artifacts=[{"path": str(dest.relative_to(WORKSPACE_DIR)), "type": "model"}],
            structured_data={
                "output_model_path": str(dest.relative_to(WORKSPACE_DIR)),
                "removed": True,
                "removed_component": component_name,
            },
        )

    # ------------------------------------------------------------------
    # 15) set_socket_connection  [D / build / P2]
    # ------------------------------------------------------------------

    @mcp.tool()
    def set_socket_connection(
        target_model_path: str,
        component_name: str,
        socket_name: str,
        connect_to: str,
        output_model_path: str,
    ) -> dict[str, Any]:
        """
        コンポーネントの Socket 接続先を変更して別名保存する。

        Args:
            target_model_path: 編集対象のモデルパス。
            component_name: Socket を持つコンポーネント名。
            socket_name: 変更する Socket 名（例: "parent_frame", "child_frame"）。
            connect_to: 新しい接続先のパス（例: "/bodyset/r_humerus"）。
            output_model_path: 変更後のモデルを保存するパス（.osim）。

        Note:
            - 元ファイルは変更しません。
            - 接続先が存在しない場合は initSystem() の時点でエラーになります。
        """
        import opensim  # noqa: PLC0415

        try:
            src = resolve_path(target_model_path)
        except ValueError as exc:
            return error_result("PATH_ERROR", str(exc))
        if not src.exists():
            return error_result("PATH_ERROR", f"モデルが見つかりません: {target_model_path}")

        try:
            dest = resolve_output_path(output_model_path, (src,))
        except ValueError as exc:
            return error_result("PATH_ERROR", str(exc))

        try:
            model = opensim.Model(str(src))
        except Exception as exc:
            return error_result("EXECUTION_ERROR", f"モデルロード失敗: {exc}")

        # コンポーネントを探す(名前でもフルパスでもよい。model.getComponent はパスしか受け付けない)
        try:
            comp = get_component_by_name(model, component_name)
        except AmbiguousComponentError as exc:
            return error_result("INPUT_ERROR", str(exc),
                                suggested_fix=f"フルパスで指定してください（例: '{exc.candidates[0][0]}'）")
        if comp is None:
            try:
                comp = model.getComponent(component_name)
            except Exception:
                comp = None
        if comp is None:
            return error_result(
                "INPUT_ERROR",
                f"'{component_name}' というコンポーネントが見つかりません",
                suggested_fix="list_components でコンポーネント名を確認してください",
            )

        # Socket を更新
        try:
            socket = comp.updSocket(socket_name)
            socket.setConnecteePath(connect_to)
        except Exception as exc:
            return error_result(
                "INPUT_ERROR",
                f"Socket '{socket_name}' の変更に失敗: {exc}",
                suggested_fix=(
                    f"get_component_properties('{component_name}') で "
                    "利用可能な Socket 名を確認してください"
                ),
            )

        # initSystem で接続先が有効か確認
        try:
            model.initSystem()
        except Exception as exc:
            return error_result(
                "EXECUTION_ERROR",
                f"接続先変更後の initSystem 失敗: {exc}",
                suggested_fix="connect_to のパスが正しいか確認してください",
            )

        try:
            dest.parent.mkdir(parents=True, exist_ok=True)
            model.printToXML(str(dest))
        except Exception as exc:
            return error_result("EXECUTION_ERROR", f"保存失敗: {exc}")

        return tool_result(
            status="completed",
            summary=f"{component_name}.{socket_name} → '{connect_to}' に接続変更",
            artifacts=[{"path": str(dest.relative_to(WORKSPACE_DIR)), "type": "model"}],
            structured_data={
                "output_model_path": str(dest.relative_to(WORKSPACE_DIR)),
                "connection_summary": (
                    f"{component_name}.{socket_name} connected to {connect_to}"
                ),
            },
        )

    # ------------------------------------------------------------------
    # 16) export_model  [C / build / P2]
    # ------------------------------------------------------------------

    @mcp.tool()
    def export_model(
        target_model_ref: str,
        output_path: str,
    ) -> dict[str, Any]:
        """
        編集済みモデルを最終成果物として workspace/exports/ に書き出す。

        Args:
            target_model_ref: エクスポート対象のモデルパス（ロード済みモデルのパス）。
            output_path: 書き出し先パス（workspace 内。例: "exports/final_model.osim"）。

        Note:
            - save_model_copy との違い: このツールは exports/ への出力を想定しており、
              最終成果物のアーカイブ用途に使います。
            - 元ファイルは変更しません。
        """
        import opensim  # noqa: PLC0415

        try:
            src = resolve_path(target_model_ref)
        except ValueError as exc:
            return error_result("PATH_ERROR", str(exc))
        if not src.exists():
            return error_result("PATH_ERROR", f"モデルが見つかりません: {target_model_ref}")

        try:
            dest = resolve_output_path(output_path, (src,))
        except ValueError as exc:
            return error_result("PATH_ERROR", str(exc))
        if dest.suffix.lower() != ".osim":
            return error_result("INPUT_ERROR", "output_path の拡張子は .osim にしてください")

        dest.parent.mkdir(parents=True, exist_ok=True)

        try:
            model = opensim.Model(str(src))
            model.initSystem()
            model.printToXML(str(dest))
        except Exception as exc:
            return error_result("EXECUTION_ERROR", f"エクスポート失敗: {exc}")

        size = dest.stat().st_size
        return tool_result(
            status="completed",
            summary=f"モデルを '{dest.name}' としてエクスポートしました",
            artifacts=[{"path": str(dest.relative_to(WORKSPACE_DIR)), "type": "model"}],
            structured_data={
                "saved_path": str(dest.relative_to(WORKSPACE_DIR)),
                "size_bytes": size,
            },
        )


# ---------------------------------------------------------------------------
# 内部ヘルパー
# ---------------------------------------------------------------------------


# component_kind のうち Analysis。OpenSim は Model の AnalysisSet を .osim に書き出さないので add_component では拒否する
_ANALYSIS_KINDS = frozenset({"JointReaction", "BodyKinematics", "PointKinematics", "ProbeReporter"})


def _create_component(opensim: Any, kind: str, spec: dict[str, Any], model: Any) -> tuple[Any, str]:
    """
    component_kind と spec から OpenSim コンポーネントを生成する。

    Returns
    -------
    (component, name)  component が None の場合は未サポート。spec の誤りは例外を送出する。
    """
    name = spec.get("name", f"{kind.lower()}_1")

    if kind == "CoordinateActuator":
        comp = opensim.CoordinateActuator()
        comp.setName(name)
        comp.set_coordinate(spec.get("coordinate", ""))
        comp.setOptimalForce(float(spec.get("optimal_force", 1.0)))
        comp.setMinControl(float(spec.get("min_control", -1.0)))
        comp.setMaxControl(float(spec.get("max_control", 1.0)))
        return comp, name

    elif kind == "PointActuator":
        comp = opensim.PointActuator()
        comp.setName(name)
        comp.set_body(spec["body"])
        comp.setOptimalForce(float(spec.get("optimal_force", 1.0)))
        return comp, name

    elif kind == "PathActuator":
        comp = opensim.PathActuator()
        comp.setName(name)
        comp.setOptimalForce(float(spec.get("optimal_force", 1.0)))
        # 経路の点 [{"body": "r_humerus", "location": [x, y, z]}, ...](2 点以上)
        points = spec.get("path_points") or []
        if len(points) < 2:
            raise ValueError('PathActuator には path_points を 2 点以上指定してください'
                             '(例: [{"body": "r_humerus", "location": [0, 0, 0]}, ...])')
        for i, pt in enumerate(points):
            frame = model.getBodySet().get(pt["body"])
            comp.addNewPathPoint(f"{name}-P{i + 1}", frame, opensim.Vec3(*[float(v) for v in pt["location"]]))
        return comp, name

    else:
        return None, name
