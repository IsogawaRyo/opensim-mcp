"""
IK E2E テストスクリプト。

MCP の run_inverse_kinematics → get_job_status → summarize_results の
一連フローを直接呼び出して検証する。

使い方:
    python tests/run_ik_e2e_test.py
"""

import sys
import time
from pathlib import Path

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

from opensim_mcp.config import WORKSPACE_DIR
from opensim_mcp.job_service import get_log, get_outputs, get_status, create_job, run_job
from opensim_mcp.schemas import JobStatus

SETUP_XML = WORKSPACE_DIR / "setups" / "ik" / "arm26_ik_setup.xml"


def run_e2e_test() -> None:
    print("=" * 60)
    print(" IK E2E テスト")
    print("=" * 60)

    # ------------------------------------------------------------------
    # 1. Setup XML 確認
    # ------------------------------------------------------------------
    print("\n[1] Setup XML 確認...")
    if not SETUP_XML.exists():
        print(f"❌ Setup XML が見つかりません: {SETUP_XML}")
        sys.exit(1)
    print(f"   ✅ {SETUP_XML.name}")

    # ------------------------------------------------------------------
    # 2. ジョブ作成・実行（subprocess ベース）
    # ------------------------------------------------------------------
    print("\n[2] IK ジョブを投入...")
    job_id = create_job("run_inverse_kinematics", input_files=[str(SETUP_XML)])
    run_job(job_id, "ik", str(SETUP_XML))
    print(f"   ✅ job_id: {job_id}")


    # ------------------------------------------------------------------
    # 3. 完了まで待機（最大 60 秒）
    # ------------------------------------------------------------------
    print("\n[3] 実行中...")
    timeout = 60
    start = time.time()
    last_status = None

    while time.time() - start < timeout:
        meta = get_status(job_id)
        status = meta["status"]
        if status != last_status:
            elapsed = time.time() - start
            print(f"   [{elapsed:5.1f}s] status: {status}")
            last_status = status

        if status in (JobStatus.COMPLETED, JobStatus.FAILED):
            break
        time.sleep(0.5)

    # ------------------------------------------------------------------
    # 4. 結果確認
    # ------------------------------------------------------------------
    meta = get_status(job_id)
    status = meta["status"]
    elapsed = time.time() - start

    print(f"\n[4] 結果確認 (経過時間: {elapsed:.1f}s)")
    print(f"   status   : {status}")
    print(f"   started  : {meta.get('started_at')}")
    print(f"   finished : {meta.get('finished_at')}")

    if status == JobStatus.COMPLETED:
        outputs = get_outputs(job_id)
        print(f"\n   ✅ 成果物: {len(outputs)} 個")
        for o in outputs:
            print(f"      - {o['path']} ({o['size_bytes']:,} bytes)")

        stdout = get_log(job_id, "stdout")
        print(f"\n   stdout:\n{_indent(stdout, 6)}")

    elif status == JobStatus.FAILED:
        stderr = get_log(job_id, "stderr")
        summary = get_log(job_id, "summary")
        print(f"\n   ❌ IK 失敗")
        print(f"   stderr:\n{_indent(stderr, 6)}")
        print(f"   summary:\n{_indent(summary, 6)}")
        sys.exit(1)

    else:
        print(f"\n   ⚠️  タイムアウト ({timeout}s)")
        sys.exit(1)

    # ------------------------------------------------------------------
    # 5. inspect_timeseries_file テスト（結果 .mot を読む）
    # ------------------------------------------------------------------
    print("\n[5] 結果ファイルの inspect_timeseries_file テスト...")
    results_mot = WORKSPACE_DIR / "jobs" / "ik_test_results" / "arm26_ik_result.mot"
    if results_mot.exists():
        from opensim_mcp.tools.file_tools import _inspect_storage
        result = _inspect_storage(results_mot)
        sd = result.get("structured_data", {})
        print(f"   ✅ IK 結果 mot: {sd.get('num_rows')} 行 × {sd.get('num_columns')} 列")
        print(f"      時刻範囲: {sd.get('time_range')}")
        print(f"      列名: {sd.get('column_names')}")
    else:
        print(f"   ⚠️  結果ファイルが見つかりません: {results_mot}")

    print("\n" + "=" * 60)
    print(" ✅ IK E2E テスト完了")
    print("=" * 60)


def _indent(text: str, n: int) -> str:
    pad = " " * n
    return "\n".join(pad + line for line in text.splitlines()) if text.strip() else f"{' ' * n}(空)"


if __name__ == "__main__":
    run_e2e_test()
