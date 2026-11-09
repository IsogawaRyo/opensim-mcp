"""
MCP Prompts（仕様書 §11）。

実装 Prompts:
  - review_model_structure      : モデル構造レビュー
  - diagnose_ik_failure         : IK 失敗診断
  - diagnose_id_failure         : ID 失敗診断
  - diagnose_dynamic_inconsistency: RRA/CMC 前の整合性点検
  - review_analysis_results     : 解析結果レビュー
  - diagnose_moco_failure       : Moco solve 失敗診断
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from mcp.server.fastmcp import FastMCP


def register_prompts(mcp: "FastMCP") -> None:
    """Phase 4 Prompts を mcp インスタンスに登録する。"""

    # ------------------------------------------------------------------
    # 1) review_model_structure
    # ------------------------------------------------------------------

    @mcp.prompt()
    def review_model_structure() -> str:
        """
        ロード済みモデルの構造をレビューするためのプロンプトを返す。

        使い方: このプロンプトを使うと、モデルを体節・関節・座標・筋肉の観点から
        体系的にレビューできます。
        """
        return """あなたは OpenSim の筋骨格モデルレビューアシスタントです。
以下の順番でモデル構造をレビューしてください：

1. **モデル概要の確認**
   - `get_model_summary` を呼び出して基本情報を取得してください。
   - モデル名、重力設定、体節数・筋肉数・関節数・座標数を確認します。

2. **体節 (Bodies) のレビュー**
   - `list_components(component_type="bodies")` で一覧を取得してください。
   - 各体節の質量・慣性テンソルが生理学的に妥当か確認してください。

3. **関節・座標 (Joints / Coordinates) のレビュー**
   - `list_components(component_type="joints")` で一覧を取得してください。
   - 自由度の設定（locked / prescribed / free）が適切か確認してください。
   - 可動域（range_min / range_max）が実用的な値か確認してください。

4. **筋肉 (Muscles) のレビュー**
   - `list_components(component_type="muscles")` で一覧を取得してください。
   - 各筋肉の max_isometric_force が妥当か確認してください。
   - optimal_fiber_length と tendon_slack_length のバランスを確認してください。

5. **マーカー (Markers) のレビュー**
   - `list_components(component_type="markers")` で一覧を取得してください。
   - マーカーが体節に正しく付いているか確認してください。

6. **モデルの検証**
   - `validate_model` を呼び出して整合性エラーがないか確認してください。

各ステップで問題を発見した場合は、`set_component_property` で修正できます。
"""

    # ------------------------------------------------------------------
    # 2) diagnose_ik_failure
    # ------------------------------------------------------------------

    @mcp.prompt()
    def diagnose_ik_failure(job_id: str = "") -> str:
        """
        IK 失敗時の一次診断プロンプトを返す。

        Args:
            job_id: 失敗した IK ジョブの ID（省略可）。
        """
        job_hint = f"\nまず `get_job_log('{job_id}', 'stderr')` でエラーログを取得してください。" if job_id else ""
        return f"""あなたは OpenSim Inverse Kinematics (IK) のデバッグ専門家です。{job_hint}

IK 失敗の主要な原因と確認手順：

1. **ログの確認**
   - `get_job_log(job_id='<job_id>', stream='stderr')` でエラーを確認してください。
   - よくあるエラーメッセージ:
     - "Marker not found" → マーカー名の不一致
     - "Time range error" → TRC ファイルの時間範囲外
     - "Model invalid" → モデルの読み込み失敗

2. **setup XML の確認すべきポイント**
   - `model_file` が正しいパスか
   - `marker_file` (.trc) が存在するか
   - `time_range` が TRC ファイルの収録時間内か
   - IKTaskSet にマーカー名が正しいか

3. **マーカー名の照合**
   - `list_components(component_type='markers')` でモデルのマーカー名一覧を取得
   - `inspect_timeseries_file` で TRC のカラム名（マーカー名）を確認
   - モデルとTRCのマーカー名が一致しているか比較してください

4. **修正手順**
   - `write_setup_xml(tool_type='ik', ...)` で setup XML を作り直してください
   - マーカー名が一致しない場合は TRC ファイル側またはモデル側（`set_component_property`）で調整してください

5. **再実行**
   - `run_inverse_kinematics` で再度試してください
"""

    # ------------------------------------------------------------------
    # 3) diagnose_id_failure
    # ------------------------------------------------------------------

    @mcp.prompt()
    def diagnose_id_failure(job_id: str = "") -> str:
        """
        ID (Inverse Dynamics) 失敗時の確認プロンプトを返す。

        Args:
            job_id: 失敗した ID ジョブの ID（省略可）。
        """
        job_hint = f"\nまず `get_job_log('{job_id}', 'stderr')` でエラーログを取得してください。" if job_id else ""
        return f"""あなたは OpenSim Inverse Dynamics (ID) のデバッグ専門家です。{job_hint}

ID 失敗の主要な確認ポイント：

1. **ログの確認**
   - stderr でエラーを確認してください。
   - よくあるエラー:
     - "Coordinates file missing" → .mot ファイルが存在しない
     - "Time range mismatch" → IK 結果と ID の時間範囲が合っていない
     - "NaN/Inf in results" → 数値的不安定性または外力設定の問題

2. **setup XML の確認すべきポイント**
   - `coordinates_file` に IK の出力 .mot が正しく指定されているか
   - `model_file` が IK と同じモデルを指しているか
   - `time_range` が .mot の時間範囲内か
   - `lowpass_cutoff_frequency_for_coordinates` が適切か（推奨: 6 Hz）

3. **外力データについて**
   - 地上歩行など外力が重要な動作では `external_loads_file` (.xml) が必要です
   - `write_setup_xml` の `external_loads_file` パラメータで設定できます

4. **修正手順**
   - `write_setup_xml(tool_type='id', ...)` で setup XML を修正してください
   - `run_inverse_dynamics` で再実行してください

5. **結果の確認**
   - `summarize_results(job_id)` で結果の概要を確認してください
   - `inspect_timeseries_file` で生成された .sto ファイルの内容を確認してください
"""

    # ------------------------------------------------------------------
    # 4) diagnose_dynamic_inconsistency
    # ------------------------------------------------------------------

    @mcp.prompt()
    def diagnose_dynamic_inconsistency() -> str:
        """
        RRA / CMC 実行前に動的整合性を点検するプロンプトを返す。
        """
        return """あなたは OpenSim RRA/CMC のデバッグ専門家です。
RRA/CMC 実行前に動的整合性（Dynamic Inconsistency）を確認してください。

動的整合性とは、運動データとモデルのダイナミクスの一致度です。
不整合が大きいと RRA/CMC が収束しません。

**点検手順:**

1. **残差力・モーメントの確認 (RRA 後)**
   - `summarize_results` または `inspect_timeseries_file` で RRA の出力 .sto を確認してください。
   - 許容範囲の目安:
     - 残差力 (Fx, Fy, Fz): ±10 N 以下
     - 残差モーメント (Mx, My, Mz): ±30 N·m 以下
   - これらを超える場合は不整合が大きい状態です

2. **IK 結果の品質確認**
   - `inspect_timeseries_file` で IK の marker_errors .sto を確認してください。
   - 各マーカー誤差が 2 cm 以下か確認してください

3. **モデルの質量設定の確認**
   - `list_components(component_type='bodies')` で各体節の質量を確認してください。
   - `get_component_properties` で質量・慣性テンソルが被験者データと合致しているか確認してください
   - 合わない場合は `run_scale` でスケーリングを実行してください

4. **外力データの確認**
   - 地上歩行では床反力データが RRA/CMC に必須です
   - `inspect_timeseries_file` で GRF .mot ファイルを確認してください

5. **修正と再実行**
   - 質量の問題 → `set_component_property` で修正、または `run_scale` を実行
   - IK 誤差の問題 → マーカーウェイトを調整して `run_inverse_kinematics` を再実行
   - 外力設定の問題 → setup XML の `external_loads_file` を確認してください
"""

    # ------------------------------------------------------------------
    # 5) review_analysis_results
    # ------------------------------------------------------------------

    @mcp.prompt()
    def review_analysis_results(job_id: str = "") -> str:
        """
        解析結果（.sto / .mot / .trc）のレビュープロンプトを返す。

        Args:
            job_id: レビュー対象のジョブ ID（省略可）。
        """
        job_hint = f"\nまず `get_job_outputs('{job_id}')` で成果物一覧を取得してください。" if job_id else ""
        return f"""あなたは OpenSim 解析結果のレビューアシスタントです。{job_hint}

解析結果を体系的にレビューするための手順：

1. **成果物の確認**
   - `get_job_outputs(job_id='<job_id>')` で生成ファイル一覧を取得してください
   - 期待されるファイル:
     - IK: `*_ik_motion.mot` または `*_ik_result.mot`
     - ID: `*_InverseDynamics.sto` または `*_id_result.sto`
     - SO: `*_StaticOptimization_activation.sto`, `*_StaticOptimization_force.sto`
     - Analyze: `*_BodyKinematics_pos_global.sto` など

2. **タイムシリーズデータの確認**
   - `inspect_timeseries_file(file_path='<path>')` で各ファイルを確認してください
   - 確認ポイント:
     - 時間範囲が期待通りか
     - カラム数が正しいか（座標数/筋肉数と一致するか）
     - NaN や異常値がないか（inspect の preview で確認）

3. **生理学的妥当性の確認**
   - 座標値が可動域内（`get_component_properties` の range と比較）
   - 関節反力が体重の合理的な倍数範囲（歩行: 0.5〜1.5 BW 程度）
   - 筋肉活動 (activation) が 0〜1 の範囲
   - 筋肉力 (force) が max_isometric_force 以下

4. **ログの確認**
   - `get_job_log(job_id='<job_id>', stream='stdout')` で実行ログを確認してください
   - 警告メッセージがあれば注意が必要です

5. **次のステップの提案**
   - IK → ID → SO/RRA → CMC の順で解析を進めてください
   - 各ステップの結果を確認してから次に進むことが重要です
"""

    # ------------------------------------------------------------------
    # 6) diagnose_moco_failure
    # ------------------------------------------------------------------

    @mcp.prompt()
    def diagnose_moco_failure(job_id: str = "") -> str:
        """
        Moco solve 失敗時の原因切り分けプロンプトを返す。

        Args:
            job_id: 失敗した Moco ジョブの ID（省略可）。
        """
        job_hint = (
            f"\nまず `get_job_log('{job_id}', 'stderr')` と "
            f"`summarize_moco_solution('{job_id}')` でエラー詳細を確認してください。"
            if job_id else ""
        )
        return f"""あなたは OpenSim Moco の最適化制御デバッグ専門家です。{job_hint}

Moco solve 失敗の主要な原因と対処法：

1. **収束失敗 (Convergence failure)**
   - エラー: "Convergence failure" / "Maximum iterations reached"
   - 対処:
     - `configure_moco_problem` で `solver_settings.optim_max_iterations` を増やす（例: 3000）
     - `solver_settings.num_mesh_intervals` を減らす（50 → 25）で粗い解から試す
     - 目的関数の重みを調整する（goals の weight を下げる）

2. **Infeasibility (実行不可能)**
   - エラー: "Infeasible" / "bound violation"
   - 対処:
     - `configure_moco_problem` の `bounds` を確認し、座標の範囲が広すぎ/狭すぎないか確認
     - `time_bounds` が運動データの時間範囲と一致しているか確認
     - 筋肉活動の拘束 (0≤a≤1) が自然に満たされるようモデルを確認

3. **モデルエラー**
   - エラー: "Model initialization failed"
   - 対処:
     - `validate_model` でモデルの整合性を確認
     - `create_moco_study` の `model_path` が正しいか確認

4. **参照データの問題**
   - tracking テンプレートで states_ref が設定されていない
   - inverse テンプレートで coordinates_ref が設定されていない
   - `configure_moco_problem` でパスを確認し、`inspect_timeseries_file` でファイルを確認

5. **設定の確認**
   - `list_workspace_files(subdir='setups/moco')` で config.json を特定
   - config.json の内容を確認して template_type と参照ファイルが正しいか確認

6. **推奨デバッグ手順**
   1. `num_mesh_intervals` を小さく（10〜25）してまず収束するか確認
   2. goal の weight を 1 つずつ追加して問題のある goal を特定
   3. 解析ツール（IK/ID/SO）の結果を参照データとして使い、より良い初期解を提供
   4. 段階的に問題を複雑化する（単純な custom → tracking → full CMC → Moco）
"""
