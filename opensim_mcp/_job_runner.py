"""
OpenSim ジョブ実行用の子プロセス runner スクリプト。

job_service.run_job() から subprocess.Popen() で呼び出される。
このスクリプトはメインプロセスとは独立した Python インタープリタで動作するため、
OpenSim の C++ ライブラリのスレッドセーフ制約を回避できる。

使い方:
    python _job_runner.py /path/to/run_info.json
"""

from __future__ import annotations

import json
import math
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
import xml.etree.ElementTree as ET

# プロジェクトルートを sys.path に追加
ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _update_metadata(jdir: Path, updates: dict) -> None:
    meta_file = jdir / "metadata.json"
    if meta_file.exists():
        with open(meta_file, encoding="utf-8") as f:
            meta = json.load(f)
    else:
        meta = {}
    meta.update(updates)
    with open(meta_file, "w", encoding="utf-8") as f:
        json.dump(meta, f, indent=2, ensure_ascii=False)


def _resolve_output_path(base_dir: Path, raw_path: str) -> Path | None:
    raw_path = (raw_path or "").strip()
    if not raw_path:
        return None

    p = Path(raw_path)
    if not p.is_absolute():
        p = base_dir / p

    return p.resolve()


def _to_workspace_relative(path: Path, workspace_dir: Path) -> str | None:
    try:
        return str(path.resolve().relative_to(workspace_dir.resolve()))
    except ValueError:
        return None


def _collect_declared_outputs(tool_name_key: str, setup_xml_path: str, workspace_dir: Path) -> list[str]:
    setup_path = Path(setup_xml_path).resolve()
    root = ET.parse(setup_path).getroot()
    tool = next(iter(root), None)
    if tool is None:
        return []

    def _child_text(tag: str) -> str:
        elem = tool.find(tag)
        return elem.text.strip() if elem is not None and elem.text else ""

    outputs: list[Path] = []

    if tool_name_key == "ik":
        motion_file = _resolve_output_path(setup_path.parent, _child_text("output_motion_file"))
        if motion_file:
            outputs.append(motion_file)

    elif tool_name_key == "id":
        results_dir = _resolve_output_path(setup_path.parent, _child_text("results_directory")) or setup_path.parent
        for tag in ("output_gen_force_file", "output_body_forces_file"):
            output_path = _resolve_output_path(results_dir, _child_text(tag))
            if output_path:
                outputs.append(output_path)

    elif tool_name_key in {"analyze", "so", "rra", "cmc", "fd"}:
        results_dir = _resolve_output_path(setup_path.parent, _child_text("results_directory"))
        if results_dir and results_dir.exists():
            outputs.extend(
                p for p in sorted(results_dir.iterdir())
                if p.is_file()
            )

    elif tool_name_key == "scale":
        # ScaleTool の出力モデルは ModelScaler / MarkerPlacer の中にある
        for tag in ("ModelScaler/output_model_file", "MarkerPlacer/output_model_file"):
            output_model_file = _resolve_output_path(setup_path.parent, _child_text(tag))
            if output_model_file:
                outputs.append(output_model_file)

    rel_paths: list[str] = []
    for output in outputs:
        if output.exists():
            rel_path = _to_workspace_relative(output, workspace_dir)
            if rel_path and rel_path not in rel_paths:
                rel_paths.append(rel_path)

    return rel_paths


def _prepare_output_dirs(tool_name_key: str, setup_xml_path: str) -> None:
    """
    setup XML に書かれた出力先のフォルダを先に作る。

    OpenSim は出力先フォルダが無いと、ファイルを書かずに成功を返すことがある(IK で確認)。
    """
    setup_path = Path(setup_xml_path).resolve()
    try:
        tool = next(iter(ET.parse(setup_path).getroot()), None)
    except ET.ParseError:
        return  # 壊れた XML は OpenSim 側でエラーにさせる
    if tool is None:
        return

    def _child_text(tag: str) -> str:
        elem = tool.find(tag)
        return elem.text.strip() if elem is not None and elem.text else ""

    file_tags = {
        "ik": ["output_motion_file"],
        "scale": ["ModelScaler/output_model_file", "MarkerPlacer/output_model_file"],
    }.get(tool_name_key, [])
    for tag in file_tags:
        p = _resolve_output_path(setup_path.parent, _child_text(tag))
        if p:
            p.parent.mkdir(parents=True, exist_ok=True)
    if tool_name_key in {"id", "analyze", "so", "rra", "cmc", "fd"}:
        p = _resolve_output_path(setup_path.parent, _child_text("results_directory"))
        if p:
            p.mkdir(parents=True, exist_ok=True)


def run_ik(setup_xml_path: str, jdir: Path) -> dict:
    import opensim  # noqa: PLC0415

    with open(jdir / "stdout.log", "w", encoding="utf-8") as sout:
        sout.write(f"[IK] Setup XML: {setup_xml_path}\n")
        sout.flush()

    ik_tool = opensim.InverseKinematicsTool(setup_xml_path)
    ret = ik_tool.run()

    with open(jdir / "stdout.log", "a", encoding="utf-8") as sout:
        sout.write(f"[IK] 完了 (成功: {ret})\n")

    return {
        "tool": "InverseKinematicsTool",
        "setup_xml": setup_xml_path,
        "status": "completed",
        "success": bool(ret),
    }


def run_id(setup_xml_path: str, jdir: Path) -> dict:
    import opensim  # noqa: PLC0415

    with open(jdir / "stdout.log", "w", encoding="utf-8") as sout:
        sout.write(f"[ID] Setup XML: {setup_xml_path}\n")
        sout.flush()

    id_tool = opensim.InverseDynamicsTool(setup_xml_path)
    ret = id_tool.run()

    with open(jdir / "stdout.log", "a", encoding="utf-8") as sout:
        sout.write(f"[ID] 完了 (成功: {ret})\n")

    return {
        "tool": "InverseDynamicsTool",
        "setup_xml": setup_xml_path,
        "status": "completed",
        "success": bool(ret),
    }


def run_analyze(setup_xml_path: str, jdir: Path) -> dict:
    import opensim  # noqa: PLC0415

    with open(jdir / "stdout.log", "w", encoding="utf-8") as sout:
        sout.write(f"[Analyze] Setup XML: {setup_xml_path}\n")
        sout.flush()

    analyze_tool = opensim.AnalyzeTool(setup_xml_path)
    ret = analyze_tool.run()

    with open(jdir / "stdout.log", "a", encoding="utf-8") as sout:
        sout.write(f"[Analyze] 完了 (成功: {ret})\n")

    return {
        "tool": "AnalyzeTool",
        "setup_xml": setup_xml_path,
        "status": "completed",
        "success": bool(ret),
    }


def run_scale(setup_xml_path: str, jdir: Path) -> dict:
    import opensim  # noqa: PLC0415

    with open(jdir / "stdout.log", "w", encoding="utf-8") as sout:
        sout.write(f"[Scale] Setup XML: {setup_xml_path}\n")
        sout.flush()

    scale_tool = opensim.ScaleTool(setup_xml_path)
    ret = scale_tool.run()

    with open(jdir / "stdout.log", "a", encoding="utf-8") as sout:
        sout.write(f"[Scale] 完了 (成功: {ret})\n")

    return {
        "tool": "ScaleTool",
        "setup_xml": setup_xml_path,
        "status": "completed",
        "success": bool(ret),
    }


def run_so(setup_xml_path: str, jdir: Path) -> dict:
    """Static Optimization は AnalyzeTool として実行する（setup XML に StaticOptimization 解析を含む）。"""
    import opensim  # noqa: PLC0415

    with open(jdir / "stdout.log", "w", encoding="utf-8") as sout:
        sout.write(f"[StaticOptimization] Setup XML: {setup_xml_path}\n")
        sout.flush()

    analyze_tool = opensim.AnalyzeTool(setup_xml_path)
    ret = analyze_tool.run()

    with open(jdir / "stdout.log", "a", encoding="utf-8") as sout:
        sout.write(f"[StaticOptimization] 完了 (成功: {ret})\n")

    return {
        "tool": "StaticOptimization (via AnalyzeTool)",
        "setup_xml": setup_xml_path,
        "status": "completed",
        "success": bool(ret),
    }


def run_rra(setup_xml_path: str, jdir: Path) -> dict:
    import opensim  # noqa: PLC0415

    with open(jdir / "stdout.log", "w", encoding="utf-8") as sout:
        sout.write(f"[RRA] Setup XML: {setup_xml_path}\n")
        sout.flush()

    rra_tool = opensim.RRATool(setup_xml_path)
    ret = rra_tool.run()

    with open(jdir / "stdout.log", "a", encoding="utf-8") as sout:
        sout.write(f"[RRA] 完了 (成功: {ret})\n")

    return {
        "tool": "RRATool",
        "setup_xml": setup_xml_path,
        "status": "completed",
        "success": bool(ret),
    }


def run_cmc(setup_xml_path: str, jdir: Path) -> dict:
    import opensim  # noqa: PLC0415

    with open(jdir / "stdout.log", "w", encoding="utf-8") as sout:
        sout.write(f"[CMC] Setup XML: {setup_xml_path}\n")
        sout.flush()

    cmc_tool = opensim.CMCTool(setup_xml_path)
    ret = cmc_tool.run()

    with open(jdir / "stdout.log", "a", encoding="utf-8") as sout:
        sout.write(f"[CMC] 完了 (成功: {ret})\n")

    return {
        "tool": "CMCTool",
        "setup_xml": setup_xml_path,
        "status": "completed",
        "success": bool(ret),
    }


def run_fd(setup_xml_path: str, jdir: Path) -> dict:
    import opensim  # noqa: PLC0415

    with open(jdir / "stdout.log", "w", encoding="utf-8") as sout:
        sout.write(f"[ForwardDynamics] Setup XML: {setup_xml_path}\n")
        sout.flush()

    fd_tool = opensim.ForwardTool(setup_xml_path)
    ret = fd_tool.run()

    with open(jdir / "stdout.log", "a", encoding="utf-8") as sout:
        sout.write(f"[ForwardDynamics] 完了 (成功: {ret})\n")

    return {
        "tool": "ForwardTool",
        "setup_xml": setup_xml_path,
        "status": "completed",
        "success": bool(ret),
    }


def _moco_model_processor(opensim: Any, model_path: str, config: dict, log: Any) -> Any:
    """
    Moco 用にモデルを前処理する ModelProcessor を作る。

    muscle_model="degroote"(既定): Moco の公式例と同じ前処理。
      Millard / Thelen など弾性腱の筋は fiber_length の状態を持ち、そのままでは Moco で解けないため、
      腱を剛体にして DeGrooteFregly2016 筋に置き換える。
    muscle_model="as_is": モデルをそのまま使う(Moco 対応の筋だけのモデル向け)。
    reserve_strength > 0: 各座標に予備アクチュエータを足す(既定 1.0)。
    external_loads_file: 指定があれば外力を足す。
    """
    proc = opensim.ModelProcessor(model_path)
    if config.get("external_loads_file"):
        proc.append(opensim.ModOpAddExternalLoads(config["external_loads_file"]))
    muscle_model = config.get("muscle_model", "degroote")
    if muscle_model == "degroote":
        proc.append(opensim.ModOpIgnoreTendonCompliance())
        proc.append(opensim.ModOpReplaceMusclesWithDeGrooteFregly2016())
        proc.append(opensim.ModOpIgnorePassiveFiberForcesDGF())
        proc.append(opensim.ModOpScaleActiveFiberForceCurveWidthDGF(1.5))
    elif muscle_model != "as_is":
        raise ValueError(f"未対応の muscle_model です: {muscle_model!r}(degroote / as_is)")
    reserve_strength = float(config.get("reserve_strength", 1.0))
    if reserve_strength > 0:
        proc.append(opensim.ModOpAddReserves(reserve_strength))
    log(f"[Moco] 前処理: muscle_model={muscle_model}, reserve_strength={reserve_strength}")
    return proc


def _moco_states_table(opensim: Any, path: str) -> Any:
    """状態リファレンス用の TableProcessor。度を rad に直し、列名を状態の絶対パスにそろえる。"""
    table = opensim.TableProcessor(path)
    table.append(opensim.TabOpConvertDegreesToRadians())
    table.append(opensim.TabOpUseAbsoluteStateNames())
    return table


def _moco_make_goal(opensim: Any, spec: dict) -> Any:
    """configure_moco_problem の goals 1 件から MocoGoal を作る。未対応の type はエラーにする。"""
    gtype = spec.get("type", "")
    if gtype == "MocoControlGoal":
        goal = opensim.MocoControlGoal()
    elif gtype == "MocoFinalTimeGoal":
        goal = opensim.MocoFinalTimeGoal()
    elif gtype == "MocoStateTrackingGoal":
        goal = opensim.MocoStateTrackingGoal()
        goal.setReference(_moco_states_table(opensim, spec["states_ref"]))
        goal.setAllowUnusedReferences(True)
    elif gtype == "MocoMarkerTrackingGoal":
        goal = opensim.MocoMarkerTrackingGoal()
        # Python では MarkersReference(ファイル) も MarkersReference(テーブル) も通らず、重みの Set が要る。
        # TRC から読んだテーブルは Units を持つので mm のデータも m に直される
        goal.setMarkersReference(opensim.MarkersReference(
            opensim.TimeSeriesTableVec3(spec["markers_ref"]), opensim.SetMarkerWeights()))
        goal.setAllowUnusedReferences(True)
    elif gtype == "MocoJointReactionGoal":
        goal = opensim.MocoJointReactionGoal()
        goal.setJointPath(spec["joint_path"])
    else:
        raise ValueError(
            f"未対応の goal type です: {gtype!r}(MocoControlGoal / MocoFinalTimeGoal / "
            "MocoStateTrackingGoal / MocoMarkerTrackingGoal / MocoJointReactionGoal)"
        )
    goal.setName(spec.get("name", gtype))
    # MocoJointReactionGoal は成分ごとの setWeight(名前, 重み) が基底の setWeight(重み) を隠すので、基底を直接呼ぶ
    opensim.MocoGoal.setWeight(goal, float(spec.get("weight", 1.0)))
    return goal


def _moco_make_constraint(opensim: Any, spec: dict) -> Any:
    """configure_moco_problem の constraints 1 件から path constraint を作る。"""
    ctype = spec.get("type", "")
    if ctype != "MocoControlBoundConstraint":
        raise ValueError(f"未対応の constraint type です: {ctype!r}(MocoControlBoundConstraint)")
    constraint = opensim.MocoControlBoundConstraint()
    constraint.setName(spec.get("name", ctype))
    for path in spec.get("control_paths", []):
        constraint.addControlPath(path)
    if "lower_bound" in spec:
        constraint.setLowerBound(opensim.Constant(float(spec["lower_bound"])))
    if "upper_bound" in spec:
        constraint.setUpperBound(opensim.Constant(float(spec["upper_bound"])))
    return constraint


def _moco_apply_bounds(opensim: Any, problem: Any, model_path: str, bounds: dict, log: Any) -> None:
    """bounds {座標名: [下限, 上限]} を状態の範囲として設定する。回転座標は度、並進座標は m で受け取る。"""
    if not bounds:
        return
    model = opensim.Model(model_path)
    model.initSystem()
    coords = model.getCoordinateSet()
    for name, (lower, upper) in bounds.items():
        if not coords.contains(name):
            raise ValueError(f"bounds の座標 {name!r} がモデルにありません")
        coord = coords.get(name)
        scale = math.pi / 180.0 if coord.getMotionType() == 1 else 1.0  # 1 = Rotational
        problem.setStateInfo(f"{coord.getAbsolutePathString()}/value",
                             [float(lower) * scale, float(upper) * scale])
        log(f"[Moco] bounds: {name} = [{lower}, {upper}]")


def run_moco(config_json_path: str, jdir: Path) -> dict:
    """Moco 最適化を子プロセスで実行する。

    config.json の template_type に従って MocoTrack / MocoInverse / MocoStudy を切り替える。
    """
    import json  # noqa: PLC0415
    import opensim  # noqa: PLC0415

    config = json.loads(Path(config_json_path).read_text(encoding="utf-8"))
    template_type     = config.get("template_type", "custom")
    model_path        = config["model_path"]
    time_bounds       = config.get("time_bounds", [0.0, 1.0])
    solver_settings   = config.get("solver_settings", {})
    states_ref        = config.get("states_ref")
    coordinates_ref   = config.get("coordinates_ref")
    goals             = config.get("goals", [])

    stdout_log = open(jdir / "stdout.log", "w", encoding="utf-8")  # noqa: WPS515

    def log(msg: str) -> None:
        stdout_log.write(msg + "\n")
        stdout_log.flush()

    log(f"[Moco] template_type: {template_type}")
    log(f"[Moco] model: {model_path}")
    log(f"[Moco] time_bounds: {time_bounds}")

    output_solution = str(jdir / "moco_solution.sto")

    t0, t1 = float(time_bounds[0]), float(time_bounds[1])
    num_mesh_intervals = int(solver_settings.get("num_mesh_intervals", 50))

    try:
        model_proc = _moco_model_processor(opensim, model_path, config, log)

        if template_type == "tracking":
            # ----- MocoTrack (state tracking) -----
            if not states_ref:
                raise ValueError("template_type='tracking' には states_ref が必要です")
            track = opensim.MocoTrack()
            track.setName("moco_track")
            track.setModel(model_proc)
            track.setStatesReference(_moco_states_table(opensim, states_ref))
            track.set_states_global_tracking_weight(1.0)
            track.set_allow_unused_references(True)
            track.set_track_reference_position_derivatives(True)
            track.set_initial_time(t0)
            track.set_final_time(t1)
            study = track.initialize()

        elif template_type == "inverse":
            # ----- MocoInverse (coordinate tracking) -----
            if not coordinates_ref:
                raise ValueError("template_type='inverse' には coordinates_ref が必要です")
            inverse = opensim.MocoInverse()
            inverse.setName("moco_inverse")
            inverse.setModel(model_proc)
            coord_ref = opensim.TableProcessor(coordinates_ref)
            coord_ref.append(opensim.TabOpLowPassFilter(6))
            inverse.setKinematics(coord_ref)
            inverse.set_kinematics_allow_extra_columns(True)
            inverse.set_initial_time(t0)
            inverse.set_final_time(t1)
            # mesh_interval は秒単位の刻み幅
            inverse.set_mesh_interval((t1 - t0) / num_mesh_intervals)
            # bounds / constraints を足せるよう、solve() ではなく MocoStudy を取り出して解く
            study = inverse.initialize()

        elif template_type == "custom":
            # ----- MocoStudy (custom) -----
            study = opensim.MocoStudy()
            study.setName("moco_custom")
            problem = study.updProblem()
            problem.setModelProcessor(model_proc)
            problem.setTimeBounds(t0, t1)
            for g in goals:
                problem.addGoal(_moco_make_goal(opensim, g))
            study.initCasADiSolver()

        else:
            raise ValueError(f"未対応の template_type です: {template_type!r}(tracking / inverse / custom)")

        problem = study.updProblem()
        _moco_apply_bounds(opensim, problem, model_path, config.get("bounds") or {}, log)
        for c in config.get("constraints") or []:
            problem.addPathConstraint(_moco_make_constraint(opensim, c))

        solver = opensim.MocoCasADiSolver.safeDownCast(study.updSolver())
        if solver:
            solver.set_num_mesh_intervals(num_mesh_intervals)
            solver.set_verbosity(int(solver_settings.get("verbosity", 2)))
            solver.set_optim_max_iterations(int(solver_settings.get("optim_max_iterations", 1000)))

        log(f"[Moco] {template_type} 初期化完了、solve 開始...")
        solution = study.solve()
        if not solution.success():
            # 収束しなかった解でも中身を確認できるよう保存してから失敗にする
            solution.unseal().write(output_solution)
            raise RuntimeError(f"Moco が収束しませんでした(status: {solution.getStatus()})。"
                               f" 途中の解: {output_solution}")

        # 結果を保存
        solution.write(output_solution)
        obj_val = solution.getObjective() if hasattr(solution, "getObjective") else "N/A"
        log(f"[Moco] 完了 — objective: {obj_val}")
        log(f"[Moco] 出力ファイル: {output_solution}")

    except Exception as exc:
        log(f"[Moco] エラー: {exc}")
        stdout_log.close()
        raise

    stdout_log.close()

    return {
        "tool": f"Moco ({template_type})",
        "config_json": config_json_path,
        "status": "completed",
        "solution": output_solution,
    }



def main() -> None:
    if len(sys.argv) < 2:
        print("Usage: _job_runner.py <run_info.json>", file=sys.stderr)
        sys.exit(1)

    run_info_path = Path(sys.argv[1])
    with open(run_info_path, encoding="utf-8") as f:
        run_info = json.load(f)

    job_id = run_info["job_id"]
    tool_name_key = run_info["tool_name_key"]
    setup_xml_path = run_info["setup_xml_path"]
    jdir = Path(run_info["job_dir"])
    workspace_dir = Path(run_info["workspace_dir"])

    # running に更新
    _update_metadata(jdir, {"status": "running", "started_at": _now_iso(), "pid": os.getpid()})
    started = datetime.now().timestamp()

    try:
        if tool_name_key != "moco":
            _prepare_output_dirs(tool_name_key, setup_xml_path)
        else:
            # MocoTrack などは作業フォルダに中間ファイル(<name>_tracked_states.sto など)を書くので、
            # サーバーの起動フォルダ(リポジトリ直下など)を汚さないようジョブのフォルダで実行する
            os.chdir(jdir)

        if tool_name_key == "ik":
            result = run_ik(setup_xml_path, jdir)
        elif tool_name_key == "id":
            result = run_id(setup_xml_path, jdir)
        elif tool_name_key == "analyze":
            result = run_analyze(setup_xml_path, jdir)
        elif tool_name_key == "scale":
            result = run_scale(setup_xml_path, jdir)
        elif tool_name_key == "so":
            result = run_so(setup_xml_path, jdir)
        elif tool_name_key == "rra":
            result = run_rra(setup_xml_path, jdir)
        elif tool_name_key == "cmc":
            result = run_cmc(setup_xml_path, jdir)
        elif tool_name_key == "fd":
            result = run_fd(setup_xml_path, jdir)
        elif tool_name_key == "moco":
            result = run_moco(setup_xml_path, jdir)  # setup_xml_path は config.json パス
        else:
            raise ValueError(f"未知の tool_name_key: {tool_name_key!r}")

        if result.get("success") is False:
            raise RuntimeError(f"{result.get('tool', tool_name_key)} が失敗を返しました(run() = False)")

        # summary.json 保存
        with open(jdir / "summary.json", "w", encoding="utf-8") as f:
            json.dump(result, f, indent=2, ensure_ascii=False)

        # 成果物一覧(出力先に前から置いてあるファイルは除き、今回の実行で書かれたものだけ数える)
        artifacts_dir = jdir / "artifacts"
        # moco の setup_xml_path は config.json なので、setup XML の出力先は無い(解は jobs/<id>/ に書く)
        declared = [] if tool_name_key == "moco" else _collect_declared_outputs(tool_name_key, setup_xml_path, workspace_dir)
        output_files = [rel for rel in declared if (workspace_dir / rel).stat().st_mtime >= started - 1.0]
        if tool_name_key != "moco" and not output_files:
            raise RuntimeError(
                "ツールは成功を返しましたが、setup XML の出力先に成果物が1つも書かれませんでした。"
                " 出力パス(output_motion_file / results_directory など)を確認してください"
            )
        if artifacts_dir.exists():
            for p in sorted(artifacts_dir.iterdir()):
                if not p.is_file():
                    continue
                rel_path = _to_workspace_relative(p, workspace_dir)
                if rel_path and rel_path not in output_files:
                    output_files.append(rel_path)

        _update_metadata(jdir, {
            "status": "completed",
            "finished_at": _now_iso(),
            "output_files": output_files,
        })

    except Exception as exc:
        with open(jdir / "stderr.log", "a", encoding="utf-8") as serr:
            serr.write(f"\n[ERROR] {exc}\n")
            import traceback
            traceback.print_exc(file=serr)

        with open(jdir / "summary.json", "w", encoding="utf-8") as f:
            json.dump({"error": str(exc)}, f, indent=2, ensure_ascii=False)

        _update_metadata(jdir, {
            "status": "failed",
            "finished_at": _now_iso(),
        })
        sys.exit(1)


if __name__ == "__main__":
    main()
