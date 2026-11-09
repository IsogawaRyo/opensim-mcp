"""
MCP E2E テスト: ExternalLoads + ID + SO

検証内容:
  1. OpenSim サンプル gait データを workspace に配置
  2. write_external_loads_xml で GRF XML を生成
  3. write_setup_xml(tool_type="id" / "so") で setup XML を生成
  4. run_inverse_dynamics / run_static_optimization を MCP 経由で実行
  5. get_job_outputs が実成果物を返すことを確認
"""

from __future__ import annotations

import asyncio
import os
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

import server

WORKSPACE = ROOT / "workspace"


def _find_sample_root() -> Path:
    """OpenSim GUI 付属の Gait10dof18musc を探す(OPENSIM_MODELS_DIR → ~/Documents/OpenSim/<版>/Models の新しい順)。"""
    env = os.environ.get("OPENSIM_MODELS_DIR")
    candidates = ([Path(env)] if env else []) + sorted(Path.home().glob("Documents/OpenSim/*/Models"), reverse=True)
    for models_dir in candidates:
        if (models_dir / "Gait10dof18musc" / "subject01.osim").exists():
            return models_dir / "Gait10dof18musc"
    raise SystemExit("OpenSim GUI のサンプル Gait10dof18musc が見つかりません。"
                     "環境変数 OPENSIM_MODELS_DIR に …/Documents/OpenSim/<版>/Models を指定してください")


SAMPLE_ROOT = _find_sample_root()

MODEL_PATH = "models/original/gait10dof18musc/subject01.osim"
IK_PATH = "data/mot/gait10dof18musc/subject01_walk_IK.mot"
GRF_MOT_PATH = "data/mot/gait10dof18musc/subject01_walk_grf.mot"
LOADS_XML_PATH = "setups/loads/gait10dof18musc/subject01_walk_grf.xml"
ID_SETUP_PATH = "setups/id/gait10dof18musc/subject01_walk_id_setup_e2e.xml"
SO_SETUP_PATH = "setups/analyze/gait10dof18musc/subject01_walk_so_setup_e2e.xml"
ID_OUTPUT_PATH = "results/gait10dof18musc/id/subject01_walk_id_e2e.sto"
ID_BODY_FORCES_PATH = "results/gait10dof18musc/id/subject01_walk_body_forces_e2e.sto"
SO_RESULTS_DIR = "results/gait10dof18musc/so_e2e"


def _banner(msg: str) -> None:
    print(f"\n{'=' * 60}\n {msg}\n{'=' * 60}")


def _workspace_path(relative_path: str) -> Path:
    return WORKSPACE / relative_path


def _prepare_workspace_files() -> None:
    copies = [
        (SAMPLE_ROOT / "subject01.osim", _workspace_path(MODEL_PATH)),
        (SAMPLE_ROOT / "IK" / "subject01_walk_IK.mot", _workspace_path(IK_PATH)),
        (SAMPLE_ROOT / "subject01_walk_grf.mot", _workspace_path(GRF_MOT_PATH)),
    ]
    for src, dst in copies:
        assert src.exists(), f"サンプルファイルが見つかりません: {src}"
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)

    for rel_dir in [
        "setups/loads/gait10dof18musc",
        "setups/id/gait10dof18musc",
        "setups/analyze/gait10dof18musc",
        "results/gait10dof18musc/id",
        SO_RESULTS_DIR,
    ]:
        _workspace_path(rel_dir).mkdir(parents=True, exist_ok=True)


def _unwrap_tool_result(result: tuple) -> dict:
    return result[1]


async def _call_tool(name: str, arguments: dict) -> dict:
    result = await server.mcp.call_tool(name, arguments)
    data = _unwrap_tool_result(result)
    print(f"[{name}] {data['status']}: {data['summary']}")
    return data


async def _wait_job(job_id: str) -> str:
    while True:
        data = await _call_tool("get_job_status", {"job_id": job_id})
        status = data["structured_data"]["status"]
        if status in {"completed", "failed", "cancelled"}:
            return status
        await asyncio.sleep(1.0)


async def main() -> None:
    _banner("MCP E2E: ExternalLoads + ID + SO")
    _prepare_workspace_files()

    await _call_tool("load_model", {"model_path": MODEL_PATH})

    await _call_tool(
        "write_external_loads_xml",
        {
            "params": {
                "name": "walk_grf",
                "datafile": GRF_MOT_PATH,
                "external_loads_model_kinematics_file": IK_PATH,
                "lowpass_cutoff_frequency_for_load_kinematics": 6.0,
                "external_loads": [
                    {
                        "name": "Right_GRF",
                        "applied_to_body": "calcn_r",
                        "force_expressed_in_body": "ground",
                        "point_expressed_in_body": "ground",
                        "force_identifier": "ground_force_v",
                        "point_identifier": "ground_force_p",
                        "torque_identifier": "ground_torque_",
                    },
                    {
                        "name": "Left_GRF",
                        "applied_to_body": "calcn_l",
                        "force_expressed_in_body": "ground",
                        "point_expressed_in_body": "ground",
                        "force_identifier": "1_ground_force_v",
                        "point_identifier": "1_ground_force_p",
                        "torque_identifier": "1_ground_torque_",
                    },
                ],
            },
            "output_path": LOADS_XML_PATH,
        },
    )

    await _call_tool(
        "write_setup_xml",
        {
            "tool_type": "id",
            "params": {
                "tool_name": "subject01_id_e2e",
                "model_file": MODEL_PATH,
                "coordinates_file": IK_PATH,
                "external_loads_file": LOADS_XML_PATH,
                "output_gen_force_file": ID_OUTPUT_PATH,
                "output_body_forces_file": ID_BODY_FORCES_PATH,
                "joints_to_report_body_forces": ["All"],
                "time_range": [0.45, 1.61],
                "lowpass_cutoff_frequency_for_coordinates": 6.0,
            },
            "output_path": ID_SETUP_PATH,
        },
    )

    await _call_tool(
        "write_setup_xml",
        {
            "tool_type": "so",
            "params": {
                "tool_name": "subject01_so_e2e",
                "model_file": MODEL_PATH,
                "coordinates_file": IK_PATH,
                "external_loads_file": LOADS_XML_PATH,
                "results_directory": SO_RESULTS_DIR,
                "time_range": [0.45, 1.61],
                "lowpass_cutoff_frequency_for_coordinates": 6.0,
            },
            "output_path": SO_SETUP_PATH,
        },
    )

    id_run = await _call_tool("run_inverse_dynamics", {"setup_xml_path": ID_SETUP_PATH})
    id_job = id_run["structured_data"]["job_id"]
    id_status = await _wait_job(id_job)
    assert id_status == "completed", f"ID failed: {id_status}"
    id_outputs = await _call_tool("get_job_outputs", {"job_id": id_job})
    id_paths = [artifact["path"] for artifact in id_outputs["artifacts"]]
    expected_id_paths = {ID_OUTPUT_PATH, ID_BODY_FORCES_PATH}
    assert expected_id_paths.issubset(set(id_paths)), f"ID outputs missing: {id_paths}"

    so_run = await _call_tool("run_static_optimization", {"setup_xml_path": SO_SETUP_PATH})
    so_job = so_run["structured_data"]["job_id"]
    so_status = await _wait_job(so_job)
    assert so_status == "completed", f"SO failed: {so_status}"
    so_outputs = await _call_tool("get_job_outputs", {"job_id": so_job})
    so_paths = [artifact["path"] for artifact in so_outputs["artifacts"]]
    expected_so_suffixes = {
        f"{SO_RESULTS_DIR}/subject01_so_e2e_StaticOptimization_activation.sto",
        f"{SO_RESULTS_DIR}/subject01_so_e2e_StaticOptimization_force.sto",
    }
    assert expected_so_suffixes.issubset(set(so_paths)), f"SO outputs missing: {so_paths}"

    _banner("✅ MCP ExternalLoads E2E 完了")


if __name__ == "__main__":
    asyncio.run(main())
