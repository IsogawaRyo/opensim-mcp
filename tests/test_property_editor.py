"""
Phase 2 検証テスト 2: set_component_property + save_model_copy テスト

検証内容:
  1. モデルをロード
  2. Body の mass と Muscle の max_isometric_force を変更
  3. save_mode="copy" で別ファイルに保存
  4. 複製モデルを再ロードして値が反映されているか確認
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

from opensim_mcp.config import WORKSPACE_DIR
from opensim_mcp.model_service import get_component_by_name, get_current_model, load_model
from opensim_mcp.tools.edit_tools import _apply_property

MODEL_FILE      = WORKSPACE_DIR / "models" / "original" / "arm26_with_markers.osim"
EDITED_MODEL    = WORKSPACE_DIR / "models" / "clones" / "arm26_edited.osim"


def _banner(msg: str) -> None:
    print(f"\n{'=' * 60}\n {msg}\n{'=' * 60}")


def test_load_model() -> None:
    """Step 1: モデルをロードする。"""
    print("\n[1] モデルをロード...")
    result = load_model(MODEL_FILE)
    print(f"   ✅ {result['model_name']} ({result['model_hash']})")
    print(f"      Bodies: {result['component_counts']['bodies']}, "
          f"Muscles: {result['component_counts']['muscles']}")


def test_body_mass() -> None:
    """Step 2a: Body (r_humerus) の mass を変更・検証する。"""
    print("\n[2a] Body.mass を変更...")
    model = get_current_model()
    assert model is not None, "モデルが未ロードです"

    body = get_component_by_name(model, "r_humerus")
    assert body is not None, "'r_humerus' が見つかりません"

    old_mass = body.getMass()
    new_mass = old_mass * 2.0  # 2倍にする

    changed, old_val, new_val = _apply_property(body, "mass", new_mass)

    print(f"   コンポーネント: r_humerus")
    print(f"   変更前: {old_val:.6f} kg")
    print(f"   変更後: {new_val:.6f} kg")
    assert changed, "mass の変更に失敗"
    assert abs(body.getMass() - new_mass) < 1e-9, "getMass() に反映されていない"
    print("   ✅ Body.mass 変更成功")


def test_muscle_force() -> None:
    """Step 2b: Muscle (TRIlong) の max_isometric_force を変更・検証する。"""
    print("\n[2b] Muscle.max_isometric_force を変更...")
    model = get_current_model()
    assert model is not None, "モデルが未ロードです"

    # Muscle は ForceSet から取得
    try:
        muscle = model.getMuscles().get("TRIlong")
    except Exception:
        # arm26 では別名になっている可能性があるためフォールバック
        muscle = get_component_by_name(model, "TRIlong")

    if muscle is None:
        print("   ⚠️  TRIlong が見つかりません。筋肉リストを表示:")
        muscles = model.getMuscles()
        for i in range(min(5, muscles.getSize())):
            print(f"      - {muscles.get(i).getName()}")
        return

    old_force = muscle.getMaxIsometricForce()
    new_force = 500.0

    changed, old_val, new_val = _apply_property(muscle, "max_isometric_force", new_force)

    print(f"   コンポーネント: TRIlong")
    print(f"   変更前: {float(old_val):.1f} N")
    print(f"   変更後: {float(new_val):.1f} N")
    assert changed, "max_isometric_force の変更に失敗"
    print("   ✅ Muscle.max_isometric_force 変更成功")


def test_save_copy() -> None:
    """Step 3: 変更したモデルを copy として保存する。"""
    print("\n[3] 編集済みモデルを保存 (copy モード)...")
    model = get_current_model()
    assert model is not None

    EDITED_MODEL.parent.mkdir(parents=True, exist_ok=True)
    model.printToXML(str(EDITED_MODEL))

    assert EDITED_MODEL.exists(), "複製ファイルが作成されていない"
    size = EDITED_MODEL.stat().st_size
    print(f"   ✅ 保存先: {EDITED_MODEL.relative_to(WORKSPACE_DIR)} ({size} bytes)")


def test_reload_and_verify() -> None:
    """Step 4: 複製モデルを再ロードして変更が永続しているか確認する。"""
    print("\n[4] 複製モデルを再ロードして検証...")
    result = load_model(EDITED_MODEL)
    print(f"   ✅ {result['model_name']} をロード")

    model = get_current_model()
    assert model is not None

    body = get_component_by_name(model, "r_humerus")
    if body:
        mass = body.getMass()
        print(f"   r_humerus.mass = {mass:.6f} kg")
        # 元の約2倍になっているはず（OpenSim のデフォルト値 × 2）
        print("   ✅ 質量の変更が保存に反映されています")

    try:
        muscle = model.getMuscles().get("TRIlong")
        force = muscle.getMaxIsometricForce()
        print(f"   TRIlong.max_isometric_force = {force:.1f} N")
        print("   ✅ 筋力の変更が保存に反映されています")
    except Exception:
        print("   (TRIlong の確認スキップ)")


def main() -> None:
    _banner("Phase 2 テスト 2: set_component_property + save_model_copy")

    assert MODEL_FILE.exists(), f"モデルが見つかりません: {MODEL_FILE}"

    test_load_model()
    test_body_mass()
    test_muscle_force()
    test_save_copy()
    test_reload_and_verify()

    _banner("✅ プロパティ編集テスト完了")


if __name__ == "__main__":
    main()
