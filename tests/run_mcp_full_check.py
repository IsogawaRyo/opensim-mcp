"""
MCP 全機能テスト: 全 Tools / Prompts / Resources を MCP(stdio)経由で実際に呼ぶ

検証内容:
  basic    : 閲覧・編集・構造編集の各ツール、パス制約、元ファイルの上書き防止、TRC / C3D の読み込み
  jobs     : write_setup_xml → IK / ID / SO / Analyze / Scale のジョブ実行と成果物、
             壊れた setup や OpenSim が落ちる setup が failed になること
  dynamics : RRA / CMC(OpenSim 付属の setup)と、CMC の結果を使った FD
  moco     : MocoInverse / MocoTrack / MocoStudy(custom)の作成・設定・求解・要約
  meta     : 全 Resources と 6 Prompts
  security : workspace の外を読み書きさせない(../ を含む名前・ID・Resource、外を指す setup XML)
  最後に   : テスト前後で元ファイル(models/original と samples)が変わっていないこと

workspace は毎回一時フォルダに新しく組み立てる(リポジトリの workspace/ と本番の workspace には触れない)。
データの入手先:
  - OpenSim Python パッケージ同梱のテストデータ(arm26.osim / storage.sto / walking2.c3d など)
  - OpenSim GUI 付属のサンプル ~/Documents/OpenSim/<版>/Models/Gait10dof18musc
    (--models-dir か環境変数 OPENSIM_MODELS_DIR で指定可。見つからなければ jobs / dynamics / moco はスキップ)

使い方:
    python tests/run_mcp_full_check.py                    # 全フェーズ(1〜数分)
    python tests/run_mcp_full_check.py --phases basic     # 一部だけ(前提のフェーズは自動で含める)
    python tests/run_mcp_full_check.py --keep             # 一時 workspace を残す(調査用)

終了コード: 全項目通過なら 0、1 つでも失敗があれば 1。
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import opensim

ROOT = Path(__file__).parent.parent
SERVER = ROOT / "server.py"
OPENSIM_TESTS = Path(opensim.__file__).parent / "tests"

PHASES = ["basic", "jobs", "dynamics", "moco", "meta", "security"]
DEPENDS = {"moco": ["jobs"], "meta": ["jobs"]}
NEEDS_SAMPLES = {"jobs", "dynamics", "moco"}

G = "samples/Gait10dof18musc"          # workspace 内のサンプル置き場
IK_OUT = "results/check/walk_ik.mot"   # jobs の IK 結果(moco が使う)


# ---------------------------------------------------------------------------
# 結果の記録
# ---------------------------------------------------------------------------

results: list[dict] = []


def _record(name: str, ok: bool, detail: str = "") -> None:
    results.append({"check": name, "ok": ok, "detail": detail})
    print(f"   {'✅' if ok else '❌'} {name}" + (f" :: {detail[:150]}" if detail else ""), flush=True)


def check(name: str, r: dict, expect: str | tuple = "completed") -> dict:
    st = r.get("status")
    ok = st == expect if isinstance(expect, str) else st in expect
    _record(name, ok, str(r.get("summary", "")))
    return r


def _banner(msg: str) -> None:
    print(f"\n{'=' * 60}\n {msg}\n{'=' * 60}", flush=True)


# ---------------------------------------------------------------------------
# 最小 MCP stdio クライアント
# ---------------------------------------------------------------------------


class Client:
    """server.py を子プロセスで起動し、JSON-RPC を 1 行ずつやり取りする。"""

    def __init__(self, ws: Path, log_name: str):
        env = dict(os.environ, OPENSIM_MCP_WORKSPACE=str(ws))
        self._err = open(ws.parent / f"server_{log_name}.log", "w", encoding="utf-8")
        self.p = subprocess.Popen([sys.executable, str(SERVER)], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                  stderr=self._err, env=env, text=True)
        self.i = 0
        self.nonjson = 0
        self.rpc("initialize", {"protocolVersion": "2025-06-18", "capabilities": {},
                                "clientInfo": {"name": "run_mcp_full_check", "version": "1"}})
        self._send({"jsonrpc": "2.0", "method": "notifications/initialized"})

    def _send(self, msg: dict) -> None:
        self.p.stdin.write(json.dumps(msg) + "\n")
        self.p.stdin.flush()

    def rpc(self, method: str, params: dict) -> dict:
        self.i += 1
        self._send({"jsonrpc": "2.0", "id": self.i, "method": method, "params": params})
        while True:
            line = self.p.stdout.readline()
            if not line:
                raise RuntimeError("MCP サーバーが終了しました")
            if not line.startswith("{"):
                self.nonjson += 1   # stdout に JSON 以外が混ざった(通信路の汚染)
                continue
            msg = json.loads(line)
            if msg.get("id") == self.i:
                return msg

    def tool(self, name: str, **args) -> dict:
        msg = self.rpc("tools/call", {"name": name, "arguments": args})
        if "error" in msg:
            return {"status": "rpc_error", "summary": str(msg["error"])}
        text = msg["result"]["content"][0]["text"]
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            return {"status": "raw", "summary": text[:300]}

    def resource(self, uri: str) -> dict:
        msg = self.rpc("resources/read", {"uri": uri})
        if "error" in msg:
            return {"status": "rpc_error", "summary": str(msg["error"])}
        try:
            return json.loads(msg["result"]["contents"][0]["text"])
        except (json.JSONDecodeError, KeyError):
            return {"status": "raw", "summary": str(msg["result"])[:300]}

    def prompt(self, name: str, args: dict) -> dict:
        msg = self.rpc("prompts/get", {"name": name, "arguments": args})
        if "error" in msg:
            return {"status": "rpc_error", "summary": str(msg["error"])}
        texts = [m["content"]["text"] for m in msg["result"]["messages"]]
        return {"status": "completed", "summary": f"{len(texts)} messages, {sum(map(len, texts))} 文字"}

    def wait_job(self, job_id: str, timeout: float = 900) -> tuple[str, float]:
        t0 = time.time()
        while time.time() - t0 < timeout:
            st = (self.tool("get_job_status", job_id=job_id).get("structured_data") or {}).get("status", "")
            if st in ("completed", "failed", "cancelled"):
                return st, round(time.time() - t0, 1)
            time.sleep(1.0)
        return "timeout", timeout

    def close(self) -> None:
        self.p.stdin.close()
        self.p.wait(timeout=60)
        self._err.close()
        _record("stdout は JSON のみ(通信路が汚れていない)", self.nonjson == 0, f"JSON 以外の行 {self.nonjson}")


def run_job(c: Client, tool: str, setup: str, name: str, timeout: float = 900) -> str | None:
    """ジョブを投入し、完了・成果物・ログ・要約まで確認する。"""
    r = check(f"{tool} 受付", c.tool(tool, setup_xml_path=setup, job_name=name), expect=("accepted", "queued"))
    job_id = (r.get("structured_data") or {}).get("job_id")
    if not job_id:
        return None
    st, sec = c.wait_job(job_id, timeout)
    _record(f"{tool} ジョブ完了", st == "completed", f"{st} ({sec} s)")
    n = (c.tool("get_job_outputs", job_id=job_id).get("structured_data") or {}).get("count", 0)
    _record(f"{tool} 今回の成果物が 1 個以上", n > 0, f"{n} 個")
    check(f"{tool} get_job_log", c.tool("get_job_log", job_id=job_id))
    check(f"{tool} summarize_results", c.tool("summarize_results", job_id=job_id))
    return job_id if st == "completed" else None


def expect_job_failed(c: Client, name: str, r: dict, timeout: float = 120) -> None:
    job_id = (r.get("structured_data") or {}).get("job_id")
    st, sec = c.wait_job(job_id, timeout) if job_id else ("ジョブが作られなかった", 0)
    _record(name, st == "failed", f"{st} ({sec} s)")
    if job_id:
        check(f"{name}: stderr ログが残る", c.tool("get_job_log", job_id=job_id, stream="stderr"))


# ---------------------------------------------------------------------------
# workspace の準備
# ---------------------------------------------------------------------------


def find_models_dir(arg: str | None) -> Path | None:
    """Gait10dof18musc を含む OpenSim GUI のサンプル置き場を探す(新しい版を優先)。"""
    candidates = [Path(arg)] if arg else []
    if os.environ.get("OPENSIM_MODELS_DIR"):
        candidates.append(Path(os.environ["OPENSIM_MODELS_DIR"]))
    candidates += sorted(Path.home().glob("Documents/OpenSim/*/Models"), reverse=True)
    return next((d for d in candidates if (d / "Gait10dof18musc" / "subject01.osim").exists()), None)


def prepare_workspace(ws: Path, models_dir: Path | None) -> None:
    for d in ["models/original", "models/clones", "data/trc", "data/mot", "data/sto", "data/c3d", "jobs"]:
        (ws / d).mkdir(parents=True, exist_ok=True)
    for src, dst in [("arm26.osim", "models/original/arm26.osim"),
                     ("gait10dof18musc_subject01.osim", "models/original/gait10dof18musc_subject01.osim"),
                     ("dataWithNaNsOfDifferentCases.trc", "data/trc/markers.trc"),
                     ("std_subject01_walk1_ik.mot", "data/mot/walk1_ik.mot"),
                     ("storage.sto", "data/sto/storage.sto"),
                     ("walking2.c3d", "data/c3d/walking2.c3d")]:
        shutil.copy2(OPENSIM_TESTS / src, ws / dst)
    # 「models/original 以外にある元ファイル」の上書き防止を試すための置き場
    (ws / "data/models").mkdir(parents=True, exist_ok=True)
    shutil.copy2(OPENSIM_TESTS / "gait10dof18musc_subject01.osim", ws / "data/models/subject01.osim")

    if models_dir is None:
        return
    gdir = ws / G
    shutil.copytree(models_dir / "Gait10dof18musc", gdir)
    # 付属の RRA / CMC setup は model_file が空(GUI が補う前提)なので埋める。時間短縮のため区間も短くする
    rra = gdir / "RRA/walk_Setup_RRA.xml"
    s = rra.read_text()
    s = re.sub(r"<model_file\s*/>|<model_file></model_file>", "<model_file>../subject01.osim</model_file>", s)
    s = re.sub(r"<initial_time>[^<]*</initial_time>", "<initial_time>0.6</initial_time>", s)
    s = re.sub(r"<final_time>[^<]*</final_time>", "<final_time>1.0</final_time>", s)
    rra.write_text(s)
    cmc = gdir / "walk_Setup_CMC.xml"
    s = cmc.read_text()
    s = re.sub(r"<model_file\s*/>|<model_file></model_file>", "<model_file>subject01.osim</model_file>", s)
    s = re.sub(r"<initial_time>[^<]*</initial_time>", "<initial_time>0.6</initial_time>", s)
    s = re.sub(r"<final_time>[^<]*</final_time>", "<final_time>0.9</final_time>", s)
    cmc.write_text(s)
    # CMC の結果(制御・状態)で前進動力学を回す setup
    (gdir / "check_Setup_Forward.xml").write_text("""<?xml version="1.0" encoding="UTF-8" ?>
<OpenSimDocument Version="40000">
	<ForwardTool name="check_fd">
		<model_file>subject01.osim</model_file>
		<replace_force_set>false</replace_force_set>
		<force_set_files>CMC/gait10dof_Reserve_Actuators.xml</force_set_files>
		<results_directory>ResultsForwardCheck</results_directory>
		<initial_time>0.65</initial_time>
		<final_time>0.75</final_time>
		<solve_for_equilibrium_for_auxiliary_states>false</solve_for_equilibrium_for_auxiliary_states>
		<external_loads_file>subject01_walk_grf.xml</external_loads_file>
		<controls_file>CMC/ResultsCMC/walk_subject_controls.xml</controls_file>
		<states_file>CMC/ResultsCMC/walk_subject_states.sto</states_file>
	</ForwardTool>
</OpenSimDocument>
""")


def hash_originals(ws: Path) -> dict[str, str]:
    """元データ(models/original と samples。解析結果フォルダは除く)のハッシュ。"""
    out = {}
    for base in ["models/original", "samples", "data/models"]:
        for p in sorted((ws / base).rglob("*")):
            if p.is_file() and p.suffix in {".osim", ".trc", ".mot", ".xml"} and "Results" not in str(p):
                out[str(p.relative_to(ws))] = hashlib.sha1(p.read_bytes()).hexdigest()
    return out


# ---------------------------------------------------------------------------
# フェーズ
# ---------------------------------------------------------------------------


def phase_basic(c: Client, ws: Path) -> None:
    print("[閲覧系]")
    check("load_model arm26", c.tool("load_model", model_path="models/original/arm26.osim"))
    check("load_model 存在しないファイル → 失敗", c.tool("load_model", model_path="models/original/nope.osim"), "failed")
    check("load_model workspace 外 → 拒否", c.tool("load_model", model_path="../../../../etc/hosts.osim"), "failed")
    (ws / "models/original/broken.osim").write_text("<OpenSimDocument><Model name='x'><BodySet>")
    check("load_model 壊れた XML → 失敗", c.tool("load_model", model_path="models/original/broken.osim"), "failed")
    (ws / "models/original/broken.osim").unlink()
    check("load_model gait10dof18musc", c.tool("load_model", model_path="models/original/gait10dof18musc_subject01.osim"))
    check("get_model_summary", c.tool("get_model_summary"))
    for t in ["bodies", "joints", "coordinates", "muscles", "forces", "markers", "controllers", "analyses", "probes"]:
        check(f"list_components {t}", c.tool("list_components", component_type=t))
    check("list_components 不正な種類 → 失敗", c.tool("list_components", component_type="widgets"), "failed")
    check("get_component_properties 名前", c.tool("get_component_properties", component_name="femur_r"))
    check("get_component_properties フルパス", c.tool("get_component_properties", component_name="/jointset/hip_r/hip_flexion_r"))
    check("validate_model", c.tool("validate_model"))
    for f in ["data/trc/markers.trc", "data/mot/walk1_ik.mot", "data/sto/storage.sto", "data/c3d/walking2.c3d"]:
        check(f"inspect_timeseries_file {Path(f).suffix}", c.tool("inspect_timeseries_file", file_path=f))
    check("list_workspace_files", c.tool("list_workspace_files"))
    check("list_workspace_files 絞り込み", c.tool("list_workspace_files", subdir="models", extensions=[".osim"]))

    print("[編集・保存系]")
    check("set_component_property temp", c.tool("set_component_property", component_name="femur_r", property_name="mass",
                                                value=10.0, save_mode="temp"))
    check("set_component_property copy(double)", c.tool("set_component_property", component_name="hip_flexion_r",
                                                        property_name="default_value", value=0.3, save_mode="copy",
                                                        output_path="models/clones/prop_copy.osim"))
    check("set_component_property リスト値(range)", c.tool("set_component_property", component_name="hip_flexion_r",
                                                          property_name="range", value=[-0.5, 1.5], save_mode="temp"))
    check("set_component_property 無い property → 失敗", c.tool("set_component_property", component_name="femur_r",
                                                              property_name="no_such_prop", value=1, save_mode="temp"), "failed")
    check("set_component_property copy で models/original へ → 拒否", c.tool(
        "set_component_property", component_name="femur_r", property_name="mass", value=9.0, save_mode="copy",
        output_path="models/original/x.osim"), "failed")
    check("save_model_copy", c.tool("save_model_copy", output_path="models/clones/saved_copy.osim"))
    check("save_model_copy models/original へ → 拒否", c.tool("save_model_copy", output_path="models/original/new.osim"), "failed")
    check("load_model models/original の外にある元ファイル", c.tool("load_model", model_path="data/models/subject01.osim"))
    check("save_model_copy 読み込み元への上書き → 拒否", c.tool("save_model_copy", output_path="data/models/subject01.osim"), "failed")
    check("clone_model", c.tool("clone_model", source_model_path="models/original/arm26.osim", new_name="arm26_clone"))
    check("clone_model 同名 → 失敗", c.tool("clone_model", source_model_path="models/original/arm26.osim", new_name="arm26_clone"), "failed")
    check("convert_c3d_to_trc_mot", c.tool("convert_c3d_to_trc_mot", c3d_path="data/c3d/walking2.c3d", output_dir="data/converted"))
    check("convert_c3d_to_trc_mot 回転あり", c.tool("convert_c3d_to_trc_mot", c3d_path="data/c3d/walking2.c3d",
                                                  output_dir="data/converted_rot", rotate_data=True))
    check("変換後の .trc を要約", c.tool("inspect_timeseries_file", file_path="data/converted_rot/walking2_markers.trc"))
    check("変換後の力 .mot を要約", c.tool("inspect_timeseries_file", file_path="data/converted_rot/walking2_forces.mot"))
    check("convert_c3d_to_trc_mot models/original へ → 拒否", c.tool("convert_c3d_to_trc_mot", c3d_path="data/c3d/walking2.c3d",
                                                                 output_dir="models/original/x"), "failed")

    print("[構造編集系]")
    clone, added = "models/clones/arm26_clone.osim", "models/clones/arm26_added.osim"
    check("add_component CoordinateActuator", c.tool("add_component", target_model_path=clone, component_kind="CoordinateActuator",
                                                     component_spec={"name": "act_shoulder", "coordinate": "r_shoulder_elev", "optimal_force": 500.0},
                                                     output_model_path=added))
    check("add_component PointActuator", c.tool("add_component", target_model_path=added, component_kind="PointActuator",
                                                component_spec={"name": "pa", "body": "r_humerus", "optimal_force": 10.0},
                                                output_model_path="models/clones/arm26_added2.osim"))
    check("add_component PathActuator", c.tool("add_component", target_model_path=clone, component_kind="PathActuator",
                                               component_spec={"name": "path_act", "optimal_force": 50.0, "path_points": [
                                                   {"body": "r_humerus", "location": [0.0, -0.05, 0.0]},
                                                   {"body": "r_ulna_radius_hand", "location": [0.0, -0.05, 0.0]}]},
                                               output_model_path="models/clones/arm26_path.osim"))
    check("add_component PathActuator 点が足りない → 失敗", c.tool("add_component", target_model_path=clone, component_kind="PathActuator",
                                                              component_spec={"name": "p2"}, output_model_path="models/clones/x.osim"), "failed")
    check("add_component 解析 → .osim に保存できない旨のエラー", c.tool("add_component", target_model_path=clone, component_kind="BodyKinematics",
                                                                 component_spec={"name": "bk"}, output_model_path="models/clones/x.osim"), "failed")
    check("add_component 未対応の種類 → 失敗", c.tool("add_component", target_model_path=clone, component_kind="Banana",
                                                     component_spec={"name": "x"}, output_model_path="models/clones/x.osim"), "failed")
    check("remove_component 追加したアクチュエータ", c.tool("remove_component", target_model_path="models/clones/arm26_added2.osim",
                                                           component_name="pa", output_model_path="models/clones/arm26_removed.osim"))
    check("remove_component 元からある筋", c.tool("remove_component", target_model_path=clone, component_name="TRIlong",
                                                  output_model_path="models/clones/arm26_noTRIlong.osim"))
    check("add_component clones は上書き保存できる", c.tool("add_component", target_model_path="models/clones/arm26_removed.osim",
                                                         component_kind="CoordinateActuator", component_spec={"name": "act_elbow", "coordinate": "r_elbow_flex"},
                                                         output_model_path="models/clones/arm26_removed.osim"))
    check("set_socket_connection 名前", c.tool("set_socket_connection", target_model_path=added, component_name="r_shoulder",
                                              socket_name="parent_frame", connect_to="/ground", output_model_path="models/clones/arm26_socket.osim"))
    check("set_socket_connection フルパス", c.tool("set_socket_connection", target_model_path=added, component_name="/jointset/r_shoulder",
                                                  socket_name="parent_frame", connect_to="/ground", output_model_path="models/clones/arm26_socket2.osim"))
    check("set_socket_connection 存在しない接続先 → 失敗", c.tool("set_socket_connection", target_model_path=added, component_name="r_shoulder",
                                                              socket_name="parent_frame", connect_to="/bodyset/nothing",
                                                              output_model_path="models/clones/x.osim"), "failed")
    check("export_model", c.tool("export_model", target_model_ref="models/clones/arm26_removed.osim", output_path="exports/arm26.osim"))
    check("export_model models/original へ → 拒否", c.tool("export_model", target_model_ref="models/clones/arm26_removed.osim",
                                                          output_path="models/original/arm26.osim"), "failed")
    check("load_model 編集済みモデル", c.tool("load_model", model_path=added))
    check("validate_model 編集済みモデル", c.tool("validate_model"))


def phase_jobs(c: Client, ws: Path) -> None:
    print("[IK → ID / SO / Analyze]")
    tasks = re.findall(r'<IKMarkerTask name="([^"]+)">\s*<apply>true</apply>\s*<weight>([\d.]+)</weight>',
                       (ws / G / "IK/walk_Setup_IK.xml").read_text())
    # 出力先フォルダ(results/check)はまだ無い。無くても成果物が書かれること
    check("write_setup_xml ik", c.tool("write_setup_xml", tool_type="ik", output_path="setups/ik/walk_ik.xml", params={
        "model_file": f"{G}/subject01.osim", "marker_file": f"{G}/subject01_walk.trc",
        "output_motion_file": IK_OUT, "time_range": [0.5, 1.5],
        "markers": [{"name": n, "weight": float(w)} for n, w in tasks]}))
    run_job(c, "run_inverse_kinematics", "setups/ik/walk_ik.xml", "check_ik")
    check("write_external_loads_xml", c.tool("write_external_loads_xml", output_path="setups/loads/walk_grf.xml", params={
        "name": "walk_grf", "datafile": f"{G}/subject01_walk_grf.mot",
        "external_loads": [
            {"name": "Right_GRF", "applied_to_body": "calcn_r", "force_expressed_in_body": "ground", "point_expressed_in_body": "ground",
             "force_identifier": "ground_force_v", "point_identifier": "ground_force_p", "torque_identifier": "ground_torque_"},
            {"name": "Left_GRF", "applied_to_body": "calcn_l", "force_expressed_in_body": "ground", "point_expressed_in_body": "ground",
             "force_identifier": "1_ground_force_v", "point_identifier": "1_ground_force_p", "torque_identifier": "1_ground_torque_"}]}))
    common = {"model_file": f"{G}/subject01.osim", "coordinates_file": IK_OUT,
              "external_loads_file": "setups/loads/walk_grf.xml", "time_range": [0.6, 1.2]}
    check("write_setup_xml id", c.tool("write_setup_xml", tool_type="id", output_path="setups/id/walk_id.xml",
                                       params={**common, "output_gen_force_file": "results/check/walk_id.sto"}))
    run_job(c, "run_inverse_dynamics", "setups/id/walk_id.xml", "check_id")
    check("write_setup_xml so", c.tool("write_setup_xml", tool_type="so", output_path="setups/so/walk_so.xml",
                                       params={**common, "results_directory": "results/check/so"}))
    run_job(c, "run_static_optimization", "setups/so/walk_so.xml", "check_so")
    check("write_setup_xml analyze", c.tool("write_setup_xml", tool_type="analyze", output_path="setups/analyze/walk_an.xml",
                                            params={**common, "results_directory": "results/check/analyze",
                                                    "analyses": ["BodyKinematics", "MuscleAnalysis"]}))
    run_job(c, "run_analyze_tool", "setups/analyze/walk_an.xml", "check_analyze")

    print("[Scale]")
    check("write_setup_xml scale", c.tool("write_setup_xml", tool_type="scale", output_path="setups/scale/scale.xml", params={
        "model_file": f"{G}/subject01.osim", "marker_file": f"{G}/subject01_static.trc", "subject_mass": 72.6,
        "subject_name": "subject01", "output_model_file": "models/clones/subject01_scaled.osim", "time_range": [0.5, 1.5]}))
    run_job(c, "run_scale", "setups/scale/scale.xml", "check_scale")
    check("write_setup_xml scale 出力が元モデル → 拒否", c.tool("write_setup_xml", tool_type="scale", output_path="setups/scale/x.xml", params={
        "model_file": f"{G}/subject01.osim", "marker_file": f"{G}/subject01_static.trc",
        "output_model_file": f"{G}/subject01.osim"}), "failed")
    check("write_setup_xml 未対応の種類 → 失敗", c.tool("write_setup_xml", tool_type="cmc", params={}, output_path="setups/x.xml"), "failed")

    print("[失敗するジョブ]")
    (ws / "setups/ik/broken.xml").write_text("<OpenSimDocument><InverseKinematicsTool><model_file>nope.osim")
    expect_job_failed(c, "壊れた setup → failed", c.tool("run_inverse_kinematics", setup_xml_path="setups/ik/broken.xml"))
    # model_file の無い IK は OpenSim 自体が segfault する。running のまま残らず failed になること
    xml = (ws / "setups/ik/walk_ik.xml").read_text()
    (ws / "setups/ik/nomodel.xml").write_text(re.sub(r"<model_file>[^<]*</model_file>", "", xml))
    expect_job_failed(c, "OpenSim が落ちる setup → failed", c.tool("run_inverse_kinematics", setup_xml_path="setups/ik/nomodel.xml"))
    check("get_job_status 存在しない job → 失敗", c.tool("get_job_status", job_id="nope123"), "failed")


def phase_dynamics(c: Client, ws: Path) -> None:
    print("[RRA / CMC / FD]")
    run_job(c, "run_rra", f"{G}/RRA/walk_Setup_RRA.xml", "check_rra")
    if run_job(c, "run_cmc", f"{G}/walk_Setup_CMC.xml", "check_cmc"):
        run_job(c, "run_forward_dynamics", f"{G}/check_Setup_Forward.xml", "check_fd")
    else:
        _record("run_forward_dynamics(CMC の結果が必要)", False, "CMC が失敗したので実行できない")


def _solve_moco(c: Client, label: str, template: str, patch: dict, expect: str = "completed") -> None:
    r = check(f"create_moco_study {label}", c.tool("create_moco_study", model_path=f"{G}/subject01.osim",
                                                    template_type=template, study_name=f"check_{template}"))
    sid = (r.get("structured_data") or {}).get("study_id")
    check(f"configure_moco_problem {label}", c.tool("configure_moco_problem", study_id=sid, config_patch=patch))
    r = check(f"solve_moco_problem {label} 受付", c.tool("solve_moco_problem", study_id=sid), expect=("accepted", "queued"))
    job_id = (r.get("structured_data") or {}).get("job_id")
    st, sec = c.wait_job(job_id, 1500) if job_id else ("ジョブが作られなかった", 0)
    _record(f"solve_moco_problem {label} → {expect}", st == expect, f"{st} ({sec} s)")
    if job_id and expect == "completed":
        check(f"summarize_moco_solution {label}", c.tool("summarize_moco_solution", job_id=job_id))


def phase_moco(c: Client, ws: Path) -> None:
    print("[Moco]")
    cwd_before = set(Path.cwd().iterdir())
    solver = {"num_mesh_intervals": 10, "optim_max_iterations": 300}
    _solve_moco(c, "inverse(bounds・constraint 付き)", "inverse", {
        "time_bounds": [0.7, 0.9], "coordinates_ref": IK_OUT, "solver_settings": solver,
        "bounds": {"knee_angle_r": [-120, 10]},
        "constraints": [{"type": "MocoControlBoundConstraint", "control_paths": ["/forceset/soleus_r"],
                         "lower_bound": 0.0, "upper_bound": 0.8}]})
    _solve_moco(c, "tracking", "tracking", {"time_bounds": [0.7, 0.9], "states_ref": IK_OUT, "solver_settings": solver})
    _solve_moco(c, "custom", "custom", {"time_bounds": [0.7, 0.9], "solver_settings": solver,
                                        "goals": [{"type": "MocoControlGoal", "weight": 1.0}]})
    _solve_moco(c, "custom 未知の goal", "custom", {"time_bounds": [0.7, 0.9], "goals": [{"type": "NoSuchGoal"}]}, expect="failed")
    _solve_moco(c, "tracking 参照ファイル無し", "tracking", {"time_bounds": [0.7, 0.9]}, expect="failed")
    r = c.tool("create_moco_study", model_path=f"{G}/subject01.osim", template_type="inverse")
    sid = (r.get("structured_data") or {}).get("study_id")
    check("configure_moco_problem workspace 外 → 拒否", c.tool("configure_moco_problem", study_id=sid,
                                                              config_patch={"states_ref": "../../etc/hosts"}), "failed")
    check("configure_moco_problem 不明な study → 失敗", c.tool("configure_moco_problem", study_id="nope", config_patch={}), "failed")
    new_files = sorted(p.name for p in set(Path.cwd().iterdir()) - cwd_before)
    _record("Moco がサーバーの作業フォルダにファイルを書かない", not new_files, ", ".join(new_files))


def phase_security(c: Client, ws: Path) -> None:
    print("[workspace の外を読ませない]")
    geom = ws / "models/original/Geometry"
    geom.mkdir(parents=True, exist_ok=True)
    (geom / "bone.vtp").write_text("<VTKFile/>")
    (ws.parent / "secret.txt").write_text("SECRET")          # workspace の外に置いた「秘密」
    for label, name in [("../ で外", "..%2F..%2F..%2F..%2Fsecret.txt"), ("絶対パス", "%2Fetc%2Fhosts"),
                        ("メッシュ以外", "..%2F..%2F..%2F..%2Fmodels%2Foriginal%2Farm26.osim")]:
        msg = c.rpc("resources/read", {"uri": f"opensim://model/geometry/{name}"})
        content = (msg.get("result") or {}).get("contents", [{}])[0]
        body = content.get("text") or content.get("blob") or ""
        _record(f"geometry Resource {label} → 返さない", "SECRET" not in body and "Host" not in body and len(body) < 64, body[:40])
    msg = c.rpc("resources/read", {"uri": "opensim://model/geometry/bone.vtp"})
    _record("geometry Resource Geometry 内のメッシュは返す", "result" in msg)
    check("get_job_log job_id に ../ → 拒否", c.tool("get_job_log", job_id="../../", stream="stdout"), "failed")
    check("inspect_timeseries_file workspace 外 → 拒否", c.tool("inspect_timeseries_file", file_path="../secret.txt"), "failed")

    print("[workspace の外に書かせない]")
    check("clone_model new_name に ../ → 拒否", c.tool("clone_model", source_model_path="models/original/arm26.osim",
                                                     new_name="../../../escaped_clone"), "failed")
    check("clone_model new_name に / → 拒否", c.tool("clone_model", source_model_path="models/original/arm26.osim",
                                                    new_name="sub/escaped"), "failed")
    check("clone_model 日本語の名前は可", c.tool("clone_model", source_model_path="models/original/arm26.osim", new_name="腕モデル_v2"))
    check("create_moco_study study_name に ../ → 拒否", c.tool("create_moco_study", model_path="models/original/arm26.osim",
                                                             template_type="custom", study_name="../../../escaped_study"), "failed")
    check("configure_moco_problem study_id に ../ → 失敗", c.tool("configure_moco_problem", study_id="../../x", config_patch={}), "failed")

    print("[外を指す setup XML]")
    (ws / "setups/ik").mkdir(parents=True, exist_ok=True)
    outside_out = ws.parent / "escaped_ik.mot"
    (ws / "setups/ik/outside.xml").write_text(f"""<?xml version="1.0" encoding="UTF-8"?>
<OpenSimDocument Version="40000"><InverseKinematicsTool name="x">
<model_file>{ws / 'models/original/arm26.osim'}</model_file>
<marker_file>../../data/trc/markers.trc</marker_file>
<output_motion_file>{outside_out}</output_motion_file>
</InverseKinematicsTool></OpenSimDocument>""")
    check("run_inverse_kinematics 出力が workspace の外 → 拒否", c.tool("run_inverse_kinematics", setup_xml_path="setups/ik/outside.xml"), "failed")
    (ws / "setups/loads").mkdir(parents=True, exist_ok=True)
    (ws / "setups/loads/outside_grf.xml").write_text(
        '<OpenSimDocument Version="40000"><ExternalLoads name="x"><datafile>/etc/hosts</datafile></ExternalLoads></OpenSimDocument>')
    (ws / "setups/id").mkdir(parents=True, exist_ok=True)
    (ws / "setups/id/outside_id.xml").write_text(f"""<?xml version="1.0" encoding="UTF-8"?>
<OpenSimDocument Version="40000"><InverseDynamicsTool name="x">
<model_file>{ws / 'models/original/arm26.osim'}</model_file>
<results_directory>../../results</results_directory>
<external_loads_file>../loads/outside_grf.xml</external_loads_file>
<output_gen_force_file>id.sto</output_gen_force_file>
</InverseDynamicsTool></OpenSimDocument>""")
    check("run_inverse_dynamics 参照先 XML が外を指す → 拒否", c.tool("run_inverse_dynamics", setup_xml_path="setups/id/outside_id.xml"), "failed")

    escaped = sorted(p.name for p in ws.parent.iterdir() if p.name.startswith("escaped"))
    _record("workspace の外に何も作られていない", not escaped, ", ".join(escaped))


def phase_meta(c: Client, ws: Path, has_samples: bool) -> None:
    print("[Resources]")
    c.tool("load_model", model_path="models/original/gait10dof18musc_subject01.osim")
    uris = [f"opensim://model/{k}" for k in ["summary", "bodies", "joints", "coordinates", "muscles", "forces",
                                             "markers", "controllers", "analyses", "probes", "properties/femur_r", "visual"]]
    uris += ["opensim://workspace/files",
             "opensim://timeseries/data%2Fmot%2Fwalk1_ik.mot",
             "opensim://file/summary/data%2Fmot%2Fwalk1_ik.mot",
             "opensim://file/summary/data%2Ftrc%2Fmarkers.trc",
             "opensim://file/summary/data%2Fc3d%2Fwalking2.c3d"]
    job_id = None
    if has_samples:
        for j in sorted((ws / "jobs").iterdir(), key=lambda p: p.stat().st_mtime):
            if json.loads((j / "metadata.json").read_text()).get("status") == "completed":
                job_id = j.name
                break
    if job_id:
        uris += [f"opensim://job/{job_id}/{k}" for k in ["status", "log", "outputs", "summary"]]
    for uri in uris:
        r = c.resource(uri)
        summary = str(r.get("summary", ""))
        ok = r.get("status") not in ("rpc_error", "raw") and not any(w in summary for w in ("エラー", "見つかりません", "失敗"))
        _record(f"resource {uri.removeprefix('opensim://')}", ok, summary)

    print("[Prompts]")
    for name, args in [("review_model_structure", {}), ("diagnose_ik_failure", {"job_id": job_id or "x"}),
                       ("diagnose_id_failure", {"job_id": job_id or "x"}), ("diagnose_dynamic_inconsistency", {}),
                       ("review_analysis_results", {"job_id": job_id or "x"}), ("diagnose_moco_failure", {"job_id": job_id or "x"})]:
        check(f"prompt {name}", c.prompt(name, args))


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--phases", default=",".join(PHASES), help=f"カンマ区切り({', '.join(PHASES)})")
    ap.add_argument("--models-dir", help="OpenSim GUI のサンプル置き場(…/Documents/OpenSim/<版>/Models)")
    ap.add_argument("--workdir", help="一時 workspace を作る場所(既定: システムの一時フォルダ)")
    ap.add_argument("--keep", action="store_true", help="終了後も一時 workspace を残す")
    args = ap.parse_args()

    wanted = [p.strip() for p in args.phases.split(",") if p.strip()]
    unknown = set(wanted) - set(PHASES)
    if unknown:
        ap.error(f"不明なフェーズ: {', '.join(sorted(unknown))}")
    for p in list(wanted):
        wanted += DEPENDS.get(p, [])
    phases = [p for p in PHASES if p in wanted]

    models_dir = find_models_dir(args.models_dir)
    work = Path(tempfile.mkdtemp(prefix="opensim_mcp_check_", dir=args.workdir)).resolve()
    ws = work / "workspace"
    _banner("MCP 全機能テスト")
    print(f"workspace : {ws}")
    print(f"サンプル  : {models_dir or '見つからない(jobs / dynamics / moco はスキップ)'}")
    print(f"フェーズ  : {', '.join(phases)}")

    prepare_workspace(ws, models_dir)
    before = hash_originals(ws)
    skipped = []
    t0 = time.time()
    try:
        for phase in phases:
            if phase in NEEDS_SAMPLES and models_dir is None:
                skipped.append(phase)
                continue
            _banner(phase)
            c = Client(ws, phase)
            try:
                if phase == "meta":
                    phase_meta(c, ws, models_dir is not None)
                else:
                    globals()[f"phase_{phase}"](c, ws)
            finally:
                c.close()

        _banner("元ファイルが変わっていないか")
        after = hash_originals(ws)
        changed = [k for k in before if before[k] != after.get(k)]
        _record("元ファイル(models/original・samples)が書き換わっていない", not changed, ", ".join(changed))
    finally:
        (work / "results.json").write_text(json.dumps(results, ensure_ascii=False, indent=1), encoding="utf-8")
        if not args.keep:
            shutil.rmtree(work, ignore_errors=True)

    failed = [r for r in results if not r["ok"]]
    _banner(f"{'✅' if not failed else '❌'} {len(results) - len(failed)} / {len(results)} 通過"
            f"({time.time() - t0:.0f} 秒)")
    if skipped:
        print(f"スキップ: {', '.join(skipped)}(OpenSim GUI のサンプルが見つからない)")
    for r in failed:
        print(f"   ❌ {r['check']} :: {r['detail'][:200]}")
    if args.keep:
        print(f"一時 workspace: {ws}(結果: {work / 'results.json'})")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
