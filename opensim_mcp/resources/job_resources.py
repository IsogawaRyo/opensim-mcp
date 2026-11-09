"""
ジョブ系 Resources。

実装 Resources（仕様書 §8.2 より）:
  opensim://job/{job_id}/status
  opensim://job/{job_id}/log
  opensim://job/{job_id}/outputs
  opensim://job/{job_id}/summary
"""

from __future__ import annotations

import json
import logging
from typing import TYPE_CHECKING

from ..job_service import get_log, get_outputs, get_status
from ..schemas import resource_result

if TYPE_CHECKING:
    from mcp.server.fastmcp import FastMCP

logger = logging.getLogger(__name__)


def register_job_resources(mcp: "FastMCP") -> None:
    """ジョブ系 Resource を mcp インスタンスに登録する。"""

    # ------------------------------------------------------------------
    # opensim://job/{job_id}/status
    # ------------------------------------------------------------------

    @mcp.resource("opensim://job/{job_id}/status")
    def job_status(job_id: str) -> str:
        """ジョブの状態メタデータ。"""
        source = f"opensim://job/{job_id}/status"
        try:
            meta = get_status(job_id)
            return json.dumps(
                resource_result(source, f"Job {job_id}: {meta['status']}",
                                structured_data=meta),
                ensure_ascii=False,
            )
        except ValueError as exc:
            return json.dumps(
                resource_result(source, str(exc), warnings=[str(exc)]),
                ensure_ascii=False,
            )

    # ------------------------------------------------------------------
    # opensim://job/{job_id}/log
    # ------------------------------------------------------------------

    @mcp.resource("opensim://job/{job_id}/log")
    def job_log(job_id: str) -> str:
        """ジョブの stdout ログ。"""
        source = f"opensim://job/{job_id}/log"
        try:
            get_status(job_id)  # 存在確認
        except ValueError as exc:
            return json.dumps(resource_result(source, str(exc), warnings=[str(exc)]),
                              ensure_ascii=False)

        stdout = get_log(job_id, "stdout")
        stderr = get_log(job_id, "stderr")
        return json.dumps(
            resource_result(
                source,
                f"stdout: {len(stdout)} 文字, stderr: {len(stderr)} 文字",
                structured_data={"stdout": stdout, "stderr": stderr},
            ),
            ensure_ascii=False,
        )

    # ------------------------------------------------------------------
    # opensim://job/{job_id}/outputs
    # ------------------------------------------------------------------

    @mcp.resource("opensim://job/{job_id}/outputs")
    def job_outputs(job_id: str) -> str:
        """ジョブの成果物ファイル一覧。"""
        source = f"opensim://job/{job_id}/outputs"
        try:
            get_status(job_id)
        except ValueError as exc:
            return json.dumps(resource_result(source, str(exc), warnings=[str(exc)]),
                              ensure_ascii=False)

        outputs = get_outputs(job_id)
        return json.dumps(
            resource_result(source, f"{len(outputs)} 個の成果物",
                            structured_data={"count": len(outputs), "artifacts": outputs}),
            ensure_ascii=False,
        )

    # ------------------------------------------------------------------
    # opensim://job/{job_id}/summary
    # ------------------------------------------------------------------

    @mcp.resource("opensim://job/{job_id}/summary")
    def job_summary(job_id: str) -> str:
        """ジョブの実行サマリ（summary.json）。"""
        source = f"opensim://job/{job_id}/summary"
        try:
            meta = get_status(job_id)
        except ValueError as exc:
            return json.dumps(resource_result(source, str(exc), warnings=[str(exc)]),
                              ensure_ascii=False)

        summary_text = get_log(job_id, "summary")
        return json.dumps(
            resource_result(
                source,
                f"Job {job_id} ({meta['tool_name']}): {meta['status']}",
                structured_data={
                    "job_id": job_id,
                    "tool_name": meta["tool_name"],
                    "status": meta["status"],
                    "raw_summary": summary_text,
                },
            ),
            ensure_ascii=False,
        )
