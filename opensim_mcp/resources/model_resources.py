"""
モデル系 Resources。

実装 Resources（仕様書 §8.2 より）:
  opensim://model/summary
  opensim://model/bodies
  opensim://model/joints
  opensim://model/coordinates
  opensim://model/muscles
  opensim://model/forces
  opensim://model/markers
  opensim://model/controllers
  opensim://model/analyses
  opensim://model/probes
  opensim://model/properties/{component_name}
  opensim://workspace/files
  opensim://file/summary/{path}   (path は URL エンコードした workspace 相対パス)
"""

from __future__ import annotations

import json
import logging
from typing import TYPE_CHECKING, Any
from urllib.parse import unquote

from ..config import WORKSPACE_DIR
from ..model_service import AmbiguousComponentError, get_component_by_name, get_current_model, get_model
from ..schemas import resource_result
from ..tools.file_tools import inspect_file

if TYPE_CHECKING:
    from mcp.server.fastmcp import FastMCP

logger = logging.getLogger(__name__)


def register_model_resources(mcp: "FastMCP") -> None:
    """モデル系 Resource を mcp インスタンスに登録する。"""

    def _no_model_result(source: str) -> str:
        return json.dumps(
            resource_result(source, "モデル未ロード",
                            warnings=["load_model を実行してからアクセスしてください"]),
            ensure_ascii=False,
        )

    def _extract_set(comp_set: Any) -> list[dict[str, str]]:
        return [
            {
                "name": comp_set.get(i).getName(),
                "type": comp_set.get(i).getConcreteClassName(),
            }
            for i in range(comp_set.getSize())
        ]

    # ------------------------------------------------------------------
    # opensim://model/summary
    # ------------------------------------------------------------------

    @mcp.resource("opensim://model/summary")
    def model_summary() -> str:
        """現在ロードされているモデルの概要。"""
        source = "opensim://model/summary"
        model = get_current_model()
        if model is None:
            return _no_model_result(source)
        try:
            gravity = model.getGravity()
            gvec = [gravity.get(0), gravity.get(1), gravity.get(2)]
            nb = model.getBodySet().getSize()
            nj = model.getJointSet().getSize()
            nm = model.getMuscles().getSize()
            return json.dumps(
                resource_result(
                    source,
                    f"{model.getName()}: {nb} bodies, {nj} joints, {nm} muscles",
                    structured_data={
                        "model_name": model.getName(),
                        "num_bodies": nb,
                        "num_joints": nj,
                        "num_coordinates": model.getCoordinateSet().getSize(),
                        "num_muscles": nm,
                        "num_forces": model.getForceSet().getSize(),
                        "gravity": gvec,
                    },
                ),
                ensure_ascii=False,
            )
        except Exception as exc:
            return json.dumps(resource_result(source, f"エラー: {exc}", warnings=[str(exc)]),
                              ensure_ascii=False)

    # ------------------------------------------------------------------
    # コンポーネントセット系 Resource（共通ファクトリ）
    # ------------------------------------------------------------------

    def _make_set_resource(uri: str, label: str, getter_name: str) -> None:
        @mcp.resource(uri)
        def _resource() -> str:
            model = get_current_model()
            if model is None:
                return _no_model_result(uri)
            try:
                getter = getattr(model, getter_name)
                items = _extract_set(getter())
                return json.dumps(
                    resource_result(uri, f"{len(items)} {label}",
                                    structured_data={"count": len(items), "items": items}),
                    ensure_ascii=False,
                )
            except Exception as exc:
                return json.dumps(resource_result(uri, f"エラー: {exc}", warnings=[str(exc)]),
                                  ensure_ascii=False)
        # FastMCP が関数名でリソースを区別するため、名前を一意にする
        _resource.__name__ = f"resource_{label}"

    _make_set_resource("opensim://model/bodies",      "bodies",      "getBodySet")
    _make_set_resource("opensim://model/joints",      "joints",      "getJointSet")
    _make_set_resource("opensim://model/coordinates", "coordinates", "getCoordinateSet")
    _make_set_resource("opensim://model/muscles",     "muscles",     "getMuscles")
    _make_set_resource("opensim://model/forces",      "forces",      "getForceSet")
    _make_set_resource("opensim://model/markers",     "markers",     "getMarkerSet")
    _make_set_resource("opensim://model/controllers", "controllers", "getControllerSet")
    _make_set_resource("opensim://model/analyses",    "analyses",    "getAnalysisSet")
    _make_set_resource("opensim://model/probes",      "probes",      "getProbeSet")

    # ------------------------------------------------------------------
    # opensim://model/properties/{component_name}
    # ------------------------------------------------------------------

    @mcp.resource("opensim://model/properties/{component_name}")
    def model_component_properties(component_name: str) -> str:
        """指定 component の properties と outputs。"""
        source = f"opensim://model/properties/{component_name}"
        try:
            model = get_model()
        except ValueError:
            return _no_model_result(source)

        try:
            component = get_component_by_name(model, component_name)
        except AmbiguousComponentError as exc:
            return json.dumps(
                resource_result(source, str(exc),
                                warnings=["フルパスで指定してください"]),
                ensure_ascii=False,
            )
        if component is None:
            try:
                component = model.getComponent(component_name)
            except Exception:
                pass

        if component is None:
            return json.dumps(
                resource_result(source, f"'{component_name}' が見つかりません",
                                warnings=[f"list_components で名前を確認してください"]),
                ensure_ascii=False,
            )

        properties: list[dict[str, str]] = []
        try:
            for i in range(component.getNumProperties()):
                prop = component.getPropertyByIndex(i)
                properties.append({"name": prop.getName(), "type": prop.getTypeName()})
        except Exception:
            pass

        outputs: list[str] = []
        try:
            names = component.getOutputNames()
            outputs = [names[i] for i in range(len(names))]
        except Exception:
            pass

        return json.dumps(
            resource_result(
                source,
                f"'{component_name}': {len(properties)} properties",
                structured_data={
                    "component_name": component_name,
                    "component_type": component.getConcreteClassName(),
                    "properties": properties,
                    "outputs": outputs,
                },
            ),
            ensure_ascii=False,
        )

    # ------------------------------------------------------------------
    # opensim://workspace/files
    # ------------------------------------------------------------------

    @mcp.resource("opensim://workspace/files")
    def workspace_files() -> str:
        """ワークスペース内のファイル一覧。"""
        source = "opensim://workspace/files"
        _IMPORTANT_EXTS = {".osim", ".xml", ".trc", ".mot", ".sto", ".c3d", ".csv"}
        try:
            files = [
                {
                    "path": str(p.relative_to(WORKSPACE_DIR)),
                    "type": p.suffix.lstrip("."),
                    "size_bytes": p.stat().st_size,
                }
                for p in sorted(WORKSPACE_DIR.rglob("*"))
                if p.is_file() and p.suffix.lower() in _IMPORTANT_EXTS
            ]
            return json.dumps(
                resource_result(source, f"{len(files)} ファイル",
                                structured_data={"count": len(files), "files": files}),
                ensure_ascii=False,
            )
        except Exception as exc:
            return json.dumps(resource_result(source, f"エラー: {exc}", warnings=[str(exc)]),
                              ensure_ascii=False)

    # ------------------------------------------------------------------
    # opensim://file/summary/{path}
    # ------------------------------------------------------------------

    @mcp.resource("opensim://file/summary/{path}")
    def file_summary(path: str) -> str:
        """時系列ファイル（.sto / .mot / .trc / .c3d / .csv）の要約。path は URL エンコードした workspace 相対パス。"""
        source = f"opensim://file/summary/{path}"
        result = inspect_file(unquote(path))
        warnings = list(result.get("warnings") or [])
        if result.get("status") != "completed":
            warnings.append(result.get("summary", ""))
        return json.dumps(
            resource_result(source, result.get("summary", ""), warnings=warnings,
                            structured_data=result.get("structured_data")),
            ensure_ascii=False,
        )
