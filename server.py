"""
OpenSim MCP Server — エントリーポイント

仕様書 v1.1 Phase 1-4 実装:
  Tools    : load_model / get_model_summary / list_components /
             get_component_properties / validate_model / clone_model /
             inspect_timeseries_file /
             run_inverse_kinematics / run_inverse_dynamics / run_analyze_tool /
             run_scale / run_static_optimization / run_rra / run_cmc / run_forward_dynamics /
             get_job_status / get_job_outputs / get_job_log / summarize_results /
             list_workspace_files / save_model_copy / write_setup_xml / write_external_loads_xml /
             convert_c3d_to_trc_mot / set_component_property /
             add_component / remove_component / set_socket_connection / export_model /
             create_moco_study / configure_moco_problem / solve_moco_problem / summarize_moco_solution
  Prompts  : review_model_structure / diagnose_ik_failure / diagnose_id_failure /
             diagnose_dynamic_inconsistency / review_analysis_results / diagnose_moco_failure
  Resources: opensim://model/* / opensim://job/{id}/* / opensim://workspace/files /
             opensim://file/summary/{path}

仕様書 §18.1 注意:
  stdio transport では stdout が JSON-RPC 通信路になるため、
  アプリケーションログは必ず stderr に出力すること。
  print() は絶対に使用しない。
"""

import logging
import os
import sys

# ---------------------------------------------------------------------------
# stdout の分離（OpenSim ネイティブ出力対策）
# ---------------------------------------------------------------------------
# OpenSim / Simbody の C++ ログ（"[info] Loaded model ..." 等）は fd 1 に直接書かれ、
# JSON-RPC 通信路を汚す。本来の stdout を複製して sys.stdout（MCP が使う）に残し、
# fd 1 は stderr に付け替える。opensim を import する前に行うこと。
_rpc_stdout_fd = os.dup(sys.stdout.fileno())
os.dup2(sys.stderr.fileno(), sys.stdout.fileno())
sys.stdout = os.fdopen(_rpc_stdout_fd, "w", encoding="utf-8")

from mcp.server.fastmcp import FastMCP

from opensim_mcp.resources.job_resources import register_job_resources
from opensim_mcp.resources.model_resources import register_model_resources
from opensim_mcp.resources.gui_resources import register_gui_resources
from opensim_mcp.tools.analysis_tools import register_analysis_tools
from opensim_mcp.tools.edit_tools import register_edit_tools
from opensim_mcp.tools.file_tools import register_file_tools
from opensim_mcp.tools.job_tools import register_job_tools
from opensim_mcp.tools.model_tools import register_model_tools
from opensim_mcp.tools.moco_tools import register_moco_tools
from opensim_mcp.tools.structural_tools import register_structural_tools
from opensim_mcp.prompts import register_prompts
from opensim_mcp.config import SERVER_VERSION
from opensim_mcp.workspace import ensure_workspace_structure

# ---------------------------------------------------------------------------
# ロギング設定（stderr のみ）
# ---------------------------------------------------------------------------

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    stream=sys.stderr,
)
logger = logging.getLogger("opensim_mcp.server")

# ---------------------------------------------------------------------------
# MCP サーバー初期化
# ---------------------------------------------------------------------------

mcp = FastMCP(
    "opensim-mcp",
    instructions=(
        "OpenSim 4.x の主要操作（モデル読込・閲覧・IK / ID / Scale / SO / RRA / CMC / FD / Analyze実行・"
        "ジョブ管理・データ変換・構造編集・ Moco 最適制御）を"
        "MCP プロトコル経由で安全に提供するサーバーです。\n\n"
        "典型的なフロー:\n"
        "1. load_model でモデルをロードする\n"
        "2. get_model_summary / list_components でモデル構造を確認する\n"
        "3. write_external_loads_xml / write_setup_xml で外力 XML と setup XML を生成する\n"
        "4. run_scale / run_inverse_kinematics / run_inverse_dynamics / "
        "run_static_optimization / run_rra / run_cmc / run_forward_dynamics / run_analyze_tool で解析を実行する\n"
        "5. get_job_status で進捗を確認し、summarize_results で結果を確認する\n\n"
        "Moco: create_moco_study → configure_moco_problem → solve_moco_problem → summarize_moco_solution\n"
        "構造編集: clone_model → add_component / remove_component / set_socket_connection → export_model\n\n"
        "注意: workspace 外のファイルへのアクセスは禁止されています。"
    ),
)
# FastMCP は version を受け取らず、initialize で MCP SDK のバージョン(1.x)を名乗ってしまうので、
# 内側の低レベル Server に本体のバージョンを渡す
mcp._mcp_server.version = SERVER_VERSION

# ---------------------------------------------------------------------------
# ワークスペース初期化
# ---------------------------------------------------------------------------

try:
    ensure_workspace_structure()
    logger.info("Workspace initialized")
except Exception as exc:
    logger.warning("Workspace initialization warning: %s", exc)

# ---------------------------------------------------------------------------
# Tools 登録
# ---------------------------------------------------------------------------

register_model_tools(mcp)
register_file_tools(mcp)
register_analysis_tools(mcp)
register_edit_tools(mcp)
register_structural_tools(mcp)
register_moco_tools(mcp)
register_job_tools(mcp)
logger.info("Tools registered (Phase 1 + Phase 2 + Phase 3 + Phase 4)")

# ---------------------------------------------------------------------------
# Prompts 登録
# ---------------------------------------------------------------------------

register_prompts(mcp)
logger.info("Prompts registered")

# ---------------------------------------------------------------------------
# Resources 登録
# ---------------------------------------------------------------------------

register_model_resources(mcp)
register_job_resources(mcp)
register_gui_resources(mcp)
logger.info("Resources registered")

# ---------------------------------------------------------------------------
# エントリーポイント
# ---------------------------------------------------------------------------


def main() -> None:
    """stdio transport でサーバーを起動する。"""
    logger.info("Starting OpenSim MCP Server v%s (stdio transport)", SERVER_VERSION)
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
