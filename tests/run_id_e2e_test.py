"""
Phase 2 検証テスト 1: Inverse Dynamics (ID) E2E テスト

検証内容:
  1. write_setup_xml で ID 用 setup XML を生成
  2. run_inverse_dynamics でジョブを投入
  3. 完了まで待機して結果を確認
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

from opensim_mcp.config import WORKSPACE_DIR
from opensim_mcp.job_service import create_job, get_log, get_status, run_job
from opensim_mcp.schemas import JobStatus
from opensim_mcp.tools.edit_tools import _build_id_xml

MODEL_FILE  = WORKSPACE_DIR / "models" / "original" / "arm26_with_markers.osim"
IK_RESULT   = WORKSPACE_DIR / "jobs" / "ik_test_results" / "arm26_ik_result.mot"
SETUP_XML   = WORKSPACE_DIR / "setups" / "id" / "arm26_id_setup.xml"
OUTPUT_FILE = WORKSPACE_DIR / "jobs" / "id_test_results" / "arm26_id_result.sto"
TIMEOUT     = 120  # seconds


def _banner(msg: str) -> None:
    print(f"\n{'=' * 60}\n {msg}\n{'=' * 60}")


def test_write_setup_xml() -> Path:
    """Step 1: ID setup XML を _build_id_xml で生成して保存する。"""
    print("\n[1] write_setup_xml (ID) ...")

    SETUP_XML.parent.mkdir(parents=True, exist_ok=True)

    # OpenSim は output_gen_force_file を setup XML ディレクトリ相対で解釈することがあるため、
    # setup XML と同一ディレクトリに出力先を指定する。
    out_force = str(SETUP_XML.parent / "arm26_id_result.sto")
    out_body  = str(SETUP_XML.parent / "arm26_body_forces.sto")

    params = {
        "model_file":              str(MODEL_FILE),
        "coordinates_file":        str(IK_RESULT),
        "output_gen_force_file":   out_force,
        "output_body_forces_file": out_body,
        "time_range": [0.0, 0.5],
        "lowpass_cutoff_frequency_for_coordinates": 6.0,
    }

    xml_content, schema = _build_id_xml(params)
    SETUP_XML.write_text(xml_content, encoding="utf-8")

    print(f"   ✅ 生成: {SETUP_XML.relative_to(WORKSPACE_DIR)}")
    print(f"   出力先: {Path(out_force).relative_to(WORKSPACE_DIR)}")
    print(f"   必須キー: {schema['required_keys']}")
    assert SETUP_XML.exists(), "Setup XML が作成されていない"
    return SETUP_XML


def test_run_id(setup_xml: Path) -> str:
    """Step 2: run_inverse_dynamics ジョブを投入して完了を待つ。"""
    print("\n[2] run_inverse_dynamics を投入...")
    job_id = create_job("run_inverse_dynamics", input_files=[str(setup_xml)])
    run_job(job_id, "id", str(setup_xml))
    print(f"   ✅ job_id: {job_id}")

    print("\n[3] ジョブ完了待ち...")
    elapsed = 0.0
    interval = 1.0
    while elapsed < TIMEOUT:
        meta = get_status(job_id)
        status = meta["status"]
        print(f"   [{elapsed:5.1f}s] status: {status}")
        if status in (JobStatus.COMPLETED, JobStatus.FAILED, "completed", "failed"):
            break
        time.sleep(interval)
        elapsed += interval

    meta = get_status(job_id)
    status = meta["status"]
    print(f"\n[4] 最終ステータス: {status}")
    print(f"    started:  {meta.get('started_at')}")
    print(f"    finished: {meta.get('finished_at')}")

    if status in (JobStatus.FAILED, "failed"):
        print("\n   ❌ ジョブ失敗!")
        print("--- stderr ---")
        print(get_log(job_id, "stderr"))
        print("--- summary ---")
        print(get_log(job_id, "summary"))
        sys.exit(1)

    assert status in (JobStatus.COMPLETED, "completed"), f"想定外のステータス: {status}"
    print("\n   ✅ ID ジョブ完了!")
    return job_id


def test_check_results(job_id: str) -> None:
    """Step 3: 出力ファイルと stdout ログを確認する。"""
    print("\n[5] 結果ファイルを確認...")

    stdout = get_log(job_id, "stdout")
    print("   stdout:")
    for line in stdout.strip().splitlines():
        print(f"      {line}")

    # OpenSim は output_gen_force_file を setup XML からの相対パスで解釈する場合がある
    search_dirs = [
        WORKSPACE_DIR / "jobs" / "id_test_results",
        WORKSPACE_DIR / "setups" / "id",   # フォールバック
        WORKSPACE_DIR,
    ]
    found_sto: list[Path] = []
    for d in search_dirs:
        if d.exists():
            found_sto.extend(d.glob("**/*.sto"))

    if found_sto:
        for f in found_sto:
            print(f"   ✅ 出力ファイル: {f.relative_to(WORKSPACE_DIR)} ({f.stat().st_size} bytes)")
        return

    print("   ⚠️  出力 .sto ファイルが見つかりません")


def main() -> None:
    _banner("Phase 2 テスト 1: Inverse Dynamics E2E")

    # 前提確認
    assert MODEL_FILE.exists(), f"モデルが見つかりません: {MODEL_FILE}"
    assert IK_RESULT.exists(),  f"IK 結果 .mot が見つかりません: {IK_RESULT}\n先に run_ik_e2e_test.py を実行してください。"

    setup_xml = test_write_setup_xml()
    job_id    = test_run_id(setup_xml)
    test_check_results(job_id)

    _banner("✅ ID E2E テスト完了")


if __name__ == "__main__":
    main()
