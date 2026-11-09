"""
モデル閲覧・編集系 Tools。

実装 Tools（仕様書 §10.1, §10.2 より Phase 1 対象）:
  1. load_model           [A / read_only / P0]
  2. get_model_summary    [A / read_only / P0]
  3. list_components      [A / read_only / P0]
  4. get_component_properties [A / read_only / P0]
  5. validate_model       [A / read_only / P0]
  9. clone_model          [C / build     / P0]
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from ..config import WORKSPACE_DIR
from ..model_service import (
    AmbiguousComponentError,
    clone_model as _clone_model,
    get_component_by_name,
    get_model,
    load_model as _load_model,
)
from ..schemas import error_result, tool_result
from ..workspace import check_extension, resolve_path

if TYPE_CHECKING:
    from mcp.server.fastmcp import FastMCP

logger = logging.getLogger(__name__)


def register_model_tools(mcp: "FastMCP") -> None:
    """モデル系 Tool を mcp インスタンスに登録する。"""

    # ------------------------------------------------------------------
    # 1) load_model
    # ------------------------------------------------------------------

    @mcp.tool()
    def load_model(model_path: str) -> dict[str, Any]:
        """
        .osim モデルをロードし、セッションの current model として扱う。

        Args:
            model_path: workspace 内の .osim ファイルパス（相対 or 絶対）。

        Returns:
            model_name / model_hash / component_counts / warnings
        """
        try:
            p = resolve_path(model_path)
            check_extension(p)
        except ValueError as exc:
            return error_result("PATH_ERROR", str(exc),
                                suggested_fix="workspace/models/original/ 内のパスを指定してください")

        if not p.exists():
            return error_result("PATH_ERROR", f"ファイルが見つかりません: {model_path}",
                                suggested_fix="workspace/models/original/ にファイルを配置してください")
        try:
            result = _load_model(p)
        except RuntimeError as exc:
            return error_result("MODEL_ERROR", str(exc))

        return tool_result(
            status="completed",
            summary=(
                f"モデル '{result['model_name']}' をロードしました"
                f" (hash: {result['model_hash']})"
            ),
            warnings=result["warnings"],
            structured_data={
                "model_name": result["model_name"],
                "model_path": str(p.relative_to(WORKSPACE_DIR)),
                "model_hash": result["model_hash"],
                "component_counts": result["component_counts"],
            },
        )

    # ------------------------------------------------------------------
    # 2) get_model_summary
    # ------------------------------------------------------------------

    @mcp.tool()
    def get_model_summary(model_ref: str | None = None) -> dict[str, Any]:
        """
        ロード済みモデルの概要を返す。

        Args:
            model_ref: モデルパス（省略時は current model）。
        """
        try:
            model = get_model(model_ref)
        except ValueError as exc:
            return error_result("MODEL_ERROR", str(exc), retryable=True,
                                suggested_fix="先に load_model を実行してください")

        try:
            gravity = model.getGravity()
            gvec = [gravity.get(0), gravity.get(1), gravity.get(2)]
        except Exception:
            gvec = [0.0, -9.80665, 0.0]

        nb = model.getBodySet().getSize()
        nj = model.getJointSet().getSize()
        nm = model.getMuscles().getSize()

        return tool_result(
            status="completed",
            summary=f"{model.getName()}: {nb} bodies, {nj} joints, {nm} muscles",
            structured_data={
                "model_name": model.getName(),
                "num_bodies": nb,
                "num_joints": nj,
                "num_coordinates": model.getCoordinateSet().getSize(),
                "num_muscles": nm,
                "num_forces": model.getForceSet().getSize(),
                "gravity": gvec,
                "notes": "",
            },
        )

    # ------------------------------------------------------------------
    # 3) list_components
    # ------------------------------------------------------------------

    _COMPONENT_TYPES = frozenset(
        {"bodies", "joints", "coordinates", "muscles", "forces",
         "markers", "controllers", "analyses", "probes"}
    )

    @mcp.tool()
    def list_components(component_type: str) -> dict[str, Any]:
        """
        指定カテゴリの component 一覧を返す。

        Args:
            component_type: bodies|joints|coordinates|muscles|forces|
                            markers|controllers|analyses|probes のいずれか。
        """
        if component_type not in _COMPONENT_TYPES:
            return error_result(
                "INPUT_ERROR",
                f"'{component_type}' は無効な component_type です。"
                f"許可値: {', '.join(sorted(_COMPONENT_TYPES))}",
            )

        try:
            model = get_model()
        except ValueError as exc:
            return error_result("MODEL_ERROR", str(exc), retryable=True,
                                suggested_fix="先に load_model を実行してください")

        _GETTERS: dict[str, Any] = {
            "bodies":      model.getBodySet,
            "joints":      model.getJointSet,
            "coordinates": model.getCoordinateSet,
            "muscles":     model.getMuscles,
            "forces":      model.getForceSet,
            "markers":     model.getMarkerSet,
            "controllers": model.getControllerSet,
            "analyses":    model.getAnalysisSet,
            "probes":      model.getProbeSet,
        }

        try:
            comp_set = _GETTERS[component_type]()
            items = [
                {
                    "name": comp_set.get(i).getName(),
                    "type": comp_set.get(i).getConcreteClassName(),
                }
                for i in range(comp_set.getSize())
            ]
        except Exception as exc:
            return error_result("MODEL_ERROR", f"component の取得に失敗しました: {exc}")

        return tool_result(
            status="completed",
            summary=f"{len(items)} 個の {component_type} が見つかりました",
            structured_data={
                "component_type": component_type,
                "count": len(items),
                "items": items,
            },
        )

    # ------------------------------------------------------------------
    # 4) get_component_properties
    # ------------------------------------------------------------------

    @mcp.tool()
    def get_component_properties(component_name: str) -> dict[str, Any]:
        """
        単一 component の property と output 情報を返す。

        Args:
            component_name: コンポーネント名（例: "femur_r", "hip_flexion_r"）。
        """
        try:
            model = get_model()
        except ValueError as exc:
            return error_result("MODEL_ERROR", str(exc), retryable=True,
                                suggested_fix="先に load_model を実行してください")

        try:
            component = get_component_by_name(model, component_name)
        except AmbiguousComponentError as exc:
            return error_result("INPUT_ERROR", str(exc),
                                suggested_fix=f"フルパスで指定してください（例: '{exc.candidates[0][0]}'）")
        if component is None:
            # Fallback: OpenSim の getComponent() を試みる
            try:
                component = model.getComponent(component_name)
            except Exception:
                pass

        if component is None:
            return error_result(
                "INPUT_ERROR",
                f"'{component_name}' というコンポーネントが見つかりません。"
                f"list_components で名前を確認してください。",
            )

        # properties
        properties: list[dict[str, str]] = []
        try:
            for i in range(component.getNumProperties()):
                prop = component.getPropertyByIndex(i)
                properties.append({
                    "name": prop.getName(),
                    "type": prop.getTypeName(),
                })
        except Exception as exc:
            logger.debug("Property 取得の一部でエラー: %s", exc)

        # outputs
        outputs: list[str] = []
        try:
            output_names = component.getOutputNames()
            outputs = [output_names[i] for i in range(len(output_names))]
        except Exception:
            pass

        return tool_result(
            status="completed",
            summary=f"'{component_name}': {len(properties)} properties, {len(outputs)} outputs",
            structured_data={
                "component_name": component_name,
                "component_type": component.getConcreteClassName(),
                "properties": properties,
                "outputs": outputs,
            },
        )

    # ------------------------------------------------------------------
    # 5) validate_model
    # ------------------------------------------------------------------

    @mcp.tool()
    def validate_model(model_ref: str | None = None) -> dict[str, Any]:
        """
        モデルの基本妥当性を確認する。

        Args:
            model_ref: モデルパス（省略時は current model）。
                       パスを指定した場合は新規にロードして確認する。
        """
        import opensim  # noqa: PLC0415

        errors: list[str] = []
        warnings: list[str] = []

        if model_ref:
            try:
                p = resolve_path(model_ref)
            except ValueError as exc:
                return error_result("PATH_ERROR", str(exc))
            try:
                model = opensim.Model(str(p))
                model.initSystem()
            except Exception as exc:
                errors.append(str(exc))
                return tool_result(
                    status="completed",
                    summary=f"モデルにエラーがあります ({len(errors)} 件)",
                    warnings=warnings,
                    structured_data={"is_valid": False, "errors": errors, "warnings": warnings},
                )
        else:
            try:
                model = get_model()
            except ValueError as exc:
                return error_result("MODEL_ERROR", str(exc), retryable=True,
                                    suggested_fix="先に load_model を実行してください")

        # 基本チェック
        try:
            if model.getBodySet().getSize() == 0:
                warnings.append("Body が 0 個です")
            if model.getJointSet().getSize() == 0:
                warnings.append("Joint が 0 個です")
        except Exception as exc:
            errors.append(f"チェック中にエラー: {exc}")

        is_valid = len(errors) == 0
        return tool_result(
            status="completed",
            summary="モデルは有効です" if is_valid else f"モデルに {len(errors)} 件のエラーがあります",
            warnings=warnings,
            structured_data={"is_valid": is_valid, "errors": errors, "warnings": warnings},
        )

    # ------------------------------------------------------------------
    # 9) clone_model
    # ------------------------------------------------------------------

    @mcp.tool()
    def clone_model(source_model_path: str, new_name: str) -> dict[str, Any]:
        """
        モデルを複製して workspace/models/clones/ に保存する。

        Args:
            source_model_path: 元モデルの workspace 内パス。
            new_name: 複製先のモデル名（拡張子なし、例: "gait2392_modified"）。

        Note:
            元ファイルは変更しません。構造編集は複製先に対して行ってください。
        """
        try:
            source = resolve_path(source_model_path)
        except ValueError as exc:
            return error_result("PATH_ERROR", str(exc))

        if not source.exists():
            return error_result(
                "PATH_ERROR",
                f"ファイルが見つかりません: {source_model_path}",
                suggested_fix="workspace/models/original/ 内のパスを指定してください",
            )

        clones_dir = WORKSPACE_DIR / "models" / "clones"
        try:
            dest = _clone_model(source, new_name, clones_dir)
        except ValueError as exc:
            return error_result("INPUT_ERROR", str(exc))
        except Exception as exc:
            return error_result("EXECUTION_ERROR", f"複製に失敗しました: {exc}")

        return tool_result(
            status="completed",
            summary=f"'{source.name}' → '{dest.name}' として複製しました",
            artifacts=[{"path": str(dest.relative_to(WORKSPACE_DIR)), "type": "model"}],
            structured_data={"cloned_model_path": str(dest.relative_to(WORKSPACE_DIR))},
        )
