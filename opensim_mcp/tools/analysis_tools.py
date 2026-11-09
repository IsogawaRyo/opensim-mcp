"""
解析実行系 Tools。

実装 Tools（仕様書 §10.4 より）:
  Phase 1:
    18. run_inverse_kinematics  [E / run_analysis / P0]
    24. run_analyze_tool        [E / run_analysis / P0]
  Phase 2:
    19. run_inverse_dynamics    [E / run_analysis / P1]
  Phase 3:
    17. run_scale               [E / run_analysis / P2]
    20. run_static_optimization [E / run_analysis / P2]
    21. run_rra                 [E / run_analysis / P2]
    22. run_cmc                 [E / run_analysis / P2]
    23. run_forward_dynamics    [E / run_analysis / P2]

どれもジョブ管理を経由してサブプロセスで非同期実行される（仕様書 §4.3）。
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import TYPE_CHECKING, Any

from ..job_service import create_job, run_job
from ..schemas import error_result, tool_result
from ..workspace import check_setup_paths, resolve_path

if TYPE_CHECKING:
    from mcp.server.fastmcp import FastMCP

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# ジョブ実行関数（run_job の fn 引数として渡す）
# ---------------------------------------------------------------------------


def _run_ik(
    setup_xml_path: str,
    *,
    stdout_log: Any,
    stderr_log: Any,
    job_dir: Path,
    **_kwargs: Any,
) -> dict[str, Any]:
    """InverseKinematicsTool を実行する（ジョブスレッド内で呼ばれる）。"""
    import opensim  # noqa: PLC0415

    stdout_log.write(f"[IK] Setup XML: {setup_xml_path}\n")
    stdout_log.flush()

    ik_tool = opensim.InverseKinematicsTool(setup_xml_path)
    ik_tool.run()

    stdout_log.write("[IK] 完了\n")
    return {
        "tool": "InverseKinematicsTool",
        "setup_xml": setup_xml_path,
        "status": "completed",
    }


def _run_analyze(
    setup_xml_path: str,
    *,
    stdout_log: Any,
    stderr_log: Any,
    job_dir: Path,
    **_kwargs: Any,
) -> dict[str, Any]:
    """AnalyzeTool を実行する（ジョブスレッド内で呼ばれる）。"""
    import opensim  # noqa: PLC0415

    stdout_log.write(f"[Analyze] Setup XML: {setup_xml_path}\n")
    stdout_log.flush()

    analyze_tool = opensim.AnalyzeTool(setup_xml_path)
    analyze_tool.run()

    stdout_log.write("[Analyze] 完了\n")
    return {
        "tool": "AnalyzeTool",
        "setup_xml": setup_xml_path,
        "status": "completed",
    }


# ---------------------------------------------------------------------------
# Tool 登録
# ---------------------------------------------------------------------------


def register_analysis_tools(mcp: "FastMCP") -> None:
    """解析実行系 Tool を mcp インスタンスに登録する。"""

    @mcp.tool()
    def run_inverse_kinematics(
        setup_xml_path: str,
        job_name: str | None = None,  # noqa: ARG001
    ) -> dict[str, Any]:
        """
        Inverse Kinematics をジョブとして非同期実行する。

        Args:
            setup_xml_path: IK setup XML のパス（workspace 内相対 or 絶対）。
            job_name: 任意のジョブ表示名（現在は無視されます）。

        Returns:
            job_id と初期ステータス "queued"。
            進捗確認には get_job_status(job_id) を使用してください。

        Note:
            - setup XML には .trc マーカーファイルと .osim モデルへの参照が必要です。
            - 結果ファイルは setup XML 内の output_motion_file に保存されます。
        """
        try:
            p = resolve_path(setup_xml_path)
        except ValueError as exc:
            return error_result("PATH_ERROR", str(exc))

        if not p.exists():
            return error_result(
                "PATH_ERROR",
                f"setup XML が見つかりません: {setup_xml_path}",
                suggested_fix="workspace/setups/ik/ にファイルを配置してください",
            )

        try:
            check_setup_paths(p)
        except ValueError as exc:
            return error_result("PATH_ERROR", str(exc),
                                suggested_fix="setup XML の中のパスを workspace 内に直してください")

        job_id = create_job("run_inverse_kinematics", input_files=[str(p)])
        run_job(job_id, "ik", str(p))
        logger.info("IK job queued: %s", job_id)

        return tool_result(
            status="accepted",
            summary=f"IK ジョブ {job_id} を受け付けました (queued)",
            structured_data={
                "job_id": job_id,
                "status": "queued",
                "setup_xml": str(p),
            },
        )

    @mcp.tool()
    def run_analyze_tool(
        setup_xml_path: str,
        job_name: str | None = None,  # noqa: ARG001
    ) -> dict[str, Any]:
        """
        Analyze Tool をジョブとして非同期実行する。

        Args:
            setup_xml_path: Analyze setup XML のパス（workspace 内相対 or 絶対）。
            job_name: 任意のジョブ表示名（現在は無視されます）。

        Returns:
            job_id と初期ステータス "queued"。
            進捗確認には get_job_status(job_id) を使用してください。

        Note:
            - JointReaction, PointKinematics, BodyKinematics などを setup XML で指定します。
            - 結果ファイルは setup XML 内の results_directory に保存されます。
        """
        try:
            p = resolve_path(setup_xml_path)
        except ValueError as exc:
            return error_result("PATH_ERROR", str(exc))

        if not p.exists():
            return error_result(
                "PATH_ERROR",
                f"setup XML が見つかりません: {setup_xml_path}",
                suggested_fix="workspace/setups/analyze/ にファイルを配置してください",
            )

        try:
            check_setup_paths(p)
        except ValueError as exc:
            return error_result("PATH_ERROR", str(exc),
                                suggested_fix="setup XML の中のパスを workspace 内に直してください")

        job_id = create_job("run_analyze_tool", input_files=[str(p)])
        run_job(job_id, "analyze", str(p))
        logger.info("Analyze job queued: %s", job_id)

        return tool_result(
            status="accepted",
            summary=f"Analyze ジョブ {job_id} を受け付けました (queued)",
            structured_data={
                "job_id": job_id,
                "status": "queued",
                "setup_xml": str(p),
            },
        )

    # ------------------------------------------------------------------
    # 19) run_inverse_dynamics  [Phase 2 / P1]
    # ------------------------------------------------------------------

    @mcp.tool()
    def run_inverse_dynamics(
        setup_xml_path: str,
        job_name: str | None = None,  # noqa: ARG001
    ) -> dict[str, Any]:
        """
        Inverse Dynamics をジョブとして非同期実行する。

        Args:
            setup_xml_path: ID setup XML のパス（workspace 内相対 or 絶対）。
                            write_setup_xml tool で生成するか、手動で作成してください。
            job_name: 任意のジョブ表示名（現在は無視されます）。

        Returns:
            job_id と初期ステータス "queued"。
            進捗確認には get_job_status(job_id) を使用してください。

        Note:
            - setup XML には IK 結果の .mot ファイルと外力データへの参照が必要です。
            - 結果は setup XML 内の output_gen_force_file に保存されます。
        """
        try:
            p = resolve_path(setup_xml_path)
        except ValueError as exc:
            return error_result("PATH_ERROR", str(exc))

        if not p.exists():
            return error_result(
                "PATH_ERROR",
                f"setup XML が見つかりません: {setup_xml_path}",
                suggested_fix="workspace/setups/id/ にファイルを配置してください。write_setup_xml tool が利用できます。",
            )

        try:
            check_setup_paths(p)
        except ValueError as exc:
            return error_result("PATH_ERROR", str(exc),
                                suggested_fix="setup XML の中のパスを workspace 内に直してください")

        job_id = create_job("run_inverse_dynamics", input_files=[str(p)])
        run_job(job_id, "id", str(p))
        logger.info("ID job queued: %s", job_id)

        return tool_result(
            status="accepted",
            summary=f"ID ジョブ {job_id} を受け付けました (queued)",
            structured_data={
                "job_id": job_id,
                "status": "queued",
                "setup_xml": str(p),
            },
        )

    # ==================================================================
    # Phase 3 Tools
    # ==================================================================

    def _make_analysis_tool(
        mcp_tool_name: str,
        tool_name_key: str,
        display_name: str,
        job_tool_name: str,
        setup_dir_hint: str,
        docstring: str,
    ) -> None:
        """Phase 3 解析ツールを動的に生成して登録する内部ヘルパー。"""
        @mcp.tool(name=mcp_tool_name)
        def _analysis_tool(
            setup_xml_path: str,
            job_name: str | None = None,  # noqa: ARG001
        ) -> dict[str, Any]:
            try:
                p = resolve_path(setup_xml_path)
            except ValueError as exc:
                return error_result("PATH_ERROR", str(exc))

            if not p.exists():
                return error_result(
                    "PATH_ERROR",
                    f"setup XML が見つかりません: {setup_xml_path}",
                    suggested_fix=f"workspace/{setup_dir_hint}/ にファイルを配置してください。write_setup_xml tool が利用できます。",
                )

            try:
                check_setup_paths(p)
            except ValueError as exc:
                return error_result("PATH_ERROR", str(exc),
                                    suggested_fix="setup XML の中のパスを workspace 内に直してください")

            job_id = create_job(job_tool_name, input_files=[str(p)])
            run_job(job_id, tool_name_key, str(p))
            logger.info("%s job queued: %s", display_name, job_id)

            return tool_result(
                status="accepted",
                summary=f"{display_name} ジョブ {job_id} を受け付けました (queued)",
                structured_data={
                    "job_id": job_id,
                    "status": "queued",
                    "setup_xml": str(p),
                },
            )

        _analysis_tool.__doc__ = docstring

    # 17) run_scale
    _make_analysis_tool(
        mcp_tool_name="run_scale",
        tool_name_key="scale",
        display_name="Scale",
        job_tool_name="run_scale",
        setup_dir_hint="setups/scale",
        docstring=(
            "ScaleTool をジョブとして非同期実行する。\n\n"
            "Args:\n"
            "    setup_xml_path: Scale setup XML のパス。\n"
            "Returns:\n"
            "    job_id と初期ステータス \"queued\"。\n\n"
            "Note:\n"
            "    - setup XML には被験者データ (.trc) とスケールファクタモデルへの参照が必要です。\n"
            "    - 出力はスケール済み .osim モデルです。"
        ),
    )

    # 20) run_static_optimization
    _make_analysis_tool(
        mcp_tool_name="run_static_optimization",
        tool_name_key="so",
        display_name="StaticOptimization",
        job_tool_name="run_static_optimization",
        setup_dir_hint="setups/analyze",
        docstring=(
            "Static Optimization をジョブとして非同期実行する。\n\n"
            "Args:\n"
            "    setup_xml_path: Analyze setup XML (StaticOptimization 解析を含む)のパス。\n"
            "Returns:\n"
            "    job_id と初期ステータス \"queued\"。\n\n"
            "Note:\n"
            "    - StaticOptimization は AnalyzeTool に属するため、Analyze setup XML で実行します。\n"
            "    - 事前に IK/ID 結果の .mot が必要です。"
        ),
    )

    # 21) run_rra
    _make_analysis_tool(
        mcp_tool_name="run_rra",
        tool_name_key="rra",
        display_name="RRA",
        job_tool_name="run_rra",
        setup_dir_hint="setups/rra",
        docstring=(
            "Residual Reduction Algorithm (RRA) をジョブとして非同期実行する。\n\n"
            "Args:\n"
            "    setup_xml_path: RRA setup XML のパス。\n"
            "Returns:\n"
            "    job_id と初期ステータス \"queued\"。\n\n"
            "Note:\n"
            "    - setup XML には IK 結果・外力データ・アクチュエータ指定ファイルへの参照が必要です。"
        ),
    )

    # 22) run_cmc
    _make_analysis_tool(
        mcp_tool_name="run_cmc",
        tool_name_key="cmc",
        display_name="CMC",
        job_tool_name="run_cmc",
        setup_dir_hint="setups/cmc",
        docstring=(
            "Computed Muscle Control (CMC) をジョブとして非同期実行する。\n\n"
            "Args:\n"
            "    setup_xml_path: CMC setup XML のパス。\n"
            "Returns:\n"
            "    job_id と初期ステータス \"queued\"。\n\n"
            "Note:\n"
            "    - RRA の結果を入力とするのが一般的です。"
        ),
    )

    # 23) run_forward_dynamics
    _make_analysis_tool(
        mcp_tool_name="run_forward_dynamics",
        tool_name_key="fd",
        display_name="ForwardDynamics",
        job_tool_name="run_forward_dynamics",
        setup_dir_hint="setups/fd",
        docstring=(
            "Forward Dynamics をジョブとして非同期実行する。\n\n"
            "Args:\n"
            "    setup_xml_path: Forward Dynamics setup XML のパス。\n"
            "Returns:\n"
            "    job_id と初期ステータス \"queued\"。\n\n"
            "Note:\n"
            "    - setup XML にはモデル・制御入力・初期状態の設定が必要です。"
        ),
    )
