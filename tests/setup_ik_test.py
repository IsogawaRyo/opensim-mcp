"""
IK E2E テスト用セットアップスクリプト。

arm26 モデルに TRC のマーカー名 (humerus, ulna) を追加したモデルを作成し、
IK Setup XML を生成してワークスペースに配置する。
マーカーファイル data/trc/arm26_ik.trc が無ければ、モデルを動かして合成する。

使い方:
    python tests/setup_ik_test.py
"""

import math
import sys
from pathlib import Path

# プロジェクトルートを sys.path に追加
ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

import opensim
from opensim_mcp.config import WORKSPACE_DIR


def _write_synthetic_trc(model_path: Path, trc_path: Path, duration: float = 1.0, rate: int = 100) -> None:
    """肩と肘をなめらかに動かしたときのマーカー位置を TRC に書く(手元のデータに頼らないため)。"""
    model = opensim.Model(str(model_path))
    state = model.initSystem()
    coords = model.getCoordinateSet()
    markers = [model.getMarkerSet().get(i) for i in range(model.getMarkerSet().getSize())]

    table = opensim.TimeSeriesTableVec3()
    table.setColumnLabels(opensim.StdVectorString([m.getName() for m in markers]))
    for k in range(int(duration * rate) + 1):
        t = k / rate
        coords.get("r_shoulder_elev").setValue(state, 0.3 + 0.2 * math.sin(2 * math.pi * t), False)
        coords.get("r_elbow_flex").setValue(state, 0.8 + 0.6 * math.sin(2 * math.pi * t))
        model.realizePosition(state)
        row = opensim.RowVectorVec3([m.getLocationInGround(state) for m in markers])
        table.appendRow(t, row)
    table.addTableMetaDataString("DataRate", str(rate))
    table.addTableMetaDataString("Units", "m")
    trc_path.parent.mkdir(parents=True, exist_ok=True)
    opensim.TRCFileAdapter.write(table, str(trc_path))


def setup_ik_test() -> None:
    TESTS_DIR = Path(opensim.__file__).parent / "tests"
    TRC_PATH = WORKSPACE_DIR / "data" / "trc" / "arm26_ik.trc"
    MODEL_OUT = WORKSPACE_DIR / "models" / "original" / "arm26_with_markers.osim"
    RESULTS_DIR = WORKSPACE_DIR / "jobs" / "ik_test_results"
    SETUP_XML = WORKSPACE_DIR / "setups" / "ik" / "arm26_ik_setup.xml"

    for d in (RESULTS_DIR, MODEL_OUT.parent, SETUP_XML.parent):
        d.mkdir(parents=True, exist_ok=True)

    # ------------------------------------------------------------------
    # Step 1: arm26 にマーカーを追加して保存
    # ------------------------------------------------------------------
    print("[1] arm26 にマーカーを追加中...")
    model = opensim.Model(str(TESTS_DIR / "arm26.osim"))
    model.initSystem()

    # TRC の 2 マーカー名に合わせて追加
    # humerus → r_humerus 体節に固定
    # ulna    → r_ulna_radius_hand 体節に固定
    m1 = opensim.Marker()
    m1.setName("humerus")
    m1.setParentFrameName("/bodyset/r_humerus")
    m1.set_location(opensim.Vec3(0.0, -0.18, 0.01))
    model.addMarker(m1)

    m2 = opensim.Marker()
    m2.setName("ulna")
    m2.setParentFrameName("/bodyset/r_ulna_radius_hand")
    m2.set_location(opensim.Vec3(0.0, -0.23, 0.01))
    model.addMarker(m2)

    model.finalizeConnections()
    model.printToXML(str(MODEL_OUT))
    print(f"   ✅ モデル保存: {MODEL_OUT.name}")

    if not TRC_PATH.exists():
        _write_synthetic_trc(MODEL_OUT, TRC_PATH)
        print(f"   ✅ マーカーファイルを合成: {TRC_PATH.name}")

    # ------------------------------------------------------------------
    # Step 2: IK Setup XML を生成
    # ------------------------------------------------------------------
    print("[2] IK Setup XML を生成...")

    ik_tool = opensim.InverseKinematicsTool()
    ik_tool.setName("arm26_ik")
    # setModel() で渡しても setup XML に model_file は書かれない。パスで指定する
    ik_tool.set_model_file(str(MODEL_OUT))
    ik_tool.set_marker_file(str(TRC_PATH))
    ik_tool.set_output_motion_file(str(RESULTS_DIR / "arm26_ik_result.mot"))
    ik_tool.setStartTime(0.0)
    ik_tool.setEndTime(1.0)      # TRC は 20 秒相当あるので最初の 1 秒だけ

    # マーカータスクの重み設定
    tasks = opensim.IKTaskSet()
    for name in ["humerus", "ulna"]:
        task = opensim.IKMarkerTask()
        task.setName(name)
        task.setApply(True)
        task.setWeight(1.0)
        tasks.cloneAndAppend(task)
    ik_tool.set_IKTaskSet(tasks)

    ik_tool.printToXML(str(SETUP_XML))
    print(f"   ✅ Setup XML 保存: {SETUP_XML.name}")

    # ------------------------------------------------------------------
    # Step 3: モデルパスを XML 内で絶対パスに書き換え
    # ------------------------------------------------------------------
    print("[3] Setup XML のパスを修正...")
    import xml.etree.ElementTree as ET
    tree = ET.parse(SETUP_XML)
    root = tree.getroot()

    # model_file タグを探して絶対パスに設定
    for elem in root.iter("model_file"):
        elem.text = str(MODEL_OUT)
    for elem in root.iter("marker_file"):
        elem.text = str(TRC_PATH)
    for elem in root.iter("output_motion_file"):
        elem.text = str(RESULTS_DIR / "arm26_ik_result.mot")

    tree.write(str(SETUP_XML), xml_declaration=True, encoding="utf-8")
    print(f"   ✅ パス修正完了")

    print()
    print("=== セットアップ完了 ===")
    print(f"モデル  : {MODEL_OUT}")
    print(f"TRC     : {TRC_PATH}")
    print(f"Setup   : {SETUP_XML}")
    print(f"結果先  : {RESULTS_DIR}")
    print()
    print("次のコマンドで IK テストを実行できます:")
    print(f"  python tests/run_ik_e2e_test.py")


if __name__ == "__main__":
    setup_ik_test()
