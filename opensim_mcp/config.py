"""
サーバー全体の設定値。
環境変数 OPENSIM_MCP_WORKSPACE でワークスペースパスを上書きできる。
"""

import os
from pathlib import Path

#: ワークスペースルート（全ファイル操作はここ以下に限定）
WORKSPACE_DIR: Path = Path(
    os.environ.get(
        "OPENSIM_MCP_WORKSPACE",
        str(Path(__file__).parent.parent / "workspace"),
    )
).resolve()

#: サーバーバージョン
SERVER_VERSION: str = "0.1.0"

#: ジョブの最大並列数
MAX_CONCURRENT_JOBS: int = int(os.environ.get("OPENSIM_MCP_MAX_JOBS", "3"))

#: ワークスペース内で許可するファイル拡張子
ALLOWED_EXTENSIONS: frozenset[str] = frozenset(
    {".osim", ".xml", ".trc", ".mot", ".sto", ".c3d", ".csv", ".json", ".log"}
)
