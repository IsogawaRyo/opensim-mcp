"""
ジョブ管理系 Tools。

実装 Tools（仕様書 §10.4 ジョブ管理 より Phase 1 対象）:
  25. get_job_status   [A / read_only / P0]
  26. get_job_outputs  [A / read_only / P0]
  27. get_job_log      [A / read_only / P0]
  28. summarize_results[A / read_only / P0]
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from ..job_service import get_log, get_outputs, get_status
from ..schemas import error_result, tool_result

if TYPE_CHECKING:
    from mcp.server.fastmcp import FastMCP

logger = logging.getLogger(__name__)


def register_job_tools(mcp: "FastMCP") -> None:
    """ジョブ管理 Tool を mcp インスタンスに登録する。"""

    # ------------------------------------------------------------------
    # 25) get_job_status
    # ------------------------------------------------------------------

    @mcp.tool()
    def get_job_status(job_id: str) -> dict[str, Any]:
        """
        ジョブの現在の状態を返す。

        Args:
            job_id: run_inverse_kinematics / run_analyze_tool などが返す job_id。

        Returns:
            status (queued|running|completed|failed|cancelled) /
            started_at / finished_at / progress_hint など。
        """
        try:
            meta = get_status(job_id)
        except ValueError as exc:
            return error_result("INPUT_ERROR", str(exc))

        return tool_result(
            status="completed",
            summary=f"Job {job_id}: {meta['status']}",
            structured_data={
                "job_id": meta["job_id"],
                "tool_name": meta["tool_name"],
                "status": meta["status"],
                "created_at": meta.get("created_at"),
                "started_at": meta.get("started_at"),
                "finished_at": meta.get("finished_at"),
                "opensim_version": meta.get("opensim_version"),
            },
        )

    # ------------------------------------------------------------------
    # 26) get_job_outputs
    # ------------------------------------------------------------------

    @mcp.tool()
    def get_job_outputs(job_id: str) -> dict[str, Any]:
        """
        ジョブの成果物ファイル一覧を返す。

        Args:
            job_id: ジョブ ID。

        Returns:
            artifacts リスト（path / type / size_bytes）。
        """
        try:
            get_status(job_id)   # job 存在確認
        except ValueError as exc:
            return error_result("INPUT_ERROR", str(exc))

        artifacts = get_outputs(job_id)
        return tool_result(
            status="completed",
            summary=f"Job {job_id}: {len(artifacts)} 個の成果物",
            artifacts=artifacts,
            structured_data={"job_id": job_id, "count": len(artifacts)},
        )

    # ------------------------------------------------------------------
    # 27) get_job_log
    # ------------------------------------------------------------------

    @mcp.tool()
    def get_job_log(job_id: str, stream: str = "stdout") -> dict[str, Any]:
        """
        ジョブのログを返す。

        Args:
            job_id: ジョブ ID。
            stream: "stdout" | "stderr" | "summary" のいずれか。

        Returns:
            log_text としてログ内容を返す。
        """
        _ALLOWED_STREAMS = {"stdout", "stderr", "summary"}
        if stream not in _ALLOWED_STREAMS:
            return error_result(
                "INPUT_ERROR",
                f"stream は {', '.join(sorted(_ALLOWED_STREAMS))} のいずれかを指定してください",
            )

        try:
            get_status(job_id)   # job 存在確認
        except ValueError as exc:
            return error_result("INPUT_ERROR", str(exc))

        log_text = get_log(job_id, stream)
        return tool_result(
            status="completed",
            summary=f"Job {job_id} [{stream}]: {len(log_text)} 文字",
            structured_data={
                "job_id": job_id,
                "stream": stream,
                "log_text": log_text,
            },
        )

    # ------------------------------------------------------------------
    # 28) summarize_results
    # ------------------------------------------------------------------

    @mcp.tool()
    def summarize_results(job_id: str) -> dict[str, Any]:
        """
        ジョブ結果を総合的に要約する。

        Args:
            job_id: ジョブ ID。

        Returns:
            summary / key_metrics / warnings / next_steps を含む結果サマリ。
        """
        try:
            meta = get_status(job_id)
        except ValueError as exc:
            return error_result("INPUT_ERROR", str(exc))

        if meta["status"] not in ("completed", "failed"):
            return tool_result(
                status="completed",
                summary=f"Job {job_id} はまだ実行中です (status: {meta['status']})",
                structured_data={
                    "job_id": job_id,
                    "status": meta["status"],
                    "message": "完了後に再度 summarize_results を実行してください",
                },
            )

        summary_text = get_log(job_id, "summary")
        outputs = get_outputs(job_id)
        stderr_text = get_log(job_id, "stderr")

        warnings: list[str] = []
        if stderr_text.strip():
            warnings.append(f"stderr に出力があります ({len(stderr_text)} 文字) — get_job_log で確認してください")

        next_steps: list[str] = []
        if meta["status"] == "completed":
            next_steps.append("get_job_outputs で成果物ファイルを確認してください")
            if meta["tool_name"] == "run_inverse_kinematics":
                next_steps.append("IK 結果の .mot ファイルを inspect_timeseries_file で確認できます")
            elif meta["tool_name"] == "run_analyze_tool":
                next_steps.append("Analyze 結果の .sto ファイルを inspect_timeseries_file で確認できます")
        elif meta["status"] == "failed":
            next_steps.append("get_job_log(job_id, 'stderr') でエラー詳細を確認してください")
            next_steps.append("setup XML のパスとモデルファイルの参照を確認してください")

        return tool_result(
            status="completed",
            summary=f"Job {job_id} ({meta['tool_name']}): {meta['status']}",
            warnings=warnings,
            artifacts=outputs,
            structured_data={
                "job_id": job_id,
                "tool_name": meta["tool_name"],
                "status": meta["status"],
                "started_at": meta.get("started_at"),
                "finished_at": meta.get("finished_at"),
                "summary": summary_text,
                "key_metrics": {},
                "next_steps": next_steps,
            },
        )
