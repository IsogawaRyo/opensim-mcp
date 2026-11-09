"""
共通の返却スキーマ。
仕様書 §9.2 Tool共通返却形式 と §8.3 Resource返却形式 に対応する。
"""

from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Any


# ---------------------------------------------------------------------------
# Job
# ---------------------------------------------------------------------------


class JobStatus(str, Enum):
    QUEUED = "queued"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


# ---------------------------------------------------------------------------
# Tool 返却ヘルパー
# ---------------------------------------------------------------------------


def tool_result(
    status: str,
    summary: str,
    warnings: list[str] | None = None,
    artifacts: list[dict[str, Any]] | None = None,
    structured_data: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """
    正常系の Tool 返却オブジェクトを生成する。

    Examples
    --------
    >>> tool_result("completed", "IK 完了", artifacts=[{"path": "jobs/001/ik.mot", "type": "motion"}])
    """
    return {
        "status": status,
        "summary": summary,
        "warnings": warnings or [],
        "artifacts": artifacts or [],
        "structured_data": structured_data or {},
    }


def error_result(
    error_code: str,
    summary: str,
    *,
    retryable: bool = False,
    suggested_fix: str = "",
) -> dict[str, Any]:
    """
    エラー時の Tool 返却オブジェクトを生成する。

    仕様書 §14.2 返却形式 に準拠する。
    """
    return {
        "status": "failed",
        "error_code": error_code,
        "summary": summary,
        "warnings": [],
        "artifacts": [],
        "structured_data": {
            "retryable": retryable,
            "suggested_fix": suggested_fix,
        },
    }


# ---------------------------------------------------------------------------
# Resource 返却ヘルパー
# ---------------------------------------------------------------------------


def resource_result(
    source: str,
    summary: str,
    warnings: list[str] | None = None,
    structured_data: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """
    Resource 返却オブジェクトを生成する。

    仕様書 §8.3 に準拠する。
    """
    return {
        "source": source,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "summary": summary,
        "warnings": warnings or [],
        "structured_data": structured_data or {},
    }
