"""
ワークスペース安全操作ユーティリティ。

仕様書 §13 入力制約 に基づき、
- workspace 外パスを拒否
- symlink 経由の workspace 外脱出を阻止
- 許可拡張子のみ受け付ける
"""

from __future__ import annotations

import re
from pathlib import Path

from .config import ALLOWED_EXTENSIONS, WORKSPACE_DIR


# ---------------------------------------------------------------------------
# パス検証
# ---------------------------------------------------------------------------


def resolve_path(path: str) -> Path:
    """
    入力パスを検証し、workspace 配下の絶対パスを返す。

    Parameters
    ----------
    path:
        ワークスペース相対パス、または絶対パス。

    Returns
    -------
    Path
        resolved された絶対パス。

    Raises
    ------
    ValueError
        workspace 外へのアクセスを試みた場合。
    """
    p = Path(path)
    if not p.is_absolute():
        p = WORKSPACE_DIR / p

    # symlink を解決してから判定(§13.1)
    resolved = p.resolve()

    try:
        resolved.relative_to(WORKSPACE_DIR)
    except ValueError:
        raise ValueError(
            f"ワークスペース外へのアクセスは禁止されています: {path!r}\n"
            f"ワークスペース: {WORKSPACE_DIR}"
        )

    return resolved


def resolve_within(base: Path, name: str) -> Path:
    """
    name を base フォルダの中に限って解決する。

    ユーザー入力をファイル名としてつなげる箇所で使う。"../" や絶対パスで base の外に出るものは拒否する。

    Raises
    ------
    ValueError
        base の外を指す場合。
    """
    base = base.resolve()
    target = (base / name).resolve()
    if target != base and base not in target.parents:
        raise ValueError(f"{base.name}/ の外は指定できません: {name!r}")
    return target


# ファイル名や ID に使ってよい名前(英数字・日本語などの文字、_ . -)。区切り文字や ".." は不可
_SAFE_NAME = re.compile(r"[^\W][\w.\-]{0,127}")


def check_safe_name(name: str, what: str = "名前") -> None:
    """
    ファイル名やフォルダ名としてそのまま使う名前を検証する(パスの区切りや "../" での脱出を防ぐ)。

    Raises
    ------
    ValueError
        使えない文字を含む場合。
    """
    if not isinstance(name, str) or not _SAFE_NAME.fullmatch(name) or ".." in name:
        raise ValueError(
            f"{what} に使えない文字が含まれています: {name!r}"
            "(英数字・日本語などの文字と _ . - だけ、128 文字まで。/ や .. は不可)"
        )


# 直接書き換えを禁止する元データの置き場(§4.1)
PROTECTED_DIRS = ("models/original",)


def resolve_output_path(path: str, sources: tuple[Path | str, ...] = ()) -> Path:
    """
    書き込み先のパスを検証し、workspace 配下の絶対パスを返す(§4.1 直接上書きの禁止)。

    resolve_path の検証に加えて、次を拒否する。
      - models/original/ 配下への書き込み
      - 編集元のファイル(sources)そのものへの書き込み
        ただし編集元が models/clones/ 配下の複製なら、上書きを認める(§4.2 複製は編集してよい)

    Raises
    ------
    ValueError
        workspace 外、または保護対象への書き込みの場合。
    """
    dest = resolve_path(path)

    for d in PROTECTED_DIRS:
        protected = (WORKSPACE_DIR / d).resolve()
        if dest == protected or protected in dest.parents:
            raise ValueError(
                f"{d}/ 配下へは書き込めません(元データの直接上書きは禁止): {path!r}\n"
                "models/clones/ や exports/ など別の場所を指定してください"
            )

    clones = (WORKSPACE_DIR / "models/clones").resolve()
    for src in sources:
        if not src:
            continue
        src_path = Path(src).resolve()
        if dest == src_path and clones not in src_path.parents:
            raise ValueError(
                f"編集元のファイルには上書きできません: {path!r}\n"
                "別名で保存してください(例: models/clones/<name>.osim)"
            )

    return dest


# setup XML の中でファイル・フォルダを指すタグ(model_file / force_set_files / results_directory / datafile など)
_SETUP_PATH_TAG = re.compile(r"(_file|_files|_directory)$|^datafile$")
# results_directory を基準に解決される出力ファイル(InverseDynamicsTool)
_RESULTS_RELATIVE_TAGS = frozenset({"output_gen_force_file", "output_body_forces_file"})


def check_setup_paths(setup_path: Path, _follow_xml: bool = True) -> None:
    """
    setup XML の中に書かれたパスが workspace 内を指すか検証する(§13.1)。

    resolve_path が守るのは setup XML 自体の場所だけで、OpenSim は XML の中に書かれたパスを
    そのまま読み書きする。workspace の外を指す setup が置かれていると、外のファイルを読まれたり
    上書きされたりするので、ジョブを作る前に拒否する。参照先の XML(外力・タスクなど)も 1 段だけ確認する。

    Raises
    ------
    ValueError
        workspace の外を指すパスがある場合。
    """
    import xml.etree.ElementTree as ET  # noqa: PLC0415

    try:
        root = ET.parse(setup_path).getroot()
    except ET.ParseError:
        return  # 壊れた XML は OpenSim 側でエラーにさせる

    base = setup_path.parent
    results_dir = next((e.text.strip() for e in root.iter("results_directory") if e.text and e.text.strip()), "")
    workspace_root = WORKSPACE_DIR.resolve()
    outside: list[str] = []
    nested_xml: list[Path] = []

    for elem in root.iter():
        text = (elem.text or "").strip()
        if not isinstance(elem.tag, str) or not _SETUP_PATH_TAG.search(elem.tag) or not text or text == "Unassigned":
            continue
        # *_files は空白区切りのリスト。単一のパスは空白を含むことがあるので分けない
        values = text.split() if elem.tag.endswith("_files") else [text]
        tag_base = base / results_dir if elem.tag in _RESULTS_RELATIVE_TAGS and results_dir else base
        for value in values:
            target = Path(value) if Path(value).is_absolute() else tag_base / value
            target = target.resolve()
            if target != workspace_root and workspace_root not in target.parents:
                outside.append(f"<{elem.tag}> {value}")
            elif _follow_xml and target.suffix.lower() == ".xml" and target.is_file():
                nested_xml.append(target)

    for xml_path in nested_xml:
        try:
            check_setup_paths(xml_path, _follow_xml=False)
        except ValueError as exc:
            outside.append(f"{xml_path.name} の中: {exc}")

    if outside:
        raise ValueError(
            "setup XML に workspace の外を指すパスがあります: " + "; ".join(outside[:5])
            + ("(ほか省略)" if len(outside) > 5 else "")
        )


def check_extension(path: Path) -> None:
    """
    拡張子が許可リストに含まれるか確認する。

    Raises
    ------
    ValueError
        許可されていない拡張子の場合。
    """
    ext = path.suffix.lower()
    if ext not in ALLOWED_EXTENSIONS:
        raise ValueError(
            f"拡張子 '{ext}' は許可されていません。"
            f"許可拡張子: {', '.join(sorted(ALLOWED_EXTENSIONS))}"
        )


# ---------------------------------------------------------------------------
# ワークスペース初期化
# ---------------------------------------------------------------------------


def ensure_workspace_structure() -> None:
    """
    推奨ディレクトリ構成 (§5.2) を作成する。
    既に存在する場合は何もしない。
    """
    subdirs = [
        "models/original",
        "models/clones",
        "setups/scale",
        "setups/ik",
        "setups/id",
        "setups/analyze",
        "setups/moco",
        "data/trc",
        "data/mot",
        "data/sto",
        "data/c3d",
        "jobs",
        "exports",
        "audit",
    ]
    for d in subdirs:
        (WORKSPACE_DIR / d).mkdir(parents=True, exist_ok=True)
