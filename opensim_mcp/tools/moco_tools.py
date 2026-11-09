"""
Moco系 Tools（Phase 4 / v3）。

実装 Tools（仕様書 §10.5 より）:
  29. create_moco_study      [E / run_moco / P2]
  30. configure_moco_problem [E / run_moco / P2]
  31. solve_moco_problem     [E / run_moco / P2]
  32. summarize_moco_solution [A / read_only / P2]

設計:
  - MocoStudy の構成情報は workspace/setups/moco/{study_id}/config.json に永続化
  - solve は _job_runner.py 経由でサブプロセス実行
  - MocoTrack (tracking) / MocoInverse (inverse) / MocoStudy (custom) の 3 テンプレートをサポート
"""

from __future__ import annotations

import json
import logging
import uuid
from pathlib import Path
from typing import TYPE_CHECKING, Any

from ..config import WORKSPACE_DIR
from ..schemas import error_result, tool_result
from ..workspace import check_safe_name, resolve_path

if TYPE_CHECKING:
    from mcp.server.fastmcp import FastMCP

logger = logging.getLogger(__name__)

MOCO_SETUPS_DIR = WORKSPACE_DIR / "setups" / "moco"
MOCO_JOBS_DIR   = WORKSPACE_DIR / "jobs"

_TEMPLATE_TYPES = {"tracking", "inverse", "custom"}


def register_moco_tools(mcp: "FastMCP") -> None:
    """Phase 4 Moco 系 Tool を mcp インスタンスに登録する。"""

    # ------------------------------------------------------------------
    # 29) create_moco_study  [E / run_moco / P2]
    # ------------------------------------------------------------------

    @mcp.tool()
    def create_moco_study(
        model_path: str,
        template_type: str = "tracking",
        study_name: str | None = None,
    ) -> dict[str, Any]:
        """
        MocoStudy の土台を作成する。

        Args:
            model_path: 使用するモデル (.osim) の workspace 内パス。
            template_type: "tracking" | "inverse" | "custom" のいずれか。
                - tracking: MocoTrack を使って運動データをトラッキング
                - inverse : MocoInverse を使って逆問題を解く
                - custom  : MocoStudy を直接構築（上級者向け）
            study_name: 任意の名前（省略時は自動生成）。

        Returns:
            study_id とコンフィグファイルのパス。

        Note:
            - create_moco_study → configure_moco_problem → solve_moco_problem の順で実行。
            - 設定は workspace/setups/moco/{study_id}/config.json に保存される。
        """
        if template_type not in _TEMPLATE_TYPES:
            return error_result(
                "INPUT_ERROR",
                f"template_type '{template_type}' は未対応です",
                suggested_fix=f"対応テンプレート: {', '.join(sorted(_TEMPLATE_TYPES))}",
            )

        try:
            model_p = resolve_path(model_path)
        except ValueError as exc:
            return error_result("PATH_ERROR", str(exc))
        if not model_p.exists():
            return error_result("PATH_ERROR", f"モデルが見つかりません: {model_path}")

        study_id = str(uuid.uuid4())[:8]
        if study_name:
            # study_id はそのままフォルダ名になるので、"../" で setups/moco/ の外に書かせない
            try:
                check_safe_name(study_name, "study_name")
            except ValueError as exc:
                return error_result("INPUT_ERROR", str(exc))
            study_id = f"{study_name}_{study_id}"

        study_dir = MOCO_SETUPS_DIR / study_id
        study_dir.mkdir(parents=True, exist_ok=True)

        config = {
            "study_id":      study_id,
            "template_type": template_type,
            "model_path":    str(model_p),
            "goals":         [],
            "bounds":        {},
            "constraints":   [],
            "solver_settings": {
                "num_mesh_intervals": 50,
                "verbosity":          2,
                "optim_solver":       "ipopt",
                "optim_max_iterations": 1000,
            },
            "time_bounds":   [0.0, 1.0],
            "states_ref":    None,
            "coordinates_ref": None,
        }

        config_path = study_dir / "config.json"
        config_path.write_text(json.dumps(config, indent=2, ensure_ascii=False), encoding="utf-8")

        logger.info("MocoStudy created: %s (%s)", study_id, template_type)

        return tool_result(
            status="completed",
            summary=f"MocoStudy '{study_id}' ({template_type}) を作成しました",
            artifacts=[{"path": str(config_path.relative_to(WORKSPACE_DIR)), "type": "config"}],
            structured_data={
                "study_id":    study_id,
                "config_path": str(config_path.relative_to(WORKSPACE_DIR)),
                "template_type": template_type,
                "next_step": "configure_moco_problem で goals / bounds を設定してください",
            },
        )

    # ------------------------------------------------------------------
    # 30) configure_moco_problem  [E / run_moco / P2]
    # ------------------------------------------------------------------

    @mcp.tool()
    def configure_moco_problem(
        study_id: str,
        config_patch: dict[str, Any],
    ) -> dict[str, Any]:
        """
        MocoStudy の設定（goals / bounds / constraints / solver）を更新する。

        Args:
            study_id: create_moco_study で発行された study_id。
            config_patch: 上書きするキーと値のペア。以下のキーをサポートします:

              time_bounds: [start, end]  (秒)
                例: [0.0, 1.2]

              goals: リスト of dict
                各 goal は {"type": <GoalType>, "weight": <float>, **kwargs}
                サポートする GoalType:
                  "MocoControlGoal"          — 制御入力の最小化
                  "MocoStateTrackingGoal"    — 座標のトラッキング
                  "MocoMarkerTrackingGoal"   — マーカーのトラッキング
                  "MocoJointReactionGoal"    — 関節反力の最小化
                  "MocoFinalTimeGoal"        — 終端時間の最小化
                例:
                  [{"type": "MocoControlGoal", "weight": 0.001},
                   {"type": "MocoStateTrackingGoal", "weight": 1.0,
                    "states_ref": "data/sto/ik_states.sto"}]

              bounds: dict of coordinate_name -> [lower, upper]
                回転座標は度、並進座標は m。全テンプレートで有効。
                例: {"shoulder_elev": [-10, 180]}

              constraints: リスト of dict
                例: [{"type": "MocoControlBoundConstraint",
                       "control_paths": ["/forceset/TRIlong"],
                       "lower_bound": 0.0, "upper_bound": 1.0}]

              solver_settings: dict
                キー: num_mesh_intervals / verbosity / optim_max_iterations
                例: {"num_mesh_intervals": 100, "optim_max_iterations": 2000}

              states_ref: str  — 状態リファレンスファイルのパス (tracking では必須)
              coordinates_ref: str — 座標リファレンスファイルのパス (inverse では必須)
              external_loads_file: str — 外力 XML のパス (任意)

              muscle_model: "degroote"(既定) | "as_is"
                degroote: 腱を剛体にし、筋を DeGrooteFregly2016 に置き換える(Moco 公式例と同じ)。
                  Millard / Thelen などの弾性腱の筋はそのままでは Moco で解けない。
                as_is: モデルをそのまま使う。
              reserve_strength: float — 各座標に足す予備アクチュエータの最適力 (既定 1.0、0 で付けない)

              goals の MocoMarkerTrackingGoal は "markers_ref"(.trc)、
              MocoJointReactionGoal は "joint_path"(例: "/jointset/walker_knee_r")を指定する。

        Returns:
            更新後の config_path と config_summary。
        """
        config_path, config = _load_study_config(study_id)
        if config is None:
            return error_result(
                "INPUT_ERROR",
                f"study_id '{study_id}' が見つかりません",
                suggested_fix="create_moco_study を先に実行してください",
            )

        # パッチ適用
        for key, value in config_patch.items():
            if key in config:
                if isinstance(config[key], dict) and isinstance(value, dict):
                    config[key].update(value)
                else:
                    config[key] = value
            else:
                config[key] = value

        # 参照ファイルのパスを workspace 内の絶対パスに解決する(workspace 外は拒否)
        try:
            for path_key in ("states_ref", "coordinates_ref", "external_loads_file"):
                if config.get(path_key):
                    config[path_key] = str(resolve_path(config[path_key]))
            for goal in config.get("goals") or []:
                for path_key in ("states_ref", "markers_ref"):
                    if goal.get(path_key):
                        goal[path_key] = str(resolve_path(goal[path_key]))
        except ValueError as exc:
            return error_result("PATH_ERROR", str(exc))

        config_path.write_text(json.dumps(config, indent=2, ensure_ascii=False), encoding="utf-8")

        summary = _build_config_summary(config)
        logger.info("MocoStudy configured: %s", study_id)

        return tool_result(
            status="completed",
            summary=f"MocoStudy '{study_id}' の設定を更新しました",
            artifacts=[{"path": str(config_path.relative_to(WORKSPACE_DIR)), "type": "config"}],
            structured_data={
                "config_path":    str(config_path.relative_to(WORKSPACE_DIR)),
                "config_summary": summary,
                "next_step": "solve_moco_problem で最適化を実行してください",
            },
        )

    # ------------------------------------------------------------------
    # 31) solve_moco_problem  [E / run_moco / P2]
    # ------------------------------------------------------------------

    @mcp.tool()
    def solve_moco_problem(
        study_id: str,
        job_name: str | None = None,  # noqa: ARG001
    ) -> dict[str, Any]:
        """
        MocoStudy の最適化を非同期で実行する。

        Args:
            study_id: configure_moco_problem で設定済みの study_id。
            job_name: 任意のジョブ名（省略可）。

        Returns:
            job_id と初期ステータス "queued"。

        Note:
            - MocoInverse / MocoTrack / MocoStudy いずれかを template_type に従い実行する。
            - 完了後は summarize_moco_solution または get_job_outputs で結果を確認してください。
            - 大規模問題では数分〜数十分かかる場合があります。
        """
        from ..job_service import create_job, run_job  # noqa: PLC0415

        config_path, config = _load_study_config(study_id)
        if config is None:
            return error_result(
                "INPUT_ERROR",
                f"study_id '{study_id}' が見つかりません",
                suggested_fix="create_moco_study を先に実行してください",
            )

        job_id = create_job("solve_moco_problem", input_files=[str(config_path)])
        run_job(job_id, "moco", str(config_path))
        logger.info("Moco solve job queued: %s (study: %s)", job_id, study_id)

        return tool_result(
            status="accepted",
            summary=f"Moco solve ジョブ {job_id} を受け付けました (queued)",
            structured_data={
                "job_id":   job_id,
                "study_id": study_id,
                "status":   "queued",
                "config":   str(config_path.relative_to(WORKSPACE_DIR)),
                "next_step": (
                    f"get_job_status('{job_id}') で進捗を確認し、"
                    f"summarize_moco_solution('{job_id}') で結果を確認してください"
                ),
            },
        )

    # ------------------------------------------------------------------
    # 32) summarize_moco_solution  [A / read_only / P2]
    # ------------------------------------------------------------------

    @mcp.tool()
    def summarize_moco_solution(
        job_id: str,
    ) -> dict[str, Any]:
        """
        Moco ジョブの解を要約して返す。

        Args:
            job_id: solve_moco_problem で発行された job_id。

        Returns:
            objective_value / constraints_status / solution_files / summary。
        """
        from ..job_service import get_log, get_status  # noqa: PLC0415

        meta = get_status(job_id)
        if meta is None:
            return error_result("INPUT_ERROR", f"job_id '{job_id}' が見つかりません")

        status = meta.get("status", "unknown")
        stdout = get_log(job_id, "stdout")
        stderr = get_log(job_id, "stderr")

        if status not in ("completed", "failed"):
            return tool_result(
                status="completed",
                summary=f"ジョブ {job_id} はまだ実行中です (status: {status})",
                structured_data={
                    "job_id": job_id,
                    "status": status,
                    "started_at":  meta.get("started_at"),
                    "finished_at": meta.get("finished_at"),
                },
            )

        # 出力ファイルを探す
        job_dir = MOCO_JOBS_DIR / job_id
        solution_files: list[dict[str, Any]] = []
        for ext in (".sto", ".mot", ".xml", ".json"):
            for f in sorted(job_dir.rglob(f"*{ext}")):
                solution_files.append({
                    "path": str(f.relative_to(WORKSPACE_DIR)),
                    "type": f.suffix.lstrip("."),
                    "size_bytes": f.stat().st_size,
                })

        # stdout から objective value を抽出
        objective_value = _parse_objective(stdout)

        if status == "failed":
            return tool_result(
                status="failed",
                summary=f"Moco solve が失敗しました (job: {job_id})",
                warnings=["ジョブが失敗しました。get_job_log でエラー詳細を確認してください"],
                structured_data={
                    "job_id":          job_id,
                    "status":          "failed",
                    "stderr_tail":     stderr[-2000:] if stderr else "",
                    "solution_files":  solution_files,
                    "constraints_status": "failed",
                },
            )

        return tool_result(
            status="completed",
            summary=f"Moco solve 完了 (job: {job_id}, objective: {objective_value})",
            artifacts=solution_files,
            structured_data={
                "job_id":          job_id,
                "status":          "completed",
                "objective_value": objective_value,
                "constraints_status": "satisfied",
                "solution_files":  solution_files,
                "started_at":      meta.get("started_at"),
                "finished_at":     meta.get("finished_at"),
            },
        )


# ---------------------------------------------------------------------------
# 内部ヘルパー
# ---------------------------------------------------------------------------


def _load_study_config(study_id: str) -> tuple[Path, dict | None]:
    """study_id から config.json をロードする。存在しない場合は (None, None)。"""
    try:
        check_safe_name(study_id, "study_id")
    except ValueError:
        return MOCO_SETUPS_DIR, None
    config_path = MOCO_SETUPS_DIR / study_id / "config.json"
    if not config_path.exists():
        # 部分一致でディレクトリを探す
        matches = list(MOCO_SETUPS_DIR.glob(f"{study_id}*/config.json"))
        if matches:
            config_path = matches[0]
        else:
            return config_path, None

    config = json.loads(config_path.read_text(encoding="utf-8"))
    return config_path, config


def _build_config_summary(config: dict[str, Any]) -> dict[str, Any]:
    """設定の要約を返す。"""
    return {
        "template_type":     config.get("template_type"),
        "time_bounds":       config.get("time_bounds"),
        "num_goals":         len(config.get("goals", [])),
        "num_bounds":        len(config.get("bounds", {})),
        "num_constraints":   len(config.get("constraints", [])),
        "solver_settings":   config.get("solver_settings", {}),
        "states_ref":        config.get("states_ref"),
        "coordinates_ref":   config.get("coordinates_ref"),
    }


def _parse_objective(stdout: str) -> str:
    """stdout から objective value を抽出する。"""
    for line in reversed(stdout.splitlines()):
        line = line.lower()
        for keyword in ("objective", "f =", "f=", "total cost", "optimal"):
            if keyword in line:
                parts = line.split()
                for i, p in enumerate(parts):
                    if keyword.strip("=") in p and i + 1 < len(parts):
                        try:
                            return str(float(parts[i + 1].strip(",")))
                        except ValueError:
                            pass
    return "N/A"
