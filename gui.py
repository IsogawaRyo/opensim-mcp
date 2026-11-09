#!/usr/bin/env python3
"""
OpenSim MCP のローカル GUI ランチャー。

- `mcp dev server.py` を起動して Inspector を開く
- `gui/viewer.html`（リポジトリ内）と workspace のファイルを配信するローカル HTTP サーバーを起動する
- 3D ビューアから利用する軽量 API を、MCP tools/resources の薄いプロキシとして提供する
"""

from __future__ import annotations

import argparse
import asyncio
import json
import re
import shutil
import subprocess
import sys
import threading
import time
import webbrowser
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, quote, unquote, urlparse

import server as mcp_server

# 3D ビューアの画面。workspace/ は git 管理外なので、リポジトリ内に置いたものを配信する
VIEWER_HTML = Path(__file__).parent / "gui" / "viewer.html"

# ビューア用 HTTP サーバーが受け付ける Host ヘッダー
_ALLOWED_HOSTS = {"localhost", "127.0.0.1", "::1"}

# グローバル状態
mcp_context = {
    "token": None,
    "proxy_port": 6277,
    "inspector_url": None,
}

server_context = {
    "project_root": None,
    "workspace_dir": None,
    "session_name": None,
    "session_source": None,
    "default_model": None,
    "default_motion": None,
    "default_marker_errors": None,
    "default_id": None,
    "default_so_activation": None,
    "default_so_force": None,
    "current_model_path": None,
    "timeseries_cache": {},
    "frame_cache": {},
    "visual_cache": {},
}


def _first_existing_path(*candidates: Path) -> Path | None:
    filtered = [candidate for candidate in candidates if candidate is not None]
    for candidate in filtered:
        if candidate.exists():
            return candidate
    return filtered[0] if filtered else None


def _workspace_relative(path: Path | None) -> str | None:
    if path is None:
        return None

    workspace_dir = server_context["workspace_dir"]
    if workspace_dir is None:
        return str(path)

    try:
        return str(path.relative_to(workspace_dir))
    except ValueError:
        return str(path)


def _resolve_requested_path(raw_path: str | None, fallback: Path | None = None) -> Path:
    if raw_path:
        path = Path(unquote(raw_path))
    elif fallback is not None:
        path = fallback
    else:
        raise ValueError("path is required")

    workspace_dir = server_context["workspace_dir"]
    if workspace_dir is None:
        raise RuntimeError("workspace directory is not configured")
    if not path.is_absolute():
        path = workspace_dir / path

    # HTTP の ?path= から workspace の外(絶対パスや ../)を読ませない
    resolved = path.resolve()
    workspace_root = Path(workspace_dir).resolve()
    if resolved != workspace_root and workspace_root not in resolved.parents:
        raise ValueError(f"workspace の外は指定できません: {raw_path or path}")
    return resolved


def _unwrap_tool_result(result: object) -> dict:
    if isinstance(result, tuple) and len(result) == 2 and isinstance(result[1], dict):
        return result[1]
    if isinstance(result, dict):
        return result
    raise TypeError(f"unexpected MCP tool result: {type(result)!r}")


def _call_mcp_tool(name: str, arguments: dict) -> dict:
    return _unwrap_tool_result(asyncio.run(mcp_server.mcp.call_tool(name, arguments)))


def _read_mcp_resource_json(uri: str) -> dict:
    contents = asyncio.run(mcp_server.mcp.read_resource(uri))
    if not contents:
        raise RuntimeError(f"resource returned no content: {uri}")
    return json.loads(list(contents)[0].content)


def _resource_uri(prefix: str, path: Path) -> str:
    relative_path = _workspace_relative(path)
    if relative_path is None:
        raise ValueError("resource path is not configured")
    return f"{prefix}/{quote(relative_path, safe='')}"


def _load_model_via_mcp(model_path: Path) -> None:
    model_key = str(model_path.resolve())
    if server_context["current_model_path"] == model_key:
        return

    data = _call_mcp_tool("load_model", {"model_path": _workspace_relative(model_path)})
    if data.get("status") != "completed":
        raise RuntimeError(data.get("summary", "load_model failed"))
    server_context["current_model_path"] = model_key


def _read_model_visual_cached(model_path: Path) -> list[dict]:
    stat = model_path.stat()
    cache_key = str(model_path)
    cache_sig = (stat.st_mtime_ns, stat.st_size)
    cached = server_context["visual_cache"].get(cache_key)
    if cached and cached["sig"] == cache_sig:
        return cached["data"]

    _load_model_via_mcp(model_path)
    resource = _read_mcp_resource_json("opensim://model/visual")
    data = resource["structured_data"]
    server_context["visual_cache"][cache_key] = {"sig": cache_sig, "data": data}
    return data


def _read_timeseries_cached(path: Path) -> dict:
    stat = path.stat()
    cache_key = str(path)
    cache_sig = (stat.st_mtime_ns, stat.st_size)
    cached = server_context["timeseries_cache"].get(cache_key)
    if cached and cached["sig"] == cache_sig:
        return cached["data"]

    resource = _read_mcp_resource_json(_resource_uri("opensim://timeseries", path))
    data = resource["structured_data"]
    server_context["timeseries_cache"][cache_key] = {"sig": cache_sig, "data": data}
    return data


def _read_marker_errors_cached(path: Path) -> dict:
    stat = path.stat()
    cache_key = f"marker-errors:{path}"
    cache_sig = (stat.st_mtime_ns, stat.st_size)
    cached = server_context["timeseries_cache"].get(cache_key)
    if cached and cached["sig"] == cache_sig:
        return cached["data"]

    resource = _read_mcp_resource_json(_resource_uri("opensim://model/visual/marker_errors", path))
    data = resource["structured_data"]
    server_context["timeseries_cache"][cache_key] = {"sig": cache_sig, "data": data}
    return data


def _read_motion_frames_cached(model_path: Path, motion_path: Path) -> dict:
    model_stat = model_path.stat()
    motion_stat = motion_path.stat()
    cache_key = f"{model_path}|{motion_path}"
    cache_sig = (
        model_stat.st_mtime_ns,
        model_stat.st_size,
        motion_stat.st_mtime_ns,
        motion_stat.st_size,
    )
    cached = server_context["frame_cache"].get(cache_key)
    if cached and cached["sig"] == cache_sig:
        return cached["data"]

    _load_model_via_mcp(model_path)
    resource = _read_mcp_resource_json(_resource_uri("opensim://model/visual/frames", motion_path))
    payload = resource["structured_data"]
    if isinstance(payload, list):
        payload = {
            "path": _workspace_relative(motion_path),
            "frame_count": len(payload),
            "frames": payload,
        }
    payload["motion_path"] = _workspace_relative(motion_path)
    server_context["frame_cache"][cache_key] = {"sig": cache_sig, "data": payload}
    return payload


def _guess_marker_errors_path(motion_path: Path | None) -> Path | None:
    if motion_path is None:
        return None
    candidate = motion_path.with_suffix("")
    candidate = candidate.with_name(f"{candidate.name}_marker_errors.sto")
    return candidate if candidate.exists() else None


def _resolve_job_artifacts(job_id: str) -> list[Path]:
    payload = _call_mcp_tool("get_job_outputs", {"job_id": job_id})
    artifacts = payload.get("artifacts", [])
    return [_resolve_requested_path(artifact["path"]) for artifact in artifacts]


def _select_job_artifact(job_id: str, predicate, label: str) -> Path | None:
    for path in _resolve_job_artifacts(job_id):
        if predicate(path):
            return path
    print(f"[GUI] {label} was not found in job {job_id}")
    return None


def configure_server_context(project_root: Path, args: argparse.Namespace) -> None:
    workspace_dir = project_root / "workspace"

    server_context["project_root"] = project_root
    server_context["workspace_dir"] = workspace_dir
    server_context["current_model_path"] = None
    server_context["timeseries_cache"].clear()
    server_context["frame_cache"].clear()
    server_context["visual_cache"].clear()

    # 表示するファイルは引数(--model / --ik など)かジョブ ID(--ik-job など)で指定する
    server_context["default_model"] = None
    server_context["default_motion"] = None
    server_context["default_marker_errors"] = None
    server_context["default_id"] = None
    server_context["default_so_activation"] = None
    server_context["default_so_force"] = None

    if args.model:
        server_context["default_model"] = _resolve_requested_path(args.model)
    if args.ik:
        server_context["default_motion"] = _resolve_requested_path(args.ik)
    if args.marker_errors:
        server_context["default_marker_errors"] = _resolve_requested_path(args.marker_errors)
    if args.id:
        server_context["default_id"] = _resolve_requested_path(args.id)
    if args.so_activation:
        server_context["default_so_activation"] = _resolve_requested_path(args.so_activation)
    if args.so_force:
        server_context["default_so_force"] = _resolve_requested_path(args.so_force)

    session_source = "mcp-session"

    if args.ik_job:
        server_context["default_motion"] = _select_job_artifact(
            args.ik_job,
            lambda path: path.suffix.lower() == ".mot",
            "IK motion output",
        )
        marker_errors = _select_job_artifact(
            args.ik_job,
            lambda path: path.suffix.lower() == ".sto" and "marker_errors" in path.name.lower(),
            "IK marker error output",
        )
        if marker_errors is not None:
            server_context["default_marker_errors"] = marker_errors
        session_source = "mcp-jobs"

    if args.id_job:
        server_context["default_id"] = _select_job_artifact(
            args.id_job,
            lambda path: path.suffix.lower() == ".sto" and "body_forces" not in path.name.lower(),
            "ID generalized force output",
        )
        session_source = "mcp-jobs"

    if args.so_job:
        server_context["default_so_activation"] = _select_job_artifact(
            args.so_job,
            lambda path: path.suffix.lower() == ".sto" and "activation" in path.name.lower(),
            "SO activation output",
        )
        server_context["default_so_force"] = _select_job_artifact(
            args.so_job,
            lambda path: path.suffix.lower() == ".sto" and "force" in path.name.lower(),
            "SO force output",
        )
        session_source = "mcp-jobs"

    if server_context["default_marker_errors"] is None:
        server_context["default_marker_errors"] = _guess_marker_errors_path(server_context["default_motion"])

    if any([args.ik_job, args.id_job, args.so_job]):
        if any([args.model, args.ik, args.id, args.so_activation, args.so_force, args.marker_errors]):
            session_source = "mcp-session"
        else:
            session_source = "mcp-jobs"
    elif any([args.model, args.ik, args.id, args.so_activation, args.so_force, args.marker_errors]):
        session_source = "mcp-paths"

    server_context["session_name"] = args.session_name or "opensim-mcp"
    server_context["session_source"] = session_source


def preload_default_model() -> None:
    model_path = server_context["default_model"]
    if model_path and model_path.exists():
        try:
            _load_model_via_mcp(model_path)
            print(f"[GUI] Loaded default model via MCP: {model_path}")
        except Exception as exc:
            print(f"[GUI] Default model preload failed: {exc}")


def setup_geometry_dir(project_root: Path, model_path: Path | None = None) -> None:
    """モデルの Geometry ファイルを workspace/Geometry に集約する。"""
    geom_dest = project_root / "workspace" / "Geometry"
    geom_dest.mkdir(parents=True, exist_ok=True)

    search_paths: list[Path] = []
    if model_path is not None:
        search_paths.extend([model_path.parent / "Geometry", model_path.parent])

    search_paths.extend([
        project_root / "workspace/models/original/Geometry",
        project_root / "workspace/models/Geometry",
    ])

    copied = 0
    seen: set[Path] = set()
    for src_dir in search_paths:
        if src_dir in seen or not src_dir.exists() or not src_dir.is_dir():
            continue
        seen.add(src_dir)
        vtps = list(src_dir.glob("*.vtp"))
        if not vtps:
            continue
        print(f"[GUI] Syncing geometry from: {src_dir}")
        for vtp in vtps:
            dest_file = geom_dest / vtp.name
            if not dest_file.exists():
                shutil.copy2(vtp, dest_file)
                copied += 1

    if copied == 0:
        print("[GUI] Geometry sync skipped; no .vtp files were found in known locations.")


def start_visualizer_server(project_root: Path) -> None:
    """3D ビューアと結果表示用の HTTP サーバーを起動する。"""
    workspace_dir = project_root / "workspace"

    class OpenSimViewerHandler(SimpleHTTPRequestHandler):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, directory=str(workspace_dir), **kwargs)

        def _host_allowed(self) -> bool:
            # localhost 以外の Host での要求は拒否する(DNS rebinding で外部のページから読まれるのを防ぐ)
            host = (self.headers.get("Host") or "").rsplit(":", 1)[0].strip("[]")
            if host in _ALLOWED_HOSTS:
                return True
            self.send_error(403, "Forbidden host")
            return False

        def do_HEAD(self):
            if self._host_allowed():
                super().do_HEAD()

        def do_GET(self):
            if not self._host_allowed():
                return
            parsed = urlparse(self.path)
            if parsed.path == "/api/config":
                self._send_json(
                    {
                        "session_name": server_context["session_name"],
                        "session_source": server_context["session_source"],
                        "model_path": _workspace_relative(server_context["default_model"]),
                        "ik_path": _workspace_relative(server_context["default_motion"]),
                        "marker_errors_path": _workspace_relative(server_context["default_marker_errors"]),
                        "id_path": _workspace_relative(server_context["default_id"]),
                        "so_activation_path": _workspace_relative(server_context["default_so_activation"]),
                        "so_force_path": _workspace_relative(server_context["default_so_force"]),
                    }
                )
                return

            if parsed.path == "/api/model/visual":
                self._handle_model_visual(parsed)
                return

            if parsed.path == "/api/model/frames":
                self._handle_model_frames(parsed)
                return

            if parsed.path == "/api/model/marker_errors":
                self._handle_marker_errors(parsed)
                return

            if parsed.path == "/api/timeseries":
                self._handle_timeseries(parsed)
                return

            if parsed.path in ("/", "/viewer.html"):
                body = VIEWER_HTML.read_bytes()
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
                return

            super().do_GET()

        def _handle_model_visual(self, parsed):
            try:
                query = parse_qs(parsed.query)
                raw_model = query.get("model", [None])[0]
                model_path = _resolve_requested_path(raw_model, server_context["default_model"])
                bodies = _read_model_visual_cached(model_path)
                self._send_json(
                    {
                        "model_path": _workspace_relative(model_path),
                        "bodies": bodies,
                    }
                )
            except Exception as exc:
                self._send_json({"error": str(exc)}, status=500)

        def _handle_model_frames(self, parsed):
            try:
                query = parse_qs(parsed.query)
                raw_model = query.get("model", [None])[0]
                raw_motion = query.get("motion", [None])[0]
                model_path = _resolve_requested_path(raw_model, server_context["default_model"])
                motion_path = _resolve_requested_path(raw_motion, server_context["default_motion"])
                payload = _read_motion_frames_cached(model_path, motion_path)
                self._send_json(
                    {
                        "motion_path": payload.get("motion_path", _workspace_relative(motion_path)),
                        "frame_count": payload.get("frame_count", len(payload.get("frames", []))),
                        "frames": payload.get("frames", []),
                    }
                )
            except Exception as exc:
                self._send_json({"error": str(exc)}, status=500)

        def _handle_timeseries(self, parsed):
            try:
                query = parse_qs(parsed.query)
                raw_path = query.get("path", [None])[0]
                path = _resolve_requested_path(raw_path)
                data = _read_timeseries_cached(path)
                self._send_json(
                    {
                        "path": _workspace_relative(path),
                        "rows": len(data["time"]),
                        "labels": data["labels"],
                        "time": data["time"],
                        "columns": data["columns"],
                    }
                )
            except Exception as exc:
                self._send_json({"error": str(exc)}, status=500)

        def _handle_marker_errors(self, parsed):
            try:
                query = parse_qs(parsed.query)
                raw_path = query.get("path", [None])[0]
                path = _resolve_requested_path(raw_path, server_context["default_marker_errors"])
                data = _read_marker_errors_cached(path)
                self._send_json(
                    {
                        "path": _workspace_relative(path),
                        "rows": len(data["time"]),
                        "labels": data["labels"],
                        "time": data["time"],
                        "columns": data["columns"],
                    }
                )
            except Exception as exc:
                self._send_json({"error": str(exc)}, status=500)

        def _send_json(self, payload: dict, status: int = 200):
            body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    # この PC からだけ接続できるようにする("" だと同じネットワークの他人からも読める)
    httpd = ThreadingHTTPServer(("127.0.0.1", 8000), partial(OpenSimViewerHandler))
    print("[GUI] Viewer server is listening on http://localhost:8000/viewer.html")
    httpd.serve_forever()


def launch_inspector(project_root: Path):
    python_exe = sys.executable
    server_script = project_root / "server.py"
    mcp_bin = Path(python_exe).parent / "mcp"
    cmd = [str(mcp_bin) if mcp_bin.exists() else "mcp", "dev", str(server_script)]

    print("--- OpenSim MCP GUI (Inspector) を起動しています ---")
    print(f"コマンド実行: {' '.join(cmd)}")

    try:
        proc = subprocess.Popen(
            cmd,
            cwd=project_root,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
        )
    except Exception as exc:
        print(f"[GUI] Inspector launch failed: {exc}")
        return None

    def monitor_logs():
        token_pattern = re.compile(r"Session token: ([a-f0-9]+)")
        url_pattern = re.compile(r"http://localhost:6274/\?MCP_PROXY_AUTH_TOKEN=[a-f0-9]+")

        assert proc.stdout is not None
        for line in iter(proc.stdout.readline, ""):
            print(line, end="")

            token_match = token_pattern.search(line)
            if token_match:
                mcp_context["token"] = token_match.group(1)
                print(f"\n[GUI] Captured auth token: {mcp_context['token'][:8]}...")

            url_match = url_pattern.search(line)
            if url_match:
                mcp_context["inspector_url"] = url_match.group(0)

    threading.Thread(target=monitor_logs, daemon=True).start()
    return proc


def _parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="OpenSim MCP GUI launcher")
    parser.add_argument("--session-name", help="Viewer session name")
    parser.add_argument("--model", help="Model path (.osim), workspace-relative or absolute")
    parser.add_argument("--ik", help="IK motion path (.mot/.sto), workspace-relative or absolute")
    parser.add_argument("--ik-job", help="MCP IK job id to resolve outputs from")
    parser.add_argument("--marker-errors", help="IK marker error path (.sto), workspace-relative or absolute")
    parser.add_argument("--id", help="ID result path (.sto), workspace-relative or absolute")
    parser.add_argument("--id-job", help="MCP ID job id to resolve outputs from")
    parser.add_argument("--so-activation", help="SO activation result path (.sto), workspace-relative or absolute")
    parser.add_argument("--so-force", help="SO force result path (.sto), workspace-relative or absolute")
    parser.add_argument("--so-job", help="MCP SO job id to resolve outputs from")
    parser.add_argument("--no-browser", action="store_true", help="Do not open browser windows automatically")
    return parser.parse_args(argv)


def main() -> None:
    args = _parse_args(sys.argv[1:])
    project_root = Path(__file__).parent.resolve()

    configure_server_context(project_root, args)
    setup_geometry_dir(project_root, server_context["default_model"])
    preload_default_model()

    vis_thread = threading.Thread(target=start_visualizer_server, args=(project_root,), daemon=True)
    vis_thread.start()

    viewer_url = "http://localhost:8000/viewer.html"
    print("\n" + "=" * 48)
    print(f"Session:   {server_context['session_name']}")
    print(f"Source:    {server_context['session_source']}")
    print(f"Viewer:    {viewer_url}")
    print("Inspector: waiting for MCP token...")
    print("=" * 48 + "\n")

    proc = launch_inspector(project_root)

    start_wait = time.time()
    while time.time() - start_wait < 15:
        if mcp_context["inspector_url"]:
            break
        time.sleep(1)

    if not args.no_browser:
        webbrowser.open(viewer_url)
        if mcp_context["inspector_url"]:
            print(f"[GUI] Opening Inspector: {mcp_context['inspector_url']}")
            webbrowser.open(mcp_context["inspector_url"])
    elif mcp_context["inspector_url"]:
        print(f"[GUI] Inspector URL: {mcp_context['inspector_url']}")

    try:
        while True:
            if proc is not None and proc.poll() is not None:
                print(f"[GUI] Inspector exited with status {proc.returncode}. Viewer server stays alive.")
                proc = None
            time.sleep(1)
    except KeyboardInterrupt:
        print("\n[GUI] Shutting down...")
        if proc is not None:
            proc.terminate()


if __name__ == "__main__":
    main()
