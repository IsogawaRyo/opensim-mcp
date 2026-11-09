# OpenSim MCP Server

OpenSim 4.x の主要操作を **MCP (Model Context Protocol)** 経由で安全かつ再現可能に提供するサーバー。  
仕様書 `specification.md` v1.1 に基づき、**Phase 1〜4 の全ツール・Prompts が実装済み**です。

> **非公式のツールです。** Stanford 大学の OpenSim チームとは関係がなく、承認も受けていません。
> OpenSim 本体は [opensim-org/opensim-core](https://github.com/opensim-org/opensim-core)（Apache License 2.0）です。
> 研究で使う場合は、下の「[引用](#引用)」にある OpenSim の論文を引用してください。

---

## 目次

1. [前提条件](#1-前提条件)
2. [環境構築（conda）](#2-環境構築conda)
3. [サーバーの起動方法](#3-サーバーの起動方法)
4. [MCP クライアントとの接続](#4-mcp-クライアントとの接続)
5. [ワークスペースの準備](#5-ワークスペースの準備)
6. [ツール一覧（33個）](#6-ツール一覧33個)
7. [Prompts（6個）](#7-prompts6個)
8. [トラブルシューティング](#8-トラブルシューティング)
9. [開発者向け情報](#9-開発者向け情報)

---

## 1. 前提条件

| 項目 | 要件 |
|------|------|
| OS | macOS 12 以降 / Ubuntu 20.04 以降 / Windows 10 以降 |
| CPU | Intel x86_64 または Apple Silicon (M1/M2/M3) |
| Python | 3.11（conda で管理） |
| conda | Anaconda または Miniconda がインストール済みであること |
| OpenSim | 4.x（conda でインストール） |

> **Apple Silicon (M1/M2/M3) を使用している場合**、x86_64 エミュレーション（Rosetta 2）が必要です（詳細は [セクション 2](#2-環境構築conda) 参照）。

---

## 2. 環境構築（conda）

### 2-1. リポジトリのクローン

```bash
git clone https://github.com/IsogawaRyo/opensim-mcp.git
cd opensim-mcp
```

### 2-2. conda 仮想環境の作成

```bash
conda env create -f environment.yml
```

> 初回は OpenSim のダウンロードに数分かかります。  
> `conda` コマンドが見つからない場合は `/opt/miniconda3/bin/conda` のようにフルパスで指定してください。

### 2-3. 環境のアクティベート

```bash
conda activate opensim_mcp
# conda コマンドが PATH にない場合:
# source /opt/miniconda3/etc/profile.d/conda.sh && conda activate opensim_mcp
```

プロンプトが `(opensim_mcp)` に変わっていれば成功です。

### 2-4. インストールの確認

```bash
python -c "import opensim; print(opensim.GetVersionAndDate())"
python -c "import mcp; print('mcp OK')"
```

OpenSim のバージョン文字列と `mcp OK` が表示されれば環境は正常です。

---

### Apple Silicon (M1/M2/M3/M4) の場合

opensim-org チャンネルは Apple Silicon 向け（osx-arm64）の OpenSim を配布しているので、
上の手順のままネイティブで動きます（OpenSim 4.5 / arm64 で動作確認済み）。

古い版の OpenSim を使うなどで arm64 版が無い場合だけ、Rosetta 2（x86_64 エミュレーション）で環境を作ります。

```bash
CONDA_SUBDIR=osx-64 conda env create -f environment.yml
conda activate opensim_mcp
conda config --env --set subdir osx-64
```

---

### 環境の更新・削除

```bash
# 環境を最新の environment.yml に合わせて更新
conda env update -f environment.yml --prune

# 環境を完全に削除して作り直す
conda env remove -n opensim_mcp
conda env create -f environment.yml
```

---

## 3. サーバーの起動方法

### 基本的な起動（stdio モード）

```bash
conda activate opensim_mcp
python server.py
```

サーバーは **stdio** で起動し、MCP クライアントからの JSON-RPC 入力を待ちます。  
通常、このコマンドを直接実行することはなく、MCP クライアント（Claude Desktop / Cursor など）が自動的に起動します。

> **注意**: `server.py` を起動しても、stdio transport の性質上ターミナルには何も表示されません。これは正常な動作です。ログは stderr に出力されます。

### ログを確認しながら起動する（デバッグ用）

```bash
conda activate opensim_mcp
python server.py 2>server.log &
tail -f server.log
```

### MCP CLI でテスト起動する

`mcp[cli]` をインストールしている場合、開発用のインスペクターが使えます。

```bash
conda activate opensim_mcp
mcp dev server.py
```

ブラウザで `http://127.0.0.1:6274` が開き、Tool / Resource / Prompt を GUI から試せます。

### 開発用 GUI 起動ツール (gui.py)

1 コマンドで GUI を起動し、自動的にブラウザを開くためのスクリプトを用意しています。

```bash
conda activate opensim_mcp
python gui.py
```

この GUI を通じて、解析結果の統計値の確認や、時系列データの JSON プレビューが容易に行えます。

---

## 4. MCP クライアントとの接続

### Claude Desktop との接続

Claude Desktop の設定ファイル（`~/Library/Application Support/Claude/claude_desktop_config.json`）に以下を追記します。

```json
{
  "mcpServers": {
    "opensim-mcp": {
      "command": "/opt/miniconda3/envs/opensim_mcp/bin/python",
      "args": ["/path/to/opensim-mcp/server.py"],
      "env": {
        "OPENSIM_MCP_WORKSPACE": "/path/to/your/workspace"
      }
    }
  }
}
```

> **`command` のパスを確認する方法**:
> ```bash
> conda activate opensim_mcp
> which python
> # 出力例: /opt/miniconda3/envs/opensim_mcp/bin/python
> ```

変更後は Claude Desktop を再起動してください。

---

### Cursor との接続

Cursor の MCP 設定（`.cursor/mcp.json` または設定 UI）に以下を追加します。

```json
{
  "mcpServers": {
    "opensim-mcp": {
      "command": "/opt/miniconda3/envs/opensim_mcp/bin/python",
      "args": ["/path/to/opensim-mcp/server.py"]
    }
  }
}
```

---

### Claude Code との接続

```bash
claude mcp add opensim_mcp --scope user \
  -e OPENSIM_MCP_WORKSPACE=/path/to/your/workspace \
  -- /opt/miniconda3/envs/opensim_mcp/bin/python /path/to/opensim-mcp/server.py
```

`claude mcp list` で `✔ Connected` と出れば接続できています。ツールは次に開いたセッションから使えます。

---

### Codex との接続

`~/.codex/config.toml` に以下を追記します。

```toml
[mcp_servers.opensim_mcp]
command = "/opt/miniconda3/envs/opensim_mcp/bin/python"
args = ["/path/to/opensim-mcp/server.py"]

[mcp_servers.opensim_mcp.env]
OPENSIM_MCP_WORKSPACE = "/path/to/your/workspace"
```

---

## 5. ワークスペースの準備

サーバーはファイル操作を `OPENSIM_MCP_WORKSPACE` 環境変数で指定したディレクトリ内に限定します。  
以下の構造を事前に作成しておくと整理しやすいです。

```
workspace/
├── models/
│   ├── original/        # 元の .osim ファイルを置く場所
│   └── clones/          # MCP サーバーが複製モデルを保存する場所
├── setups/
│   ├── scale/
│   ├── ik/
│   ├── id/
│   ├── analyze/
│   ├── moco/            # Moco Study の config.json を保存
│   └── fd/
├── data/
│   ├── trc/             # マーカーデータ
│   ├── mot/             # モーションデータ
│   ├── sto/             # ストレージファイル
│   └── c3d/             # C3D ファイル
├── jobs/                # ジョブ実行結果（自動生成）
│   └── {job_id}/
│       ├── metadata.json
│       ├── stdout.log
│       ├── stderr.log
│       └── ...
└── exports/             # エクスポート成果物
```

一括作成コマンド:

```bash
mkdir -p workspace/{models/{original,clones},setups/{scale,ik,id,analyze,moco,fd},data/{trc,mot,sto,c3d},jobs,exports}
```

---

## 6. ツール一覧（33個）

### モデル操作・閲覧（Phase 1-2）

| ツール | 説明 |
|--------|------|
| `load_model` | .osim ファイルをロードしてキャッシュ |
| `get_model_summary` | モデルの統計（体節数、筋数など）を取得 |
| `list_components` | Body / Joint / Muscle 等の一覧取得 |
| `get_component_properties` | 指定コンポーネントのプロパティ詳細を取得 |
| `validate_model` | モデルの整合性チェック |
| `clone_model` | 作業コピーを作成 |
| `set_component_property` | プロパティ値（質量、最大筋力など）の変更 |
| `save_model_copy` | 変更後のモデルを別名で保存 |

### 解析実行（Phase 1-3）

| ツール | OpenSim クラス | 説明 |
|--------|--------------|------|
| `run_inverse_kinematics` | `InverseKinematicsTool` | 逆運動学を非同期実行 |
| `run_inverse_dynamics` | `InverseDynamicsTool` | 逆動力学を非同期実行 |
| `run_analyze_tool` | `AnalyzeTool` | 解析ツールを非同期実行 |
| `run_scale` | `ScaleTool` | スケーリングを非同期実行 |
| `run_static_optimization` | `AnalyzeTool` + SO | 静的最適化を非同期実行 |
| `run_rra` | `RRATool` | 残差低減アルゴリズムを非同期実行 |
| `run_cmc` | `CMCTool` | Computed Muscle Control を非同期実行 |
| `run_forward_dynamics` | `ForwardTool` | 順動力学を非同期実行 |
| `write_setup_xml` | — | IK / ID / Analyze / Scale 用 Setup XML を生成 |

### ファイル・データ処理（Phase 1-2）

| ツール | 説明 |
|--------|------|
| `inspect_timeseries_file` | .sto / .mot / .trc / .c3d / .csv の要約を表示 |
| `list_workspace_files` | ワークスペース内のファイルをフィルタリングして表示 |
| `convert_c3d_to_trc_mot` | C3D ファイルを TRC / MOT に変換 |
| `write_external_loads_xml` | 床反力などの外力を定義する ExternalLoads XML を生成 |

### 構造編集（Phase 3）

| ツール | 説明 |
|--------|------|
| `add_component` | モデルにコンポーネント（Actuator / Analysis 等）を追加 |
| `remove_component` | ForceSet / AnalysisSet からコンポーネントを削除 |
| `set_socket_connection` | Socket 接続先を変更 |
| `export_model` | 最終成果物として exports/ に書き出し |

### Moco 最適制御（Phase 4）

| ツール | 説明 |
|--------|------|
| `create_moco_study` | MocoStudy を作成（tracking / inverse / custom） |
| `configure_moco_problem` | goals / bounds / constraints / solver を設定 |
| `solve_moco_problem` | 最適化を非同期実行 |
| `summarize_moco_solution` | 解の objective 値・出力ファイルを要約 |

### ジョブ管理（Phase 1）

| ツール | 説明 |
|--------|------|
| `get_job_status` | ジョブの進捗（queued / running / completed）を確認 |
| `get_job_outputs` | ジョブで生成された成果物リストを取得 |
| `get_job_log` | stdout / stderr ログを取得 |
| `summarize_results` | ジョブ結果のサマリを表示 |

---

## 7. Prompts（6個）

LLM が OpenSim 解析を体系的に進めるための再利用可能なテンプレートです。

| Prompt | 用途 |
|--------|------|
| `review_model_structure` | 体節・関節・筋肉・マーカー構造の系統的レビュー |
| `diagnose_ik_failure` | IK 失敗時のマーカー不一致・時間範囲エラー診断 |
| `diagnose_id_failure` | ID 失敗時の座標ファイル・外力設定確認 |
| `diagnose_dynamic_inconsistency` | RRA/CMC 前の残差力・モデル整合性点検 |
| `review_analysis_results` | .sto/.mot の生理学的妥当性確認 |
| `diagnose_moco_failure` | Moco 収束失敗・Infeasibility の原因切り分け |

### Resource 一覧

| URI テンプレート | 説明 |
|------------------|------|
| `opensim://model/*` | モデルの構造・詳細プロパティ（JSON） |
| `opensim://job/{id}/*` | ジョブの状態、ログ、成果物（JSON/Text） |
| `opensim://timeseries/{path}` | **[NEW]** 時系列データ（.sto, .mot, .trc）の全件 JSON 配信 |
| `opensim://workspace/files` | ワークスペース内のファイルツリー |
| `opensim://file/summary/{path}` | 時系列ファイル（.sto / .mot / .trc / .c3d / .csv）の要約。path は URL エンコード |

---

## 8. トラブルシューティング

### `import opensim` でエラーが出る

**原因候補 1**: conda 環境がアクティブになっていない。

```bash
conda activate opensim_mcp
```

**原因候補 2**: Apple Silicon で x86_64 エミュレーションになっていない。

```bash
CONDA_SUBDIR=osx-64 conda env create -f environment.yml
```

**原因候補 3**: `opensim-org` チャンネルが参照されていない。

```bash
conda install -c opensim-org opensim
```

---

### Claude Desktop がサーバーを認識しない

1. `claude_desktop_config.json` の JSON に構文エラーがないか確認
2. `command` に指定したパスが正しいか確認 (`which python` で確認)
3. Claude Desktop を完全に終了して再起動
4. ログを確認:
   ```bash
   tail -f ~/Library/Logs/Claude/mcp*.log
   ```

---

### ジョブが `failed` になる

ジョブの stderr ログを確認してください。

```bash
# MCP 経由で確認する場合
# get_job_log(job_id="<job_id>", stream="stderr")

# または直接ファイルで確認
cat workspace/jobs/<job_id>/stderr.log

# OpenSim が理由をコンソールに出した場合や、実行プロセスごと落ちた場合はこちら
cat workspace/jobs/<job_id>/runner.log
```

よくある原因:
- setup XML のパスが存在しない
- モデルファイルと入力ファイルの時間範囲が合っていない
- `write_setup_xml` で生成した XML のパラメータが不正
- ツールは成功を返したが、出力先に成果物が書かれなかった（出力パスを確認する）

---

### `mcp dev server.py` でポートが使用中と言われる

```bash
# 既存プロセスを確認して終了する
lsof -i :6274
kill -9 <PID>
```

---

### サーバーが起動しているかテストしたい

```bash
conda activate opensim_mcp
echo '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-11-25","capabilities":{},"clientInfo":{"name":"test","version":"0.0.1"}}}' | python server.py 2>/dev/null
```

正常であれば JSON-RPC レスポンスが返ってきます。

---

## 9. 開発者向け情報

### プロジェクト構成

```
opensim-mcp/
├── server.py                          # エントリーポイント（33 tools + 6 prompts）
├── opensim_mcp/
│   ├── _job_runner.py                 # 子プロセス用ジョブランナー（スレッドセーフ対策）
│   ├── job_service.py                 # ジョブライフサイクル管理（create/run/status/log）
│   ├── model_service.py               # モデルキャッシュ・操作
│   ├── workspace.py                   # パスセキュリティ・検証
│   ├── config.py                      # 設定（WORKSPACE_DIR 等）
│   ├── schemas.py                     # 共通返却フォーマット
│   ├── prompts.py                     # MCP Prompts（6種）
│   └── tools/
│       ├── model_tools.py             # Phase 1: 閲覧系 (load/summary/list...)
│       ├── file_tools.py              # Phase 1: ファイル系 (inspect)
│       ├── analysis_tools.py          # Phase 1-3: 解析実行系 (IK/ID/SO/RRA/CMC/FD)
│       ├── job_tools.py               # Phase 1: ジョブ管理系
│       ├── edit_tools.py              # Phase 2: 編集・保存系
│       ├── structural_tools.py        # Phase 3: 構造編集系
│       └── moco_tools.py              # Phase 4: Moco 最適制御系
├── tests/
│   ├── run_mcp_full_check.py          # MCP 全機能テスト（全 Tools / Prompts / Resources）
│   ├── run_mcp_external_loads_e2e_test.py  # 外力 + ID + SO の MCP E2E テスト
│   ├── setup_ik_test.py               # IK E2E テストの下準備
│   ├── run_ik_e2e_test.py             # IK E2E テスト
│   ├── run_id_e2e_test.py             # ID E2E テスト
│   └── test_property_editor.py        # プロパティ編集テスト
├── specification.md                   # 設計仕様書 v1.1
├── environment.yml                    # conda 環境定義
└── README.md                          # このファイル
```

### テストの実行

全機能テストは、全 Tools / Prompts / Resources を MCP（stdio）経由で実際に呼びます。

```bash
conda activate opensim_mcp
python tests/run_mcp_full_check.py
```

workspace は毎回一時フォルダに新しく作るので、`workspace/` や本番の workspace には触れません。
全フェーズで 1〜数分かかります（Moco の求解が大半）。失敗が 1 つでもあれば終了コード 1 を返します。

| オプション | 内容 |
|------------|------|
| `--phases basic,jobs` | 一部のフェーズだけ実行する（`basic` / `jobs` / `dynamics` / `moco` / `meta` / `security`） |
| `--models-dir <dir>` | OpenSim GUI のサンプル置き場を指定する |
| `--keep` | 一時 workspace を残す（失敗の調査用） |

使うデータ:

- OpenSim Python パッケージ同梱のテストデータ（`arm26.osim`、`walking2.c3d` など）
- OpenSim GUI 付属のサンプル `~/Documents/OpenSim/<版>/Models/Gait10dof18musc`
  （見つからない場合、`jobs` / `dynamics` / `moco` はスキップします）

### アーキテクチャの注意点

OpenSim の C++ ライブラリはスレッドセーフではないため、**すべての解析ツール**（IK / ID / Scale / SO / RRA / CMC / FD / Analyze / Moco）は `job_service` を介して **独立した子プロセス (`_job_runner.py`)** として実行されます。これにより、メインサーバープロセスのセグメンテーションフォルトを防止しています。

典型的なフロー:

```
MCP クライアント
    ↓ run_inverse_kinematics(setup_xml_path)
server.py (メインプロセス)
    ↓ job_service.create_job() + run_job()
_job_runner.py (子プロセス)  ← OpenSim C++ はここでのみ実行
    ↓ metadata.json に結果を書き込み
server.py
    ↓ get_job_status() / get_job_outputs()
MCP クライアント
```

### Moco のフロー

```
create_moco_study(model_path, template_type)
    → setups/moco/{study_id}/config.json を作成
configure_moco_problem(study_id, config_patch)
    → config.json を更新（goals / bounds / solver_settings）
solve_moco_problem(study_id)
    → _job_runner.py で MocoTrack / MocoInverse / MocoStudy を実行
    → jobs/{job_id}/moco_solution.sto に保存
summarize_moco_solution(job_id)
    → objective 値・出力ファイルを返す
```

### 実装フェーズ

| Phase | 内容 | ツール数 | 状態 |
|-------|------|---------|------|
| Phase 1 (v1) | モデル閲覧・IK/Analyze・ジョブ管理 | 13 | ✅ 完了 |
| Phase 2 (v1.1) | ID・setup XML・外力 XML・property 編集・C3D 変換 | 7 | ✅ 完了 |
| Phase 3 (v2) | Scale・SO・RRA・CMC・FD・構造編集 | 9 | ✅ 完了 |
| Phase 4 (v3) | Moco 最適制御・Prompts | 4 tools + 6 prompts | ✅ 完了 |

**合計: 33 tools + 6 prompts**

---

## 引用

このサーバーは OpenSim の機能を呼び出すだけです。研究で使った場合は、OpenSim の論文を引用してください
（[OpenSim のライセンスと引用のお願い](https://opensimconfluence.atlassian.net/wiki/spaces/OpenSim/pages/53086437/License+for+OpenSim+4.0+and+Later)）。

- Seth A, et al. OpenSim: Simulating musculoskeletal dynamics and neuromuscular control to study human and animal movement. *PLoS Computational Biology* 14(7): e1006223, 2018. https://doi.org/10.1371/journal.pcbi.1006223
- Delp SL, et al. OpenSim: Open-source software to create and analyze dynamic simulations of movement. *IEEE Transactions on Biomedical Engineering* 54(11): 1940–1950, 2007. https://doi.org/10.1109/TBME.2007.901024
- Moco を使った場合: Dembia CL, et al. OpenSim Moco: Musculoskeletal optimal control. *PLoS Computational Biology* 16(12): e1008493, 2020. https://doi.org/10.1371/journal.pcbi.1008493

---

## ライセンス

MIT License。全文は [LICENSE](LICENSE) を参照してください。
