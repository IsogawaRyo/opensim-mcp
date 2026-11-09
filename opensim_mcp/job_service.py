"""
ジョブライフサイクル管理。

仕様書 §12 ジョブ管理仕様 に準拠する。
- UUID ベースの job_id
- 状態遷移: queued → running → completed / failed / cancelled
- 各ジョブディレクトリに metadata.json / stdout.log / stderr.log / summary.json / artifacts/ を保存
- subprocess ベースの非同期実行
  （OpenSim の C++ ライブラリはスレッドセーフでないため、
    ThreadPoolExecutor ではなく独立した子プロセスで実行する）
"""

from __future__ import annotations

import json
import logging
import os
import subprocess
import sys
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from .config import SERVER_VERSION, WORKSPACE_DIR
from .schemas import JobStatus
from .workspace import check_safe_name

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# ジョブレジストリ（スレッドセーフ）
# ---------------------------------------------------------------------------

_jobs: dict[str, dict[str, Any]] = {}   # job_id -> metadata dict
_lock = threading.Lock()


# ---------------------------------------------------------------------------
# ディレクトリヘルパー
# ---------------------------------------------------------------------------


def _jobs_root() -> Path:
    return WORKSPACE_DIR / "jobs"


def _job_dir(job_id: str) -> Path:
    check_safe_name(job_id, "job_id")  # "../" で jobs/ の外を読ませない
    return _jobs_root() / job_id


# ---------------------------------------------------------------------------
# 内部ユーティリティ
# ---------------------------------------------------------------------------


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _write_metadata(job_id: str, meta: dict[str, Any]) -> None:
    try:
        with open(_job_dir(job_id) / "metadata.json", "w", encoding="utf-8") as f:
            json.dump(meta, f, indent=2, ensure_ascii=False)
    except Exception as exc:
        logger.warning("metadata.json の書き込みに失敗: %s", exc)


def _update_metadata(job_id: str, updates: dict[str, Any]) -> None:
    with _lock:
        if job_id in _jobs:
            _jobs[job_id].update(updates)
        meta = dict(_jobs.get(job_id, {}))
    _write_metadata(job_id, meta)


def _load_from_disk(job_id: str) -> None:
    """メモリにない job_id をディスクから復元する。"""
    with _lock:
        if job_id in _jobs:
            return
    meta_file = _job_dir(job_id) / "metadata.json"
    if meta_file.exists():
        with open(meta_file, encoding="utf-8") as f:
            meta = json.load(f)
        with _lock:
            _jobs[job_id] = meta


def _load_from_disk_force(job_id: str) -> None:
    """子プロセス完了後にディスクの最新状態を強制リロードする。"""
    meta_file = _job_dir(job_id) / "metadata.json"
    if meta_file.exists():
        with open(meta_file, encoding="utf-8") as f:
            meta = json.load(f)
        with _lock:
            _jobs[job_id] = meta


_UNFINISHED = {JobStatus.QUEUED.value, JobStatus.RUNNING.value}


def _status_value(status: Any) -> str:
    """JobStatus でも文字列でも "running" のような値にそろえる(str(JobStatus.X) は "JobStatus.X" になる)。"""
    return status.value if isinstance(status, JobStatus) else str(status)


def _fail_if_unfinished(job_id: str, reason: str) -> None:
    """
    実行プロセスがいないのに queued / running のままのジョブを failed にする。

    OpenSim が segfault するなど、実行プロセスが例外処理まで届かずに死ぬと、
    状態が running のまま永久に残るため。
    """
    with _lock:
        meta = _jobs.get(job_id)
        if meta is None or _status_value(meta.get("status")) not in _UNFINISHED:
            return
        meta.update({"status": JobStatus.FAILED.value, "finished_at": _now_iso(), "error": reason})
        snapshot = dict(meta)
    _write_metadata(job_id, snapshot)
    try:
        with open(_job_dir(job_id) / "stderr.log", "a", encoding="utf-8") as f:
            f.write(f"\n[ERROR] {reason}\n")
            runner_log = _job_dir(job_id) / "runner.log"
            if runner_log.exists():
                f.write(runner_log.read_text(encoding="utf-8", errors="replace"))
    except Exception as exc:
        logger.warning("stderr.log の書き込みに失敗: %s", exc)
    logger.warning("Job %s を failed にしました: %s", job_id, reason)


def _process_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


# ---------------------------------------------------------------------------
# 公開 API
# ---------------------------------------------------------------------------


def create_job(tool_name: str, input_files: list[str] | None = None) -> str:
    """
    新規ジョブを作成し job_id を返す。

    ディスクにジョブディレクトリと metadata.json を作成する。
    """
    try:
        import opensim
        opensim_version = opensim.GetVersionAndDate()
    except Exception:
        opensim_version = "unknown"

    job_id = str(uuid.uuid4())[:8]
    now = _now_iso()

    jdir = _job_dir(job_id)
    jdir.mkdir(parents=True, exist_ok=True)
    (jdir / "artifacts").mkdir(exist_ok=True)
    (jdir / "input_snapshot").mkdir(exist_ok=True)

    meta: dict[str, Any] = {
        "job_id": job_id,
        "tool_name": tool_name,
        "created_at": now,
        "started_at": None,
        "finished_at": None,
        "status": JobStatus.QUEUED,
        "input_files": input_files or [],
        "output_files": [],
        "opensim_version": opensim_version,
        "server_version": SERVER_VERSION,
    }
    _write_metadata(job_id, meta)

    with _lock:
        _jobs[job_id] = meta

    logger.info("Job created: %s (%s)", job_id, tool_name)
    return job_id


def run_job(
    job_id: str,
    tool_name_key: str,
    setup_xml_path: str,
) -> None:
    """
    OpenSim ツールを独立した子プロセスで非同期実行する。

    OpenSim の C++ バインディングはスレッドセーフでないため、
    ThreadPoolExecutor ではなくサブプロセスとして実行する。

    Parameters
    ----------
    job_id:
        create_job() で払い出された job_id。
    tool_name_key:
        "ik" | "analyze" のいずれか。
    setup_xml_path:
        setup XML の絶対パス。
    """
    jdir = _job_dir(job_id)
    run_info = {
        "job_id": job_id,
        "tool_name_key": tool_name_key,
        "setup_xml_path": setup_xml_path,
        "job_dir": str(jdir),
        "workspace_dir": str(WORKSPACE_DIR),
    }
    with open(jdir / "run_info.json", "w", encoding="utf-8") as f:
        json.dump(run_info, f, indent=2)

    _update_metadata(job_id, {"status": JobStatus.QUEUED})

    runner_script = Path(__file__).parent / "_job_runner.py"
    # 実行プロセスの出力は runner.log に残す。OpenSim は失敗の理由をコンソール(stdout)に出し、
    # segfault しても faulthandler の出力(stderr)で落ちた場所がわかる
    with open(jdir / "runner.log", "w", encoding="utf-8") as runner_log:
        proc = subprocess.Popen(
            [sys.executable, "-X", "faulthandler", str(runner_script), str(jdir / "run_info.json")],
            stdout=runner_log,
            stderr=subprocess.STDOUT,
            close_fds=True,
        )
    logger.info("Job subprocess started: %s (pid=%s)", job_id, proc.pid)

    def _wait() -> None:
        rc = proc.wait()
        _load_from_disk_force(job_id)
        _fail_if_unfinished(job_id, f"ジョブの実行プロセスが状態を書かずに終了しました(終了コード {rc})")
        logger.info("Job subprocess finished: %s (rc=%s)", job_id, rc)

    threading.Thread(target=_wait, daemon=True).start()


def get_status(job_id: str) -> dict[str, Any]:
    """
    ジョブのメタデータを返す。

    Raises
    ------
    ValueError
        job_id が存在しない場合。
    """
    _load_from_disk(job_id)
    with _lock:
        meta = _jobs.get(job_id)
    if meta is None:
        raise ValueError(f"job_id '{job_id}' が見つかりません。")

    if _status_value(meta.get("status")) in _UNFINISHED:
        # 進行状況は実行プロセスがディスクに書くので読み直す。
        # サーバー再起動後などで見張りのスレッドがいなくても、プロセスが死んでいれば failed にする
        _load_from_disk_force(job_id)
        with _lock:
            meta = _jobs.get(job_id, meta)
        pid = meta.get("pid")
        if _status_value(meta.get("status")) == JobStatus.RUNNING.value and pid and not _process_alive(int(pid)):
            _fail_if_unfinished(job_id, f"ジョブの実行プロセス(pid {pid})が見つかりません")
            with _lock:
                meta = _jobs.get(job_id, meta)
    result = dict(meta)
    result["status"] = _status_value(result.get("status"))
    return result


def get_log(job_id: str, stream: str = "stdout") -> str:
    """
    stdout / stderr / summary いずれかのログを返す。
    ファイルが存在しない場合は空文字列を返す。
    """
    jdir = _job_dir(job_id)
    if stream == "summary":
        log_file = jdir / "summary.json"
    else:
        log_file = jdir / f"{stream}.log"

    if not log_file.exists():
        return ""
    return log_file.read_text(encoding="utf-8")


def get_outputs(job_id: str) -> list[dict[str, Any]]:
    """成果物ファイル一覧を返す。"""
    meta = get_status(job_id)
    output_files = meta.get("output_files") or []
    resolved_outputs: list[dict[str, Any]] = []
    for rel_path in output_files:
        p = WORKSPACE_DIR / rel_path
        if p.exists() and p.is_file():
            resolved_outputs.append({
                "path": str(p.relative_to(WORKSPACE_DIR)),
                "type": p.suffix.lstrip("."),
                "size_bytes": p.stat().st_size,
            })
    if resolved_outputs:
        return resolved_outputs

    artifacts_dir = _job_dir(job_id) / "artifacts"
    if not artifacts_dir.exists():
        return []
    return [
        {
            "path": str(p.relative_to(WORKSPACE_DIR)),
            "type": p.suffix.lstrip("."),
            "size_bytes": p.stat().st_size,
        }
        for p in sorted(artifacts_dir.iterdir())
        if p.is_file()
    ]
