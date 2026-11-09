前提として、OpenSim は 4.x 系で筋骨格モデルの構築・編集・解析・動的シミュレーションを扱え、Python / MATLAB / C++ などから API を利用できる。MCP は tools / resources / prompts を公開するプロトコルで、標準 transport として stdio と Streamable HTTP を定義している。 ([GitHub][1])

---

# OpenSim向けMCPサーバー仕様書 v1.0

## 1. 目的

本サーバーは、OpenSim の主要操作を MCP 経由で安全かつ体系的に提供することを目的とする。
対象には、`.osim` モデルの読取、プロパティ確認、設定XMLの扱い、Scale / IK / ID / Static Optimization / RRA / CMC / Forward Dynamics / Analyze といった OpenSim workflow tools の実行、さらに Moco による最適制御を含む。OpenSim の公式ドキュメントはこれらのワークフローを主要機能として整理している。 ([opensimconfluence.atlassian.net][2])

## 2. この仕様書の到達点

この仕様書を読むことで、少なくとも次を決められる状態を目標とする。

* どの機能を Resource にするか
* どの機能を Tool にするか
* どの操作を read-only に制限すべきか
* どの操作を複製モデル限定にすべきか
* どの処理を job 化すべきか
* どの順で実装すべきか
* どのテストを通せば v1 を出荷できるか

MCP は server 側が resources / tools / prompts を分けて公開する思想を採っており、OpenSim 側も API・スクリプティング・ワークフロー・ファイル変換の責務が比較的はっきりしているため、この分割は実装上かなり相性がよい。 ([Model Context Protocol][3])

---

## 3. 前提技術

### 3.1 OpenSim 側の前提

推奨対象は **OpenSim 4.x** とする。OpenSim の公開リポジトリは 4.x 系の source code を扱っており、Python scripting、workflow tools、C3D 読取、Moco 連携などの現行機能群も 4.x 系の文書で整理されている。 ([GitHub][1])

### 3.2 実装言語

推奨実装言語は **Python** とする。OpenSim は公式に Python scripting を案内しており、Python パッケージ配布、Jupyter notebooks、Pythonic extensions を含む利用導線が整っている。MCP 側にも Python SDK があるため、OpenSim API と MCP server 実装を同一言語でまとめやすい。 ([opensimconfluence.atlassian.net][4])

### 3.3 MCP transport

初期実装は **stdio** を標準とする。MCP 仕様では JSON-RPC を UTF-8 でやり取りし、標準 transport として stdio と Streamable HTTP を定義している。さらにクライアントは可能なら stdio をサポートすべきとされているため、ローカル開発・ローカル利用の起点として最も適している。 ([Model Context Protocol][5])

---

## 4. 設計原則

### 4.1 直接上書きを既定で禁止する

OpenSim の live model editing は強力で、GUI 上でも markers、body masses、muscle max isometric force、joint locations など多くのプロパティを編集できる。
ただし、MCP 経由でこれを無制限に許すと誤編集の影響が大きいため、**既定では元の `.osim` を直接書き換えない**設計を採用する。永続変更は原則として複製モデルか別ファイル出力に限定する。 ([opensimconfluence.atlassian.net][6])

### 4.2 構造編集は clone ベースで行う

OpenSim の scripting ドキュメントでは、モデルへの component 追加は clone / add / load の流れで扱う例が示されている。
そのため、body / force / actuator / probe / controller / custom component の追加や接続変更は、**ロード済みオリジナルではなく clone 後の作業コピーに対してのみ許可する**。 ([opensimconfluence.atlassian.net][7])

### 4.3 重い処理は job 化する

Scale、IK、ID、RRA、CMC、Forward Dynamics、Analyze、Moco solve はいずれも入力設定・ログ・結果ファイルを伴い、失敗時の原因切り分けにもジョブ単位の追跡が必要になる。OpenSim の公式ワークフローでもこれらは段階的な処理として整理されている。したがって、**重量級処理は全て job 管理対象**とする。 ([opensimconfluence.atlassian.net][8])

### 4.4 GUI操作の再現ではなく API 呼び出しを本流にする

OpenSim には GUI、XML、Python、MATLAB、C++ など複数の interface があるが、MCP サーバーは GUI のマウス操作再現ではなく、**OpenSim API と設定ファイルに基づく再現可能な処理系**として設計する。これにより、自動化・テスト・再現性・ログ管理が大きく改善する。 ([SimTK][9])

---

## 5. システム全体像

### 5.1 論理構成

サーバー内部は次の4層に分ける。

* **MCP Interface Layer**
  tools / resources / prompts の公開、入力検証、権限制御
* **Domain Service Layer**
  OpenSim model service、file service、analysis service、moco service
* **Job Orchestration Layer**
  ジョブID払い出し、状態遷移、ログ、出力収集
* **Workspace Layer**
  モデル、設定、結果、サマリ、監査ログの保存

この分離は、MCP の責務と OpenSim の責務を混ぜないために必須である。MCP SDK は protocol メッセージと transports を扱い、OpenSim API はモデル・解析・ファイル処理を扱うため、境界を切っておく方が保守しやすい。 ([GitHub][10])

### 5.2 推奨ディレクトリ構成

```text
workspace/
  models/
    original/
    clones/
  setups/
    scale/
    ik/
    id/
    analyze/
    moco/
  data/
    trc/
    mot/
    sto/
    c3d/
  jobs/
    {job_id}/
      metadata.json
      input_snapshot/
      stdout.log
      stderr.log
      artifacts/
      summary.json
  exports/
  audit/
    audit.log
```

この構成の意図は明確で、**原本・複製・設定・生データ・実行結果・監査ログを混ぜない**ことにある。

---

## 6. 権限モデル

### 6.1 権限レベル

以下の5段階を定義する。

* **read_only**
  閲覧のみ
* **edit**
  非構造編集、Save As、設定XML保存
* **build**
  clone ベースの構造編集
* **run_analysis**
  workflow tools 実行
* **run_moco**
  Moco 実行
* **developer**
  plugin / custom component / 実験機能

### 6.2 危険度分類

危険度は以下の5段階とする。

* **A**: 閲覧のみ、非破壊
* **B**: 一時編集、保存前変更
* **C**: 非構造の永続保存
* **D**: 構造変更
* **E**: 高負荷解析または最適化

この危険度分類は、OpenSim における property editing、C3D変換、component 追加、workflow execution、Moco solve などの性質差に対応するためのものである。 ([opensimconfluence.atlassian.net][6])

---

## 7. OpenSimで対象とする操作群

### 7.1 閲覧系

* モデル読込
* モデル名、version、gravity 取得
* body / joint / coordinate / muscle / force / marker / controller / analysis / probe 一覧取得
* component property 読取
* outputs 読取
* setup XML 読取
* `.sto` `.mot` `.trc` `.c3d` の要約
* job log / output file / result metadata の閲覧

OpenSim の property editor、outputs list、common scripting commands、C3D file adapter 文書から、これらは公式に扱える読み取り系操作として確認できる。 ([opensimconfluence.atlassian.net][11])

### 7.2 編集系

* property 変更
* coordinate 初期値変更
* body mass 等の数値変更
* setup XML 編集
* Save As
* export
* clone model
* add/remove component
* force / actuator / probe / controller 追加
* socket 接続変更

OpenSim 3.0以降の live model editing と scripting 例から、これらは技術的に可能な領域である。 ([opensimconfluence.atlassian.net][6])

### 7.3 解析系

* Scale
* Inverse Kinematics
* Inverse Dynamics
* Static Optimization
* RRA
* CMC
* Forward Dynamics
* Analyze
* JointReaction などの解析

これらは OpenSim workflow の公式ページで主要ツールとして並んでいる。 ([opensimconfluence.atlassian.net][8])

### 7.4 最適制御系

* MocoStudy 構築
* tracking / prediction 問題の構築
* goals, constraints, bounds 設定
* solve
* solution export

OpenSim Moco は musculoskeletal systems の optimal control toolkit として案内されている。 ([GitHub][12])

---

## 8. Resource仕様

### 8.1 Resource設計方針

Resource は「AIに読ませるための文脈」を提供する。
副作用がないもの、または副作用があっても Resource ではなく Tool に寄せるべきものを明確に分離する。

### 8.2 必須Resource一覧

* `opensim://model/summary`
* `opensim://model/bodies`
* `opensim://model/joints`
* `opensim://model/coordinates`
* `opensim://model/muscles`
* `opensim://model/forces`
* `opensim://model/markers`
* `opensim://model/controllers`
* `opensim://model/analyses`
* `opensim://model/probes`
* `opensim://model/properties/{component_name}`
* `opensim://file/summary/{path}`
* `opensim://workspace/files`
* `opensim://job/{job_id}/status`
* `opensim://job/{job_id}/log`
* `opensim://job/{job_id}/outputs`
* `opensim://job/{job_id}/summary`

### 8.3 Resource返却形式

すべての Resource は次の共通形を持つ。

```json
{
  "source": "opensim://model/summary",
  "timestamp": "2026-04-10T12:34:56Z",
  "summary": "27 bodies, 23 joints, 18 muscles",
  "warnings": [],
  "structured_data": {}
}
```

`structured_data` は後段の LLM や別ツールが再利用しやすい形で返す。

---

## 9. Tool仕様

### 9.1 Tool設計方針

Tool は「処理を行う」「ファイルを出力する」「モデルを変える」「ジョブを走らせる」ものに限定する。
すべての Tool は次を満たす。

* 入力は明示的な構造化引数
* ワークスペース外パス禁止
* 成功時も失敗時も JSON 構造で返却
* 危険度と権限レベルを持つ
* 必要なら `job_id` を返す

### 9.2 Tool共通返却形式

```json
{
  "status": "completed",
  "summary": "Inverse Kinematics completed",
  "warnings": [],
  "artifacts": [
    {"path": "jobs/job_001/artifacts/ik_output.mot", "type": "motion"}
  ],
  "structured_data": {}
}
```

---

# 10. Tool一覧表（引数・返り値・危険度・実装優先度つき）

以下の優先度は次の意味とする。

* **P0**: v1で必須
* **P1**: v1.1で推奨
* **P2**: v2候補
* **P3**: 開発者向け拡張

---

## 10.1 閲覧系 Tools

### 1) `load_model`

**目的**: `.osim` モデルをロードし、セッションの current model として扱う。
**権限**: read_only
**危険度**: A
**優先度**: P0

**引数**

* `model_path: str`

**返り値**

* `model_name`
* `model_hash`
* `component_counts`
* `warnings`

**実装メモ**

* パスは workspace 配下のみ
* ロード時に最低限の初期化と妥当性確認を行う

**根拠**
OpenSim API / scripting は current model 取得やモデル操作を前提としている。 ([opensimconfluence.atlassian.net][13])

---

### 2) `get_model_summary`

**目的**: モデル概要を返す。
**権限**: read_only
**危険度**: A
**優先度**: P0

**引数**

* `model_ref: str | null`

**返り値**

* `model_name`
* `num_bodies`
* `num_joints`
* `num_coordinates`
* `num_muscles`
* `num_forces`
* `gravity`
* `notes`

---

### 3) `list_components`

**目的**: 指定カテゴリの component 一覧を返す。
**権限**: read_only
**危険度**: A
**優先度**: P0

**引数**

* `component_type: str`
  許可値: `bodies|joints|coordinates|muscles|forces|markers|controllers|analyses|probes`

**返り値**

* `items: list`

---

### 4) `get_component_properties`

**目的**: 単一 component の property と output 情報を返す。
**権限**: read_only
**危険度**: A
**優先度**: P0

**引数**

* `component_name: str`

**返り値**

* `properties`
* `outputs`
* `component_type`

Property Editor / Outputs List の概念に対応する。 ([opensimconfluence.atlassian.net][11])

---

### 5) `validate_model`

**目的**: モデルの基本妥当性を確認する。
**権限**: read_only
**危険度**: A
**優先度**: P0

**引数**

* `model_ref: str | null`

**返り値**

* `is_valid`
* `errors`
* `warnings`

**実装メモ**

* ロード失敗
* 不正な socket
* 重複名
* 初期化時例外
  などを整理して返す。

---

### 6) `inspect_timeseries_file`

**目的**: `.sto`, `.mot`, `.trc`, `.c3d`, `.csv` の要約を返す。
**権限**: read_only
**危険度**: A
**優先度**: P0

**引数**

* `file_path: str`

**返り値**

* `file_type`
* `time_range`
* `num_rows`
* `num_columns`
* `column_names`
* `preview`

OpenSim は motion / storage ファイルや C3D 読取をサポートする。 ([opensimconfluence.atlassian.net][14])

---

### 7) `list_workspace_files`

**目的**: workspace 内ファイル一覧を返す。
**権限**: read_only
**危険度**: A
**優先度**: P1

**引数**

* `subdir: str | null`
* `extensions: list[str] | null`

**返り値**

* `files`

---

## 10.2 編集・保存系 Tools

### 8) `set_component_property`

**目的**: 単一 property を変更する。
**権限**: edit
**危険度**: B または C
**優先度**: P1

**引数**

* `target_model_path: str`
* `component_name: str`
* `property_name: str`
* `value: any`
* `save_mode: str`
  許可値: `temp|copy`

**返り値**

* `changed: bool`
* `output_model_path`
* `diff_summary`

**実装メモ**

* `temp` はメモリ内変更のみ
* `copy` は clone 出力前提
* original 上書きは禁止

live model editing は公式に可能だが、本サーバーでは必ず安全策を入れる。 ([opensimconfluence.atlassian.net][6])

---

### 9) `clone_model`

**目的**: モデル複製を作成する。
**権限**: build
**危険度**: C
**優先度**: P0

**引数**

* `source_model_path: str`
* `new_name: str`

**返り値**

* `cloned_model_path`

**実装メモ**

* 構造編集の前提操作
* 複製先は `models/clones/`

---

### 10) `save_model_copy`

**目的**: 現在状態を別名保存する。
**権限**: edit
**危険度**: C
**優先度**: P1

**引数**

* `target_model_ref: str`
* `output_path: str`

**返り値**

* `saved_path`

---

### 11) `write_setup_xml`

**目的**: setup XML を新規作成または保存する。
**権限**: edit
**危険度**: C
**優先度**: P1

**引数**

* `tool_type: str`
* `params: dict`
* `output_path: str`

**返り値**

* `saved_path`
* `schema_summary`

File Editor や tool settings の保存運用に対応する。 ([opensimconfluence.atlassian.net][15])

---

### 12) `convert_c3d_to_trc_mot`

**目的**: C3D から OpenSim 用 TRC/MOT/STO を生成する。
**権限**: edit
**危険度**: C
**優先度**: P1

**引数**

* `c3d_path: str`
* `output_dir: str`
* `rotate_data: bool`
* `export_force_data: bool`

**返り値**

* `generated_files`
* `data_summary`

OpenSim は C3DFileAdapter と関連ユーティリティを提供し、marker / force / analog データを扱える。 ([opensimconfluence.atlassian.net][14])

---

## 10.3 構造編集系 Tools

### 13) `add_component`

**目的**: force / actuator / probe / analysis / custom component を追加する。

> **実装上の制約**: OpenSim は Model の AnalysisSet を `.osim` に書き出さない（追加して保存し、読み直すと消える）。
> そのため実装では analysis（BodyKinematics / JointReaction / PointKinematics / ProbeReporter）は拒否し、
> `write_setup_xml(tool_type="analyze")` の `analyses` で指定するよう案内する。
> 対応する component_kind は PointActuator / CoordinateActuator / PathActuator。
**権限**: build
**危険度**: D
**優先度**: P2

**引数**

* `target_model_path: str`
* `component_kind: str`
* `component_spec: dict`
* `output_model_path: str`

**返り値**

* `output_model_path`
* `added_component_name`

---

### 14) `remove_component`

**目的**: 指定 component を削除したモデルコピーを作る。
**権限**: build
**危険度**: D
**優先度**: P2

**引数**

* `target_model_path: str`
* `component_name: str`
* `output_model_path: str`

**返り値**

* `output_model_path`
* `removed: bool`

---

### 15) `set_socket_connection`

**目的**: socket 接続を変更する。
**権限**: build
**危険度**: D
**優先度**: P2

**引数**

* `target_model_path: str`
* `component_name: str`
* `socket_name: str`
* `connect_to: str`
* `output_model_path: str`

**返り値**

* `output_model_path`
* `connection_summary`

---

### 16) `export_model`

**目的**: 編集済みモデルを最終成果物として書き出す。
**権限**: build
**危険度**: C
**優先度**: P2

**引数**

* `target_model_ref: str`
* `output_path: str`

**返り値**

* `saved_path`

clone / add / load ワークフローに沿った構造編集を前提とする。 ([opensimconfluence.atlassian.net][7])

---

## 10.4 解析実行系 Tools

### 17) `run_scale`

**目的**: Scale Tool を実行する。
**権限**: run_analysis
**危険度**: E
**優先度**: P2

**引数**

* `setup_xml_path: str`
* `job_name: str | null`

**返り値**

* `job_id`
* `status`

---

### 18) `run_inverse_kinematics`

**目的**: IK を実行する。
**権限**: run_analysis
**危険度**: E
**優先度**: P0

**引数**

* `setup_xml_path: str`
* `job_name: str | null`

**返り値**

* `job_id`
* `status`

---

### 19) `run_inverse_dynamics`

**目的**: ID を実行する。
**権限**: run_analysis
**危険度**: E
**優先度**: P1

**引数**

* `setup_xml_path: str`
* `job_name: str | null`

**返り値**

* `job_id`
* `status`

---

### 20) `run_static_optimization`

**目的**: Static Optimization を実行する。
**権限**: run_analysis
**危険度**: E
**優先度**: P2

**引数**

* `setup_xml_path: str`

**返り値**

* `job_id`

---

### 21) `run_rra`

**目的**: RRA を実行する。
**権限**: run_analysis
**危険度**: E
**優先度**: P2

**引数**

* `setup_xml_path: str`

**返り値**

* `job_id`

---

### 22) `run_cmc`

**目的**: CMC を実行する。
**権限**: run_analysis
**危険度**: E
**優先度**: P2

**引数**

* `setup_xml_path: str`

**返り値**

* `job_id`

---

### 23) `run_forward_dynamics`

**目的**: Forward Dynamics を実行する。
**権限**: run_analysis
**危険度**: E
**優先度**: P2

**引数**

* `setup_xml_path: str`

**返り値**

* `job_id`

---

### 24) `run_analyze_tool`

**目的**: Analyze Tool を実行する。
**権限**: run_analysis
**危険度**: E
**優先度**: P0

**引数**

* `setup_xml_path: str`
* `job_name: str | null`

**返り値**

* `job_id`
* `status`

OpenSim workflow docs は IK, ID, Static Optimization, RRA, CMC, Forward Dynamics を主要ツールとしてまとめている。 ([opensimconfluence.atlassian.net][8])

---

### 25) `get_job_status`

**目的**: ジョブ状態を返す。
**権限**: read_only
**危険度**: A
**優先度**: P0

**引数**

* `job_id: str`

**返り値**

* `status`
* `started_at`
* `finished_at`
* `progress_hint`

---

### 26) `get_job_outputs`

**目的**: ジョブ成果物一覧を返す。
**権限**: read_only
**危険度**: A
**優先度**: P0

**引数**

* `job_id: str`

**返り値**

* `artifacts`

---

### 27) `get_job_log`

**目的**: ジョブログを返す。
**権限**: read_only
**危険度**: A
**優先度**: P0

**引数**

* `job_id: str`
* `stream: str`
  許可値: `stdout|stderr|summary`

**返り値**

* `log_text`

---

### 28) `summarize_results`

**目的**: 結果ファイル群を要約する。
**権限**: read_only
**危険度**: A
**優先度**: P0

**引数**

* `job_id: str`

**返り値**

* `summary`
* `key_metrics`
* `warnings`
* `next_steps`

---

## 10.5 Moco系 Tools

### 29) `create_moco_study`

**目的**: MocoStudy の土台を作る。
**権限**: run_moco
**危険度**: E
**優先度**: P2

**引数**

* `model_path: str`
* `template_type: str`
  許可値例: `tracking|prediction|custom`

**返り値**

* `study_id`
* `config_path`

---

### 30) `configure_moco_problem`

**目的**: goals / bounds / constraints を設定する。
**権限**: run_moco
**危険度**: E
**優先度**: P2

**引数**

* `study_id: str`
* `config_patch: dict`

**返り値**

* `config_path`
* `config_summary`

---

### 31) `solve_moco_problem`

**目的**: Moco solve を実行する。
**権限**: run_moco
**危険度**: E
**優先度**: P2

**引数**

* `study_id: str`

**返り値**

* `job_id`

---

### 32) `summarize_moco_solution`

**目的**: Moco の解を要約する。
**権限**: read_only
**危険度**: A
**優先度**: P2

**引数**

* `job_id: str`

**返り値**

* `objective_value`
* `constraints_status`
* `solution_files`
* `summary`

Moco は direct collocation による optimal control toolkit として案内されている。 ([GitHub][12])

---

## 10.6 開発者向け拡張 Tools

### 33) `load_plugin`

**目的**: OpenSim plugin を読み込む。
**権限**: developer
**危険度**: E
**優先度**: P3

### 34) `register_custom_component_template`

**目的**: custom component のテンプレート定義を登録する。
**権限**: developer
**危険度**: D
**優先度**: P3

これは一般利用向け v1 の対象外とする。plugin や custom component は拡張性が高い一方で、実行環境依存と保守負荷が大きい。 ([opensimconfluence.atlassian.net][16])

---

## 11. Prompt仕様

以下の Prompt を標準搭載する。

* `review_model_structure`
  モデルの body / joint / coordinate / muscle 構造レビュー
* `diagnose_ik_failure`
  IK失敗時の一次診断
* `diagnose_id_failure`
  ID失敗時の設定確認
* `diagnose_dynamic_inconsistency`
  RRA / CMC 前の整合性点検
* `review_analysis_results`
  `.sto` / `.mot` / `.trc` とログを踏まえた結果レビュー
* `diagnose_moco_failure`
  Moco solve 失敗時の原因切り分け

Prompt は再利用可能な分析テンプレートであり、MCP の正式要素の一つである。 ([Model Context Protocol][3])

---

## 12. ジョブ管理仕様

### 12.1 状態遷移

ジョブ状態は以下のみ許可する。

* `queued`
* `running`
* `completed`
* `failed`
* `cancelled`

### 12.2 必須保存物

各ジョブディレクトリには最低限次を保存する。

* `metadata.json`
* `input_snapshot/`
* `stdout.log`
* `stderr.log`
* `artifacts/`
* `summary.json`

### 12.3 metadata.json の必須項目

* `job_id`
* `tool_name`
* `created_at`
* `started_at`
* `finished_at`
* `status`
* `input_files`
* `output_files`
* `opensim_version`
* `server_version`

---

## 13. 入力制約

### 13.1 パス制約

* workspace 外パスは拒否
* `..` を含む相対パスは正規化後に再判定
* symlink 経由で workspace 外へ出る場合は拒否

### 13.2 拡張子制約

許可拡張子は原則として以下に限定する。

* `.osim`
* `.xml`
* `.trc`
* `.mot`
* `.sto`
* `.c3d`
* `.csv`
* `.json`
* `.log`

### 13.3 自然言語直接実行の禁止

「右膝を少し曲げる」のような自然言語を直接 OpenSim API に渡さず、
必ず MCP クライアント側で `component_name`, `property_name`, `value` に分解してから Tool を呼ぶ。

---

## 14. エラーモデル

### 14.1 エラー分類

* `INPUT_ERROR`
  引数不足、不正型、不正拡張子
* `PATH_ERROR`
  ワークスペース外アクセス
* `MODEL_ERROR`
  モデル読込失敗、初期化失敗
* `CONFIG_ERROR`
  setup XML 不備
* `EXECUTION_ERROR`
  OpenSim tool 実行失敗
* `TIMEOUT_ERROR`
  実行時間超過
* `RESOURCE_LIMIT_ERROR`
  並列数・メモリ・ディスク制限
* `PLUGIN_ERROR`
  plugin / custom component 起因

### 14.2 返却形式

```json
{
  "status": "failed",
  "error_code": "CONFIG_ERROR",
  "summary": "IK setup XML is missing marker file",
  "warnings": [],
  "artifacts": [],
  "structured_data": {
    "retryable": true,
    "suggested_fix": "Provide a valid TRC marker file path"
  }
}
```

---

## 15. テスト仕様

### 15.1 単体テスト

* `load_model` が valid / invalid `.osim` を正しく判定
* `inspect_timeseries_file` が `.sto`, `.mot`, `.trc`, `.c3d` を識別
* path 正規化が workspace 外アクセスを拒否
* `set_component_property` が `temp` と `copy` を分離

### 15.2 結合テスト

* clone → property change → export の一連動作
* setup XML 作成 → IK 実行 → log / outputs 取得
* invalid XML → failed job → log 保持
* C3D 変換 → 生成ファイル確認

### 15.3 受け入れテスト

v1 の出荷条件は最低でも次とする。

* `.osim` をロードして model summary を返せる
* body / joint / coordinate / muscle 一覧を返せる
* `.sto` / `.mot` / `.trc` を要約できる
* IK をジョブ実行できる
* Analyze をジョブ実行できる
* job status / outputs / log / summary を取得できる
* original file を直接上書きしない

---

## 16. 実装優先順位

### Phase 1: v1 最小成立

* `load_model`
* `get_model_summary`
* `list_components`
* `get_component_properties`
* `validate_model`
* `inspect_timeseries_file`
* `run_inverse_kinematics`
* `run_analyze_tool`
* `get_job_status`
* `get_job_outputs`
* `get_job_log`
* `summarize_results`
* `clone_model`

### Phase 2: v1.1 実用拡張

* `run_inverse_dynamics`
* `write_setup_xml`
* `save_model_copy`
* `set_component_property`
* `convert_c3d_to_trc_mot`
* `list_workspace_files`

OpenSim 4.5 では C3DFileAdapter の強化もあり、データ準備系まで踏み込む価値が高い。 ([opensimconfluence.atlassian.net][17])

### Phase 3: v2

* `run_scale`
* `run_static_optimization`
* `run_rra`
* `run_cmc`
* `run_forward_dynamics`
* `add_component`
* `remove_component`
* `set_socket_connection`
* `export_model`

### Phase 4: v3

* `create_moco_study`
* `configure_moco_problem`
* `solve_moco_problem`
* `summarize_moco_solution`
* developer tools

---

## 17. 非機能要件

### 17.1 再現性

同一モデル、同一設定XML、同一入力データ、同一 OpenSim version で同じジョブを再実行可能であること。OpenSim は設定XML駆動のワークフローが多く、再現性のために snapshot 保存が重要である。 ([opensimconfluence.atlassian.net][2])

### 17.2 可観測性

失敗時には、ユーザーが「設定ミスか」「入力データ不備か」「モデル問題か」を見分けられること。
そのため、stderr / summary / artifacts / structured error を必須化する。

### 17.3 拡張性

OpenSim は Python / MATLAB / C++ API や custom component 開発が可能であるため、server 側も tool registry と service layer を分離して拡張しやすくする。 ([opensimconfluence.atlassian.net][18])

---

## 18. 実装時の注意

### 18.1 stdout の扱い

stdio transport を使う以上、MCP メッセージと通常ログを混ぜない。
アプリログは stderr または job log に出す。MCP の stdio は JSON-RPC の通信路そのものである。 ([Model Context Protocol][5])

### 18.2 GUI依存を持ち込まない

OpenSim GUI の Property Editor や File Editor は便利やが、MCP サーバー側は GUI に依存せず API / XML / file adapters で完結させる。これにより headless 実行と自動テストが可能になる。 ([opensimconfluence.atlassian.net][11])

### 18.3 Moco は分離する

Moco は solve の難度と計算負荷が高く、tracking / prediction / custom optimal control まで広がるため、OpenSim 基本 workflow と同じ運用権限にしない。
Moco docs は getting started と user guide を別立てで用意しており、通常 workflow とは別の学習コストがあることがうかがえる。 ([opensim-org.github.io][19])

---

## 19. 最終結論

この仕様書に基づく OpenSim MCP server は、次の思想で作る。

* **読むものは Resource と read-only Tool に寄せる**
* **変更は original に直接入れない**
* **構造変更は clone 前提**
* **重い処理は必ず job 化**
* **Moco は別権限・別フェーズ**
* **API / XML ベースで再現可能に作る**
* **v1 は閲覧 + IK/Analyze + job管理に絞る**

この方針なら、OpenSim の公式機能範囲に沿いつつ、安全性・実装容易性・拡張性のバランスが一番ええ。 ([opensimconfluence.atlassian.net][8])

---

次に進めるなら、次は
**「この仕様書をそのままコードに落とすための Python プロジェクト構成と MCP server 雛形」**
を書けば、実装開始まで一気に持っていける。

[1]: https://github.com/opensim-org/opensim-core "opensim-org/opensim-core: SimTK OpenSim C++ libraries ... - GitHub"
[2]: https://opensimconfluence.atlassian.net/wiki/spaces/OpenSim/pages/53088203/Guide%2Bto%2BOpenSim%2BWorkflow%2Band%2BTools "Overview of the OpenSim Workflow"
[3]: https://modelcontextprotocol.io/specification/2025-11-25 "Specification"
[4]: https://opensimconfluence.atlassian.net/wiki/spaces/OpenSim/pages/53085346/Scripting%2Bin%2BPython "Scripting in Python - OpenSim"
[5]: https://modelcontextprotocol.io/specification/2025-06-18/basic/transports "Transports"
[6]: https://opensimconfluence.atlassian.net/wiki/spaces/OpenSim/pages/53090508/New%2BFeatures%2Bin%2BOpenSim%2B3.0 "New Features in OpenSim 3.0"
[7]: https://opensimconfluence.atlassian.net/wiki/spaces/OpenSim/pages/53088704 "OpenSim Documentation - OpenSim"
[8]: https://opensimconfluence.atlassian.net/wiki/spaces/OpenSim24/pages/54001688/Guide%2Bto%2BOpenSim%2BWorkflow%2Band%2BTools "Overview of the OpenSim Workflow"
[9]: https://simtk.org/api_docs/opensim/api_docs/md_doc_APIGuide.html "API Guide - SimTK"
[10]: https://github.com/modelcontextprotocol/python-sdk "MCP Python SDK"
[11]: https://opensimconfluence.atlassian.net/wiki/spaces/OpenSim/pages/53089902/Property%2BEditor%2Band%2BOutputs%2BList "Opening the Property Editor - OpenSim"
[12]: https://github.com/opensim-org/opensim-moco/blob/master/README.md "opensim-moco/README.md at master"
[13]: https://opensimconfluence.atlassian.net/wiki/spaces/OpenSim/pages/53089391/Common%2BScripting%2BCommands "Common Scripting Commands - OpenSim"
[14]: https://opensimconfluence.atlassian.net/wiki/spaces/OpenSim/pages/53090837/C3D%2B.c3d%2BFiles "Reading C3D files through Matlab - OpenSim"
[15]: https://opensimconfluence.atlassian.net/wiki/spaces/OpenSim/pages/53088813/Menus "Menus - OpenSim"
[16]: https://opensimconfluence.atlassian.net/wiki/spaces/OpenSim/pages/53084230 "OpenSim Documentation - OpenSim"
[17]: https://opensimconfluence.atlassian.net/wiki/spaces/OpenSim/pages/53091109/What%2Bs%2BNew%2Bin%2BOpenSim%2B4.5 "What's New in OpenSim 4.5?"
[18]: https://opensimconfluence.atlassian.net/wiki/spaces/OpenSim/pages/53089359/Scripting "Scripting - OpenSim"
[19]: https://opensim-org.github.io/opensim-moco-site/docs/0.4.0/mocouserguide.html "OpenSim Moco: Moco User Guide"
