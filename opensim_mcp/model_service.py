"""
OpenSim モデルの読込・閲覧・複製を担うサービス層。

設計原則（仕様書 §4.1〜4.2）:
- ロード済みモデルはメモリ内にキャッシュする
- 元の .osim ファイルは直接書き換えない
- 構造変更は clone 後のコピーにのみ許可する
"""

from __future__ import annotations

import hashlib
import logging
import shutil
import threading
from pathlib import Path
from typing import Any

from .workspace import check_safe_name

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# モデルキャッシュ（スレッドセーフ）
# ---------------------------------------------------------------------------

_lock = threading.Lock()
_models: dict[str, Any] = {}          # str(path) -> opensim.Model
_current_model_path: str | None = None


# ---------------------------------------------------------------------------
# ヘルパー
# ---------------------------------------------------------------------------


def _hash_file(path: Path) -> str:
    """ファイルの SHA-256 先頭12文字を返す（整合性確認用）。"""
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()[:12]


def _count_components(model: Any) -> dict[str, int]:
    """モデル内の各コンポーネント数を集計する。"""
    return {
        "bodies": model.getBodySet().getSize(),
        "joints": model.getJointSet().getSize(),
        "coordinates": model.getCoordinateSet().getSize(),
        "muscles": model.getMuscles().getSize(),
        "forces": model.getForceSet().getSize(),
        "markers": model.getMarkerSet().getSize(),
        "controllers": model.getControllerSet().getSize(),
        "analyses": model.getAnalysisSet().getSize(),
        "probes": model.getProbeSet().getSize(),
    }


# ---------------------------------------------------------------------------
# 公開 API
# ---------------------------------------------------------------------------


def load_model(model_path: Path) -> dict[str, Any]:
    """
    .osim モデルをロードしてキャッシュに登録する。

    Returns
    -------
    dict
        model_name / model_hash / component_counts / warnings
    """
    import opensim  # 遅延インポート（conda env 外での import エラーを遅らせる）

    global _current_model_path

    warnings: list[str] = []
    model_hash = _hash_file(model_path)

    logger.info("Loading model: %s", model_path)
    try:
        model = opensim.Model(str(model_path))
        model.initSystem()
    except Exception as exc:
        raise RuntimeError(f"モデルのロードに失敗しました: {exc}") from exc

    path_key = str(model_path)
    with _lock:
        _models[path_key] = model
        _current_model_path = path_key

    counts = _count_components(model)
    logger.info("Loaded: %s (%s)", model.getName(), counts)

    return {
        "model_name": model.getName(),
        "model_hash": model_hash,
        "component_counts": counts,
        "warnings": warnings,
    }


def get_current_model() -> Any | None:
    """現在アクティブなモデルを返す（なければ None）。"""
    with _lock:
        if _current_model_path is None:
            return None
        return _models.get(_current_model_path)


def get_model(model_ref: str | None = None) -> Any:
    """
    model_ref が None なら current model を返す。

    Raises
    ------
    ValueError
        モデルが未ロードの場合。
    """
    if model_ref is None:
        model = get_current_model()
        if model is None:
            raise ValueError(
                "モデルがロードされていません。先に load_model を実行してください。"
            )
        return model

    with _lock:
        model = _models.get(model_ref)
    if model is None:
        raise ValueError(
            f"'{model_ref}' はロードされていません。load_model で先に読み込んでください。"
        )
    return model


def clone_model(source_path: Path, new_name: str, clones_dir: Path) -> Path:
    """
    source_path を new_name.osim として clones_dir に複製する。

    Returns
    -------
    Path
        複製先の絶対パス。

    Raises
    ------
    ValueError
        同名ファイルが既に存在する場合。
    """
    check_safe_name(new_name, "new_name")  # "../" で clones/ の外に書かせない
    clones_dir.mkdir(parents=True, exist_ok=True)
    dest = clones_dir / f"{new_name}.osim"
    if dest.exists():
        raise ValueError(f"'{dest.name}' は既に存在します。別の名前を指定してください。")
    shutil.copy2(source_path, dest)
    logger.info("Cloned: %s -> %s", source_path.name, dest.name)
    return dest


class AmbiguousComponentError(ValueError):
    """同名の component が複数あるときに送出する。candidates は (フルパス, クラス名) の一覧。"""

    def __init__(self, component_name: str, candidates: list[tuple[str, str]]) -> None:
        self.candidates = candidates
        listed = ", ".join(f"{path} ({cls})" for path, cls in candidates)
        super().__init__(f"'{component_name}' に一致する component が複数あります: {listed}")


def get_component_by_name(model: Any, component_name: str) -> Any | None:
    """
    全コンポーネントセットから名前で検索する。
    見つからなければ None を返す。

    同名の component が複数ある場合（例: 座標 elbow_flex_r と
    CoordinateActuator /forceset/elbow_flex_r）は推測せず AmbiguousComponentError を送出する。
    呼び出し側はフルパスでの指定を案内すること。
    """
    matches: list[Any] = []
    set_getters = [
        model.getBodySet,
        model.getJointSet,
        model.getForceSet,
        model.getMarkerSet,
        model.getControllerSet,
        model.getAnalysisSet,
        model.getProbeSet,
    ]
    for getter in set_getters:
        try:
            comp_set = getter()
            if comp_set.contains(component_name):
                matches.append(comp_set.get(component_name))
        except Exception:
            continue

    # CoordinateSet は contains() がないため名前ループで探す
    try:
        coord_set = model.getCoordinateSet()
        for i in range(coord_set.getSize()):
            c = coord_set.get(i)
            if c.getName() == component_name:
                matches.append(c)
                break
    except Exception:
        pass

    if len(matches) > 1:
        raise AmbiguousComponentError(
            component_name,
            [(c.getAbsolutePathString(), c.getConcreteClassName()) for c in matches],
        )
    return matches[0] if matches else None


def get_model_visual_data(model: Any) -> list[dict[str, Any]]:
    """
    モデルの現在の姿勢（workingState）における全体節の 3D 位置・回転およびメッシュ情報を取得する。
    """
    import opensim  # noqa: PLC0415
    state = model.getWorkingState()
    bodies = model.getBodySet()
    vis_data = []

    for i in range(bodies.getSize()):
        body = bodies.get(i)
        transform = body.getTransformInGround(state)
        p = transform.p()
        r = transform.R()

        # メッシュ情報の抽出
        geoms = []
        # getComponentsList() を使用して Mesh コンポーネントを探す
        for comp in body.getComponentsList():
            if comp.getConcreteClassName() == "Mesh":
                mesh = opensim.Mesh.safeDownCast(comp)
                # スケール因子の取得 (OpenSim 4.x)
                s = mesh.get_scale_factors()
                geoms.append({
                    "name": mesh.getName(),
                    "file": mesh.get_mesh_file(),
                    "scale": [s[0], s[1], s[2]],
                    # translation/orientation は Mesh 自体に設定されている場合があるが、
                    # OpenSim 4.x の多くは Frame 経由で配置されるため、一旦スキップ（または原点と想定）
                    "position": [0, 0, 0],
                    "rotation": [0, 0, 0],
                })

        vis_data.append({
            "name": body.getName(),
            "position": [p[0], p[1], p[2]],
            "rotation": [
                [r.get(0, 0), r.get(0, 1), r.get(0, 2)],
                [r.get(1, 0), r.get(1, 1), r.get(1, 2)],
                [r.get(2, 0), r.get(2, 1), r.get(2, 2)],
            ],
            "geometries": geoms,
            "markers": [],
        })

    return vis_data


def get_model_visual_data_at_state(model: Any, labels: list[str], values: list[float]) -> list[dict[str, Any]]:
    """
    特定の座標値（IKの結果など）をモデルに適用し、その瞬間の 3D 可視化データを取得する。
    """
    import opensim  # noqa: PLC0415
    state = model.getWorkingState()
    coord_set = model.getCoordinateSet()
    
    # 座標の適用
    for label, val in zip(labels, values):
        if coord_set.contains(label):
            coord = coord_set.get(label)
            # 度の場合はラジアンに変換（データが度である場合を想定）
            import math
            val_rad = math.radians(val) if coord.getMotionType() != opensim.Coordinate.Translational else val
            coord.setValue(state, val_rad)
    
    # システムの更新（ジオメトリ計算のため）
    model.realizePosition(state)
    
    # その状態でのトランスフォーメーションを抽出
    bodies = model.getBodySet()
    vis_data = []

    for i in range(bodies.getSize()):
        body = bodies.get(i)
        transform = body.getTransformInGround(state)
        p = transform.p()
        r = transform.R()

        geoms = []
        for comp in body.getComponentsList():
            if comp.getConcreteClassName() == "Mesh":
                mesh = opensim.Mesh.safeDownCast(comp)
                s = mesh.get_scale_factors()
                geoms.append({
                    "name": mesh.getName(),
                    "file": mesh.get_mesh_file(),
                    "scale": [s[0], s[1], s[2]],
                    "position": [0, 0, 0],
                    "rotation": [0, 0, 0],
                })

        vis_data.append({
            "name": body.getName(),
            "position": [p[0], p[1], p[2]],
            "rotation": [
                [r.get(0, 0), r.get(0, 1), r.get(0, 2)],
                [r.get(1, 0), r.get(1, 1), r.get(1, 2)],
                [r.get(2, 0), r.get(2, 1), r.get(2, 2)],
            ],
            "geometries": geoms,
            "markers": [],
        })

    return vis_data
