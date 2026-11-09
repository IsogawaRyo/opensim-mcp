"""
編集・保存系 Tools（Phase 2 / v1.1）。

実装 Tools（仕様書 §10.1, §10.2 より）:
   7. list_workspace_files    [A / read_only   / P1]
  10. save_model_copy         [C / edit        / P1]
  11. write_setup_xml         [C / edit        / P1]
  12. convert_c3d_to_trc_mot  [C / edit        / P1]
  13. write_external_loads_xml [C / edit       / P1]
   8. set_component_property  [B/C / edit      / P1]
"""

from __future__ import annotations

import logging
import math
import os
import shutil
from pathlib import Path
from typing import TYPE_CHECKING, Any
from xml.sax.saxutils import escape

from ..config import ALLOWED_EXTENSIONS, WORKSPACE_DIR
from ..model_service import AmbiguousComponentError, get_component_by_name, get_model
from ..schemas import error_result, tool_result
from ..workspace import resolve_output_path, resolve_path

if TYPE_CHECKING:
    from mcp.server.fastmcp import FastMCP

logger = logging.getLogger(__name__)


def register_edit_tools(mcp: "FastMCP") -> None:
    """Phase 2 編集系 Tool を mcp インスタンスに登録する。"""

    # ------------------------------------------------------------------
    # 7) list_workspace_files
    # ------------------------------------------------------------------

    @mcp.tool()
    def list_workspace_files(
        subdir: str | None = None,
        extensions: list[str] | None = None,
    ) -> dict[str, Any]:
        """
        ワークスペース内のファイル一覧を返す。

        Args:
            subdir: サブディレクトリパス（例: "models/original"、省略時は全体）。
            extensions: 絞り込む拡張子リスト（例: [".osim", ".mot"]、省略時は全許可拡張子）。

        Returns:
            files リスト（path / type / size_bytes）。
        """
        search_root = WORKSPACE_DIR
        if subdir:
            try:
                search_root = resolve_path(subdir)
            except ValueError as exc:
                return error_result("PATH_ERROR", str(exc))
            if not search_root.is_dir():
                return error_result("PATH_ERROR", f"'{subdir}' はディレクトリではありません")

        allowed = (
            {ext.lower() if ext.startswith(".") else f".{ext.lower()}" for ext in extensions}
            if extensions
            else ALLOWED_EXTENSIONS
        )

        try:
            files = [
                {
                    "path": str(p.relative_to(WORKSPACE_DIR)),
                    "type": p.suffix.lstrip("."),
                    "size_bytes": p.stat().st_size,
                }
                for p in sorted(search_root.rglob("*"))
                if p.is_file() and p.suffix.lower() in allowed
            ]
        except Exception as exc:
            return error_result("EXECUTION_ERROR", f"一覧取得に失敗: {exc}")

        return tool_result(
            status="completed",
            summary=f"{len(files)} 個のファイルが見つかりました",
            structured_data={
                "search_root": str(search_root.relative_to(WORKSPACE_DIR)),
                "count": len(files),
                "files": files,
            },
        )

    # ------------------------------------------------------------------
    # 10) save_model_copy
    # ------------------------------------------------------------------

    @mcp.tool()
    def save_model_copy(
        target_model_ref: str | None = None,
        output_path: str = "models/clones/model_copy.osim",
    ) -> dict[str, Any]:
        """
        現在ロード済みのモデルを指定パスに別名保存する。

        Args:
            target_model_ref: 保存対象のモデルパス（省略時は current model）。
            output_path: 保存先の workspace 内パス（例: "models/clones/modified.osim"）。

        Note:
            元ファイルは変更しません。workspace/models/clones/ への保存を推奨します。
        """
        try:
            model = get_model(target_model_ref)
        except ValueError as exc:
            return error_result("MODEL_ERROR", str(exc), retryable=True,
                                suggested_fix="先に load_model を実行してください")

        try:
            dest = resolve_output_path(output_path, (model.getInputFileName(),))
        except ValueError as exc:
            return error_result("PATH_ERROR", str(exc))

        if dest.suffix.lower() != ".osim":
            return error_result("INPUT_ERROR", "output_path の拡張子は .osim にしてください")

        dest.parent.mkdir(parents=True, exist_ok=True)

        try:
            model.printToXML(str(dest))
        except Exception as exc:
            return error_result("EXECUTION_ERROR", f"モデル保存に失敗しました: {exc}")

        size = dest.stat().st_size if dest.exists() else 0
        return tool_result(
            status="completed",
            summary=f"モデルを '{dest.name}' として保存しました",
            artifacts=[{"path": str(dest.relative_to(WORKSPACE_DIR)), "type": "model"}],
            structured_data={"saved_path": str(dest.relative_to(WORKSPACE_DIR)), "size_bytes": size},
        )

    # ------------------------------------------------------------------
    # 11) write_setup_xml
    # ------------------------------------------------------------------

    _SUPPORTED_TOOL_TYPES = {"ik", "id", "analyze", "scale", "so"}

    @mcp.tool()
    def write_setup_xml(
        tool_type: str,
        params: dict[str, Any],
        output_path: str,
    ) -> dict[str, Any]:
        """
        OpenSim ツール用の setup XML を生成して保存する。

        Args:
            tool_type: "ik" | "id" | "analyze" | "scale" | "so" のいずれか。
            params: ツールごとのパラメータ辞書（下記参照）。
            output_path: 保存先 workspace 内パス（例: "setups/ik/my_ik.xml"）。

        IK params 例:
            {
              "model_file": "models/original/arm26.osim",
              "marker_file": "data/trc/markers.trc",
              "output_motion_file": "jobs/ik_result.mot",
              "time_range": [0.0, 2.0],
              "markers": [{"name": "LASI", "weight": 1.0}]
            }

        ID params 例:
            {
              "model_file": "models/original/gait.osim",
              "coordinates_file": "jobs/ik_result.mot",
              "external_loads_file": "setups/loads/walk_grf.xml",
              "output_gen_force_file": "jobs/id_result.sto",
              "time_range": [0.0, 2.0]
            }

        Analyze params 例:
            {
              "model_file": "models/original/gait.osim",
              "coordinates_file": "jobs/ik_result.mot",
              "external_loads_file": "setups/loads/walk_grf.xml",
              "results_directory": "jobs/analyze_results",
              "time_range": [0.0, 2.0],
              "analyses": ["BodyKinematics", "JointReaction"]
            }

        SO params 例:
            {
              "model_file": "models/original/gait.osim",
              "coordinates_file": "jobs/ik_result.mot",
              "external_loads_file": "setups/loads/walk_grf.xml",
              "results_directory": "jobs/so_results",
              "time_range": [0.0, 2.0],
              "activation_exponent": 2,
              "use_muscle_physiology": true
            }

        Scale params 例:
            {
              "model_file": "models/original/generic.osim",
              "marker_file": "data/trc/static.trc",
              "subject_mass": 75.0,
              "subject_name": "subject01",
              "marker_set_file": "setups/scale/markers.xml",  # 任意
              "output_model_file": "models/clones/scaled.osim",
              "time_range": [0.0, 1.0]
            }
        """
        if tool_type not in _SUPPORTED_TOOL_TYPES:
            return error_result(
                "INPUT_ERROR",
                f"tool_type '{tool_type}' は未対応です。対応: {', '.join(sorted(_SUPPORTED_TOOL_TYPES))}",
            )

        try:
            dest = resolve_output_path(output_path)
        except ValueError as exc:
            return error_result("PATH_ERROR", str(exc))

        dest.parent.mkdir(parents=True, exist_ok=True)

        try:
            if tool_type == "ik":
                xml_content, schema_summary = _build_ik_xml(params)
            elif tool_type == "id":
                xml_content, schema_summary = _build_id_xml(params)
            elif tool_type == "so":
                xml_content, schema_summary = _build_so_xml(params)
            elif tool_type == "scale":
                xml_content, schema_summary = _build_scale_xml(params, dest.parent)
            else:
                xml_content, schema_summary = _build_analyze_xml(params)
        except KeyError as exc:
            return error_result(
                "INPUT_ERROR",
                f"params に必須キーが不足しています: {exc}",
                suggested_fix="tool_type に応じた params を確認してください",
            )
        except ValueError as exc:
            return error_result("PATH_ERROR", str(exc))
        except Exception as exc:
            return error_result("EXECUTION_ERROR", f"XML 生成に失敗: {exc}")

        dest.write_text(xml_content, encoding="utf-8")

        return tool_result(
            status="completed",
            summary=f"{tool_type.upper()} setup XML を '{dest.name}' に保存しました",
            artifacts=[{"path": str(dest.relative_to(WORKSPACE_DIR)), "type": "xml"}],
            structured_data={
                "saved_path": str(dest.relative_to(WORKSPACE_DIR)),
                "tool_type": tool_type,
                "schema_summary": schema_summary,
            },
        )

    # ------------------------------------------------------------------
    # 13) write_external_loads_xml
    # ------------------------------------------------------------------

    @mcp.tool()
    def write_external_loads_xml(
        params: dict[str, Any],
        output_path: str,
    ) -> dict[str, Any]:
        """
        ExternalLoads XML を生成して保存する。

        Args:
            params: ExternalLoads のパラメータ辞書。
            output_path: 保存先 workspace 内パス（例: "setups/loads/walk_grf.xml"）。

        params 例:
            {
              "name": "walk_grf",
              "datafile": "data/mot/walk_grf.mot",
              "external_loads_model_kinematics_file": "jobs/ik_result.mot",
              "lowpass_cutoff_frequency_for_load_kinematics": 6.0,
              "external_loads": [
                {
                  "name": "Right_GRF",
                  "applied_to_body": "calcn_r",
                  "force_expressed_in_body": "ground",
                  "point_expressed_in_body": "ground",
                  "force_identifier": "ground_force_r_v",
                  "point_identifier": "ground_force_r_p",
                  "torque_identifier": "ground_torque_r_"
                }
              ]
            }
        """
        try:
            dest = resolve_output_path(output_path)
        except ValueError as exc:
            return error_result("PATH_ERROR", str(exc))

        dest.parent.mkdir(parents=True, exist_ok=True)

        try:
            xml_content, schema_summary = _build_external_loads_xml(params)
        except KeyError as exc:
            return error_result(
                "INPUT_ERROR",
                f"params に必須キーが不足しています: {exc}",
                suggested_fix="datafile と external_loads の各必須キーを確認してください",
            )
        except ValueError as exc:
            return error_result("PATH_ERROR", str(exc))
        except Exception as exc:
            return error_result("EXECUTION_ERROR", f"XML 生成に失敗: {exc}")

        dest.write_text(xml_content, encoding="utf-8")

        return tool_result(
            status="completed",
            summary=f"ExternalLoads XML を '{dest.name}' に保存しました",
            artifacts=[{"path": str(dest.relative_to(WORKSPACE_DIR)), "type": "xml"}],
            structured_data={
                "saved_path": str(dest.relative_to(WORKSPACE_DIR)),
                "schema_summary": schema_summary,
            },
        )

    # ------------------------------------------------------------------
    # 12) convert_c3d_to_trc_mot
    # ------------------------------------------------------------------

    @mcp.tool()
    def convert_c3d_to_trc_mot(
        c3d_path: str,
        output_dir: str = "data",
        rotate_data: bool = False,
        export_force_data: bool = True,
    ) -> dict[str, Any]:
        """
        C3D ファイルを OpenSim 用 TRC / MOT ファイルに変換する。

        Args:
            c3d_path: workspace 内の .c3d ファイルパス。
            output_dir: TRC/MOT の出力先ディレクトリ（workspace 内相対パス）。
            rotate_data: True にすると実験室の Z-up を OpenSim の Y-up に変換する（デフォルト False）。
                マーカーと力データの両方に同じ回転をかける。
            export_force_data: True にすると力データ (.mot) も生成する（デフォルト True）。

        Returns:
            生成された TRC / MOT ファイルパスの一覧。
        """
        import opensim  # noqa: PLC0415

        try:
            src = resolve_path(c3d_path)
        except ValueError as exc:
            return error_result("PATH_ERROR", str(exc))

        if not src.exists():
            return error_result("PATH_ERROR", f"C3D ファイルが見つかりません: {c3d_path}")
        if src.suffix.lower() != ".c3d":
            return error_result("INPUT_ERROR", "c3d_path の拡張子は .c3d にしてください")

        try:
            out_dir = resolve_output_path(output_dir)
        except ValueError as exc:
            return error_result("PATH_ERROR", str(exc))
        out_dir.mkdir(parents=True, exist_ok=True)

        generated: list[dict[str, Any]] = []
        stem = src.stem
        warnings: list[str] = []

        try:
            adapter = opensim.C3DFileAdapter()
            tables = adapter.read(str(src))
        except Exception as exc:
            return error_result("EXECUTION_ERROR", f"C3D 読込失敗: {exc}")

        # tables[...] は AbstractDataTable なので、型付きテーブル(TimeSeriesTableVec3)は getter で取り出す
        # rotate_data: X 軸まわりに -90°。実験室の Z-up を OpenSim の Y-up に直す ((x, y, z) → (x, z, -y))
        rotation = opensim.Rotation(-math.pi / 2, opensim.CoordinateAxis(0)) if rotate_data else None

        # --- TRC (マーカーデータ) ---
        try:
            marker_table = adapter.getMarkersTable(tables)
            if marker_table.getNumColumns() == 0:
                raise ValueError("マーカーデータがありません")

            if rotation is not None:
                _rotate_vec3_table(marker_table, rotation)

            trc_path = out_dir / f"{stem}_markers.trc"
            trc_adapter = opensim.TRCFileAdapter()
            trc_adapter.write(marker_table, str(trc_path))
            generated.append({
                "path": str(trc_path.relative_to(WORKSPACE_DIR)),
                "type": "trc",
                "size_bytes": trc_path.stat().st_size,
            })
        except Exception as exc:
            warnings.append(f"マーカーデータの変換に失敗: {exc}")

        # --- MOT (力データ) ---
        force_table = adapter.getForcesTable(tables) if export_force_data else None
        if force_table is not None and force_table.getNumColumns() > 0:
            try:
                mot_path = out_dir / f"{stem}_forces.mot"

                # 力・作用点・モーメントはすべてベクトルなので、マーカーと同じ回転をかける
                if rotation is not None:
                    _rotate_vec3_table(force_table, rotation)
                # STO は Vec3 列を書けないので、_1/_2/_3 のスカラー列に展開する
                sto_adapter = opensim.STOFileAdapter()
                sto_adapter.write(force_table.flatten(), str(mot_path))
                generated.append({
                    "path": str(mot_path.relative_to(WORKSPACE_DIR)),
                    "type": "mot",
                    "size_bytes": mot_path.stat().st_size,
                })
            except Exception as exc:
                warnings.append(f"力データの変換に失敗: {exc}")
        elif export_force_data:
            warnings.append("C3D ファイルに力データが含まれていません")

        if not generated:
            return error_result("EXECUTION_ERROR", "変換ファイルが生成されませんでした",
                                suggested_fix="C3D ファイルにマーカーデータが含まれているか確認してください")

        return tool_result(
            status="completed",
            summary=f"{src.name} → {len(generated)} ファイル生成",
            warnings=warnings,
            artifacts=generated,
            structured_data={
                "source": str(src.relative_to(WORKSPACE_DIR)),
                "generated_files": generated,
                "data_summary": {
                    "has_markers": any(g["type"] == "trc" for g in generated),
                    "has_forces":  any(g["type"] == "mot" for g in generated),
                },
            },
        )

    # ------------------------------------------------------------------
    # 8) set_component_property
    # ------------------------------------------------------------------

    @mcp.tool()
    def set_component_property(
        component_name: str,
        property_name: str,
        value: Any,
        save_mode: str = "copy",
        target_model_path: str | None = None,
        output_path: str | None = None,
    ) -> dict[str, Any]:
        """
        モデルコンポーネントの property 値を変更する。

        Args:
            component_name: コンポーネント名（例: "r_humerus", "TRIlong"）。
            property_name: プロパティ名または共通プロパティ（例: "mass", "max_isometric_force"）。
            value: 設定する値（数値・文字列・リスト）。
            save_mode: "temp"（メモリ内のみ）| "copy"（複製に保存、デフォルト）。
            target_model_path: 対象モデルパス（省略時は current model）。
            output_path: save_mode="copy" 時の保存先（省略時は clones/ に自動生成）。

        Returns:
            changed / output_model_path / diff_summary

        Note:
            元ファイルは変更しません。save_mode="copy" では複製先に保存します。
        """
        if save_mode not in ("temp", "copy"):
            return error_result("INPUT_ERROR", "save_mode は 'temp' または 'copy' を指定してください")

        try:
            model = get_model(target_model_path)
        except ValueError as exc:
            return error_result("MODEL_ERROR", str(exc), retryable=True,
                                suggested_fix="先に load_model を実行してください")

        # コンポーネントを取得
        try:
            component = get_component_by_name(model, component_name)
        except AmbiguousComponentError as exc:
            return error_result("INPUT_ERROR", str(exc),
                                suggested_fix=f"フルパスで指定してください（例: '{exc.candidates[0][0]}'）")
        if component is None:
            try:
                component = model.getComponent(component_name)
            except Exception:
                pass
        if component is None:
            return error_result("INPUT_ERROR",
                                f"'{component_name}' というコンポーネントが見つかりません",
                                suggested_fix="list_components で名前を確認してください")

        # プロパティを変更
        changed, old_value, new_value = _apply_property(component, property_name, value)

        if not changed:
            return error_result(
                "INPUT_ERROR",
                f"'{property_name}' の変更に失敗しました",
                suggested_fix=(
                    f"get_component_properties('{component_name}') で利用可能なプロパティを確認してください。"
                    "よく使うプロパティ: mass (Body), max_isometric_force (Muscle)"
                ),
            )

        diff_summary = f"{component_name}.{property_name}: {old_value!r} → {new_value!r}"
        logger.info("Property changed: %s", diff_summary)

        if save_mode == "temp":
            return tool_result(
                status="completed",
                summary=f"[temp] {diff_summary}",
                structured_data={
                    "changed": True,
                    "output_model_path": None,
                    "diff_summary": diff_summary,
                    "save_mode": "temp",
                },
            )

        # copy: モデルを別ファイルに保存
        if output_path:
            try:
                dest = resolve_output_path(output_path, (model.getInputFileName(),))
            except ValueError as exc:
                return error_result("PATH_ERROR", str(exc))
        else:
            clones_dir = WORKSPACE_DIR / "models" / "clones"
            clones_dir.mkdir(parents=True, exist_ok=True)
            dest_name = f"{model.getName()}_edited.osim"
            dest = clones_dir / dest_name

        dest.parent.mkdir(parents=True, exist_ok=True)
        try:
            model.printToXML(str(dest))
        except Exception as exc:
            return error_result("EXECUTION_ERROR", f"保存に失敗: {exc}")

        return tool_result(
            status="completed",
            summary=f"[copy] {diff_summary}",
            artifacts=[{"path": str(dest.relative_to(WORKSPACE_DIR)), "type": "model"}],
            structured_data={
                "changed": True,
                "output_model_path": str(dest.relative_to(WORKSPACE_DIR)),
                "diff_summary": diff_summary,
                "save_mode": "copy",
            },
        )


# ---------------------------------------------------------------------------
# 内部ヘルパー
# ---------------------------------------------------------------------------


def _apply_property(component: Any, property_name: str, value: Any) -> tuple[bool, Any, Any]:
    """
    コンポーネントのプロパティを変更する。

    Returns
    -------
    (changed, old_value, new_value)
    """
    prop_name = property_name.lower().replace("-", "_")

    # --- 1. 既知のセッターメソッドパターン ---
    _SETTER_MAP: dict[str, tuple[str, str]] = {
        # property_name -> (getter_method, setter_method)
        "mass":                 ("getMass",               "setMass"),
        "max_isometric_force":  ("getMaxIsometricForce",  "setMaxIsometricForce"),
        "optimal_fiber_length": ("getOptimalFiberLength",  "setOptimalFiberLength"),
        "tendon_slack_length":  ("getTendonSlackLength",   "setTendonSlackLength"),
        "pennation_angle_at_optimal": ("getPennationAngleAtOptimal", "setPennationAngleAtOptimal"),
    }

    if prop_name in _SETTER_MAP:
        getter_name, setter_name = _SETTER_MAP[prop_name]
        getter = getattr(component, getter_name, None)
        setter = getattr(component, setter_name, None)
        if getter and setter:
            try:
                old = getter()
                setter(float(value))
                new = getter()
                return True, old, new
            except Exception:
                pass

    # --- 2. GenericProperty interface ---
    # OpenSim 4.x の Python バインディングには PropertyDouble 等の型付きクラスが無いため、
    # PropertyHelper.setValue*(value, prop, index) で書き込む。
    # range のようなリスト property（要素数 2 など）は、同じ要素数のリストで指定する。
    try:
        import opensim  # noqa: PLC0415

        prop = component.updPropertyByName(property_name)
        type_name = prop.getTypeName()
        old_str = prop.toString()

        setters = {
            "double": (opensim.PropertyHelper.setValueDouble, float),
            "int":    (opensim.PropertyHelper.setValueInt, int),
            "bool":   (opensim.PropertyHelper.setValueBool, _coerce_bool),
            "string": (opensim.PropertyHelper.setValueString, str),
        }
        if type_name in setters:
            setter, cast = setters[type_name]
            values = list(value) if isinstance(value, (list, tuple)) else [value]
            if len(values) != max(prop.size(), 1):
                logger.warning("%s は %d 要素の property ですが、%d 個の値が指定されました",
                               property_name, prop.size(), len(values))
                return False, None, None
            for i, v in enumerate(values):
                setter(cast(v), prop, i)
            return True, old_str, prop.toString()
    except Exception as exc:
        logger.warning("property '%s' の変更に失敗: %s", property_name, exc)

    return False, None, None


def _resolve_param_path(val: str) -> str:
    """params 内のパス文字列を絶対パスに解決する。"""
    if not val:
        return val
    return str(resolve_path(val))


def _rotate_vec3_table(table: Any, rotation: Any) -> None:
    """TimeSeriesTableVec3 の全要素に回転をかける（欠損の NaN は NaN のまま）。"""
    for i in range(table.getNumRows()):
        row = table.updRowAtIndex(i)
        for j in range(row.size()):
            row[j] = rotation.multiply(row[j])


def _coerce_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "on"}
    return bool(value)


def _xml_bool(value: Any) -> str:
    return "true" if _coerce_bool(value) else "false"


def _xml_text(value: Any) -> str:
    return escape(str(value), {'"': "&quot;"})


def _build_analysis_object_xml(analysis: str | dict[str, Any]) -> str:
    if isinstance(analysis, str):
        analysis_type = analysis
        analysis_name = analysis
        properties: dict[str, Any] = {"on": True}
    else:
        analysis_type = str(analysis["type"])
        analysis_name = str(analysis.get("name", analysis_type))
        properties = {k: v for k, v in analysis.items() if k not in {"type", "name"}}
        properties.setdefault("on", True)

    property_lines = []
    for key, value in properties.items():
        text = _xml_bool(value) if isinstance(value, bool) else _xml_text(value)
        property_lines.append(f"\t\t\t\t<{key}>{text}</{key}>")

    return (
        f'\t\t\t<{analysis_type} name="{_xml_text(analysis_name)}">\n'
        + "\n".join(property_lines)
        + f"\n\t\t\t</{analysis_type}>"
    )


def _build_ik_xml(params: dict[str, Any]) -> tuple[str, dict]:
    """IK setup XML を生成する。"""
    model_file = _resolve_param_path(params["model_file"])
    marker_file = _resolve_param_path(params["marker_file"])
    output_mot  = _resolve_param_path(params.get("output_motion_file", ""))
    time_range  = params.get("time_range", [0.0, 1.0])
    markers     = params.get("markers", [])

    marker_tasks = "\n".join(
        f"""            <IKMarkerTask name="{m['name']}">
                <apply>true</apply>
                <weight>{m.get('weight', 1.0)}</weight>
            </IKMarkerTask>"""
        for m in markers
    )
    if not marker_tasks:
        marker_tasks = "            <!-- マーカータスクを追加してください -->"

    xml = f"""<?xml version="1.0" encoding="UTF-8"?>
<OpenSimDocument Version="40600">
\t<InverseKinematicsTool name="ik_tool">
\t\t<model_file>{model_file}</model_file>
\t\t<time_range>{time_range[0]} {time_range[1]}</time_range>
\t\t<output_motion_file>{output_mot}</output_motion_file>
\t\t<IKTaskSet>
\t\t\t<objects>
{marker_tasks}
\t\t\t</objects>
\t\t\t<groups />
\t\t</IKTaskSet>
\t\t<marker_file>{marker_file}</marker_file>
\t</InverseKinematicsTool>
</OpenSimDocument>
"""
    schema = {
        "required_keys": ["model_file", "marker_file"],
        "optional_keys": ["output_motion_file", "time_range", "markers"],
    }
    return xml, schema


def _build_id_xml(params: dict[str, Any]) -> tuple[str, dict]:
    """ID setup XML を生成する。"""
    tool_name = params.get("tool_name", "id_tool")
    model_file = _resolve_param_path(params["model_file"])
    coords_file = _resolve_param_path(params["coordinates_file"])
    output_force = _resolve_param_path(params.get("output_gen_force_file", ""))
    output_body = _resolve_param_path(params.get("output_body_forces_file", ""))
    ext_loads_file = _resolve_param_path(params.get("external_loads_file", ""))
    time_range = params.get("time_range", [0.0, 1.0])
    filter_freq = params.get("lowpass_cutoff_frequency_for_coordinates", -1)
    results_dir = _resolve_param_path(params.get("results_directory", ""))
    excluded_forces = params.get("forces_to_exclude", [])
    joints_to_report = params.get("joints_to_report_body_forces", [])
    output_force_path = Path(output_force) if output_force else None
    output_body_path = Path(output_body) if output_body else None

    if not results_dir:
        derived_results_dir = None
        if output_force_path and str(output_force_path.parent) not in {"", "."}:
            derived_results_dir = output_force_path.parent
        elif output_body_path and str(output_body_path.parent) not in {"", "."}:
            derived_results_dir = output_body_path.parent
        if derived_results_dir is not None:
            results_dir = str(derived_results_dir)

    output_force_name = output_force_path.name if output_force_path else ""
    output_body_name = output_body_path.name if output_body_path else ""

    ext_loads_tag = (
        f"\t\t<external_loads_file>{_xml_text(ext_loads_file)}</external_loads_file>"
        if ext_loads_file
        else "\t\t<external_loads_file />"
    )
    output_force_tag = (
        f"\t\t<output_gen_force_file>{_xml_text(output_force_name)}</output_gen_force_file>"
        if output_force_name
        else "\t\t<output_gen_force_file />"
    )
    output_body_tag = (
        f"\t\t<output_body_forces_file>{_xml_text(output_body_name)}</output_body_forces_file>"
        if output_body_name
        else "\t\t<output_body_forces_file />"
    )
    results_dir_tag = (
        f"\t\t<results_directory>{_xml_text(results_dir)}</results_directory>"
        if results_dir
        else "\t\t<results_directory />"
    )
    excluded_forces_text = " ".join(str(force_name) for force_name in excluded_forces)
    excluded_forces_tag = (
        f"\t\t<forces_to_exclude>{_xml_text(excluded_forces_text)}</forces_to_exclude>"
        if excluded_forces_text
        else "\t\t<forces_to_exclude />"
    )
    joints_to_report_text = " ".join(str(joint_name) for joint_name in joints_to_report)
    joints_to_report_tag = (
        f"\t\t<joints_to_report_body_forces>{_xml_text(joints_to_report_text)}</joints_to_report_body_forces>"
        if joints_to_report_text
        else "\t\t<joints_to_report_body_forces />"
    )

    xml = f"""<?xml version="1.0" encoding="UTF-8"?>
<OpenSimDocument Version="40600">
\t<InverseDynamicsTool name="{_xml_text(tool_name)}">
{results_dir_tag}
\t\t<input_directory />
\t\t<model_file>{_xml_text(model_file)}</model_file>
\t\t<time_range>{time_range[0]} {time_range[1]}</time_range>
\t\t{excluded_forces_tag}
\t\t<coordinates_file>{_xml_text(coords_file)}</coordinates_file>
\t\t<lowpass_cutoff_frequency_for_coordinates>{filter_freq}</lowpass_cutoff_frequency_for_coordinates>
{ext_loads_tag}
\t\t{output_force_tag}
\t\t{joints_to_report_tag}
{output_body_tag}
\t</InverseDynamicsTool>
</OpenSimDocument>
"""
    schema = {
        "required_keys": ["model_file", "coordinates_file"],
        "optional_keys": ["output_gen_force_file", "output_body_forces_file",
                          "external_loads_file", "time_range", "tool_name",
                          "results_directory", "forces_to_exclude",
                          "joints_to_report_body_forces",
                          "lowpass_cutoff_frequency_for_coordinates"],
    }
    return xml, schema


def _build_analyze_xml(params: dict[str, Any]) -> tuple[str, dict]:
    """Analyze setup XML を生成する。"""
    tool_name = params.get("tool_name", "analyze_tool")
    model_file = _resolve_param_path(params["model_file"])
    coords_file = _resolve_param_path(params["coordinates_file"])
    results_dir = _resolve_param_path(params.get("results_directory", ""))
    states_file = _resolve_param_path(params.get("states_file", ""))
    speeds_file = _resolve_param_path(params.get("speeds_file", ""))
    ext_loads_file = _resolve_param_path(params.get("external_loads_file", ""))
    time_range = params.get("time_range", [0.0, 1.0])
    analyses = params.get("analyses", [])
    force_set_files = params.get("force_set_files", [])
    replace_force_set = params.get("replace_force_set", False)
    solve_aux = params.get("solve_for_equilibrium_for_auxiliary_states", False)
    max_steps = params.get("maximum_number_of_integrator_steps", 20000)
    max_step_size = params.get("maximum_integrator_step_size", 1)
    min_step_size = params.get("minimum_integrator_step_size", 1e-08)
    tolerance = params.get("integrator_error_tolerance", 1e-05)
    filter_freq = params.get("lowpass_cutoff_frequency_for_coordinates", -1)

    analyses_xml = "\n".join(_build_analysis_object_xml(a) for a in analyses)
    if not analyses_xml:
        analyses_xml = "\t\t\t<!-- 解析タイプを追加してください (例: BodyKinematics, JointReaction) -->"

    force_set_files_xml = "\n".join(
        f"\t\t\t<string>{_xml_text(_resolve_param_path(force_set_file))}</string>"
        for force_set_file in force_set_files
    )
    force_set_block = (
        "\t\t<force_set_files>\n"
        "\t\t\t<objects>\n"
        f"{force_set_files_xml}\n"
        "\t\t\t</objects>\n"
        "\t\t\t<groups />\n"
        "\t\t</force_set_files>"
        if force_set_files_xml
        else "\t\t<force_set_files />"
    )
    ext_loads_tag = (
        f"\t\t<external_loads_file>{_xml_text(ext_loads_file)}</external_loads_file>"
        if ext_loads_file
        else "\t\t<external_loads_file />"
    )
    states_tag = f"\t\t<states_file>{_xml_text(states_file)}</states_file>" if states_file else "\t\t<states_file />"
    speeds_tag = f"\t\t<speeds_file>{_xml_text(speeds_file)}</speeds_file>" if speeds_file else "\t\t<speeds_file />"

    xml = f"""<?xml version="1.0" encoding="UTF-8"?>
<OpenSimDocument Version="40600">
\t<AnalyzeTool name="{_xml_text(tool_name)}">
\t\t<model_file>{_xml_text(model_file)}</model_file>
\t\t<replace_force_set>{_xml_bool(replace_force_set)}</replace_force_set>
{force_set_block}
\t\t<results_directory>{_xml_text(results_dir)}</results_directory>
\t\t<output_precision>{params.get("output_precision", 8)}</output_precision>
\t\t<initial_time>{time_range[0]}</initial_time>
\t\t<final_time>{time_range[1]}</final_time>
\t\t<solve_for_equilibrium_for_auxiliary_states>{_xml_bool(solve_aux)}</solve_for_equilibrium_for_auxiliary_states>
\t\t<maximum_number_of_integrator_steps>{max_steps}</maximum_number_of_integrator_steps>
\t\t<maximum_integrator_step_size>{max_step_size}</maximum_integrator_step_size>
\t\t<minimum_integrator_step_size>{min_step_size}</minimum_integrator_step_size>
\t\t<integrator_error_tolerance>{tolerance}</integrator_error_tolerance>
\t\t<AnalysisSet name="Analyses">
\t\t\t<objects>
{analyses_xml}
\t\t\t</objects>
\t\t\t<groups />
\t\t</AnalysisSet>
\t\t<ControllerSet name="Controllers">
\t\t\t<objects />
\t\t\t<groups />
\t\t</ControllerSet>
{ext_loads_tag}
{states_tag}
\t\t<coordinates_file>{_xml_text(coords_file)}</coordinates_file>
{speeds_tag}
\t\t<lowpass_cutoff_frequency_for_coordinates>{filter_freq}</lowpass_cutoff_frequency_for_coordinates>
\t</AnalyzeTool>
</OpenSimDocument>
"""
    schema = {
        "required_keys": ["model_file", "coordinates_file"],
        "optional_keys": [
            "tool_name", "results_directory", "time_range", "analyses",
            "states_file", "speeds_file", "external_loads_file",
            "force_set_files", "replace_force_set", "output_precision",
            "solve_for_equilibrium_for_auxiliary_states",
            "maximum_number_of_integrator_steps", "maximum_integrator_step_size",
            "minimum_integrator_step_size", "integrator_error_tolerance",
            "lowpass_cutoff_frequency_for_coordinates",
        ],
    }
    return xml, schema


def _build_so_xml(params: dict[str, Any]) -> tuple[str, dict]:
    """Static Optimization 用 Analyze setup XML を生成する。"""
    time_range = params.get("time_range", [0.0, 1.0])
    analysis_name = params.get("analysis_name", "StaticOptimization")
    so_analysis = {
        "type": "StaticOptimization",
        "name": analysis_name,
        "on": params.get("on", True),
        "start_time": params.get("start_time", time_range[0]),
        "end_time": params.get("end_time", time_range[1]),
        "step_interval": params.get("step_interval", 1),
        "in_degrees": params.get("in_degrees", True),
        "use_model_force_set": params.get("use_model_force_set", True),
        "activation_exponent": params.get("activation_exponent", 2),
        "use_muscle_physiology": params.get("use_muscle_physiology", True),
        "optimizer_convergence_criterion": params.get("optimizer_convergence_criterion", 1e-4),
        "optimizer_max_iterations": params.get("optimizer_max_iterations", 100),
    }
    analyze_params = dict(params)
    analyze_params["analyses"] = [so_analysis]
    analyze_params.setdefault("tool_name", params.get("tool_name", "so_tool"))
    return _build_analyze_xml(analyze_params)


def _build_external_loads_xml(params: dict[str, Any]) -> tuple[str, dict]:
    """ExternalLoads XML を生成する。"""
    loads_name = params.get("name", "external_loads")
    datafile = _resolve_param_path(params["datafile"])
    model_kinematics = _resolve_param_path(params.get("external_loads_model_kinematics_file", ""))
    filter_freq = params.get("lowpass_cutoff_frequency_for_load_kinematics", -1)
    external_loads = params["external_loads"]

    if not isinstance(external_loads, list) or not external_loads:
        raise KeyError("external_loads")

    load_objects: list[str] = []
    for load in external_loads:
        load_name = load["name"]
        load_lines = [
            f'\t\t\t<ExternalForce name="{_xml_text(load_name)}">',
            f"\t\t\t\t<isDisabled>{_xml_bool(load.get('is_disabled', False))}</isDisabled>",
            f"\t\t\t\t<applied_to_body>{_xml_text(load['applied_to_body'])}</applied_to_body>",
            f"\t\t\t\t<force_expressed_in_body>{_xml_text(load.get('force_expressed_in_body', 'ground'))}</force_expressed_in_body>",
            f"\t\t\t\t<point_expressed_in_body>{_xml_text(load.get('point_expressed_in_body', 'ground'))}</point_expressed_in_body>",
            f"\t\t\t\t<force_identifier>{_xml_text(load['force_identifier'])}</force_identifier>",
            f"\t\t\t\t<point_identifier>{_xml_text(load['point_identifier'])}</point_identifier>",
        ]
        torque_identifier = load.get("torque_identifier", "")
        if torque_identifier:
            load_lines.append(
                f"\t\t\t\t<torque_identifier>{_xml_text(torque_identifier)}</torque_identifier>"
            )
        data_source_name = load.get("data_source_name", "")
        if data_source_name:
            load_lines.append(
                f"\t\t\t\t<data_source_name>{_xml_text(data_source_name)}</data_source_name>"
            )
        load_lines.append("\t\t\t</ExternalForce>")
        load_objects.append("\n".join(load_lines))

    model_kinematics_tag = (
        f"\t\t<external_loads_model_kinematics_file>{_xml_text(model_kinematics)}</external_loads_model_kinematics_file>"
        if model_kinematics
        else "\t\t<external_loads_model_kinematics_file />"
    )
    filter_tag = (
        f"\t\t<lowpass_cutoff_frequency_for_load_kinematics>{filter_freq}</lowpass_cutoff_frequency_for_load_kinematics>"
        if model_kinematics
        else ""
    )

    xml = f"""<?xml version="1.0" encoding="UTF-8"?>
<OpenSimDocument Version="40600">
\t<ExternalLoads name="{_xml_text(loads_name)}">
\t\t<objects>
{chr(10).join(load_objects)}
\t\t</objects>
\t\t<groups />
\t\t<datafile>{_xml_text(datafile)}</datafile>
{model_kinematics_tag}
{filter_tag}
\t</ExternalLoads>
</OpenSimDocument>
"""
    schema = {
        "required_keys": ["datafile", "external_loads"],
        "optional_keys": [
            "name", "external_loads_model_kinematics_file",
            "lowpass_cutoff_frequency_for_load_kinematics",
        ],
        "external_load_required_keys": [
            "name", "applied_to_body", "force_identifier", "point_identifier",
        ],
        "external_load_optional_keys": [
            "force_expressed_in_body", "point_expressed_in_body",
            "torque_identifier", "data_source_name", "is_disabled",
        ],
    }
    return xml, schema


def _build_scale_xml(params: dict[str, Any], setup_dir: Path) -> tuple[str, dict]:
    """
    Scale setup XML を生成する。

    ScaleTool は setup 内のパスの前に setup ファイルのフォルダを必ず付ける(絶対パスでも
    "setups/scale//abs/..." のように連結して開けない)。そのためパスは setup_dir からの相対で書く。
    """
    model_file        = _resolve_param_path(params["model_file"])
    marker_file       = _resolve_param_path(params["marker_file"])
    marker_set_file   = _resolve_param_path(params.get("marker_set_file", ""))
    subject_mass      = float(params.get("subject_mass", 75.0))
    subject_name      = params.get("subject_name", "subject01")
    time_range        = params.get("time_range", [0.0, 1.0])
    # 出力モデルは元データを上書きしないよう検証する。省略時は models/clones/<subject_name>_scaled.osim
    output_model_file = str(resolve_output_path(
        params.get("output_model_file") or f"models/clones/{subject_name}_scaled.osim",
        (model_file,),
    ))

    def _rel(path: str) -> str:
        return os.path.relpath(path, setup_dir) if path else path

    model_file, marker_file, marker_set_file, output_model_file = (
        _rel(model_file), _rel(marker_file), _rel(marker_set_file), _rel(output_model_file))

    xml = f"""<?xml version="1.0" encoding="UTF-8"?>
<OpenSimDocument Version="40600">
\t<ScaleTool name="{subject_name}">
\t\t<mass>{subject_mass}</mass>
\t\t<GenericModelMaker>
\t\t\t<model_file>{model_file}</model_file>
\t\t\t<marker_set_file>{marker_set_file or "Unassigned"}</marker_set_file>
\t\t</GenericModelMaker>
\t\t<ModelScaler>
\t\t\t<apply>true</apply>
\t\t\t<scaling_order> measurements manualScale </scaling_order>
\t\t\t<MarkerPairSet>
\t\t\t\t<objects />
\t\t\t\t<groups />
\t\t\t</MarkerPairSet>
\t\t\t<MeasurementSet>
\t\t\t\t<objects />
\t\t\t\t<groups />
\t\t\t</MeasurementSet>
\t\t\t<marker_file>{marker_file}</marker_file>
\t\t\t<time_range>{time_range[0]} {time_range[1]}</time_range>
\t\t\t<output_model_file>{output_model_file}</output_model_file>
\t\t</ModelScaler>
\t\t<MarkerPlacer>
\t\t\t<apply>true</apply>
\t\t\t<IKTaskSet>
\t\t\t\t<objects />
\t\t\t\t<groups />
\t\t\t</IKTaskSet>
\t\t\t<marker_file>{marker_file}</marker_file>
\t\t\t<time_range>{time_range[0]} {time_range[1]}</time_range>
\t\t\t<output_model_file>{output_model_file}</output_model_file>
\t\t\t<output_motion_file></output_motion_file>
\t\t\t<output_marker_file></output_marker_file>
\t\t\t<max_marker_movement>-1</max_marker_movement>
\t\t</MarkerPlacer>
\t</ScaleTool>
</OpenSimDocument>
"""
    schema = {
        "required_keys": ["model_file", "marker_file"],
        "optional_keys": ["output_model_file", "marker_set_file", "subject_mass", "subject_name", "time_range"],
    }
    return xml, schema
