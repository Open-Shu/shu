"""Plugin installer/validator utilities for package upload (.zip/.tgz).

Scope: minimal validator for current manifest.py contract used by loader.
Security: path traversal guard; admin-only endpoints should call these helpers.
"""

from __future__ import annotations

import importlib.util
import io
import shutil
import tarfile
import tempfile
import zipfile
from pathlib import Path


class InstallError(Exception):
    pass


def _is_safe_member(dest_dir: Path, target: Path) -> bool:
    try:
        dest_dir = dest_dir.resolve()
        target = target.resolve()
        return str(target).startswith(str(dest_dir))
    except Exception:
        return False


def _safe_extract_zip(zf: zipfile.ZipFile, dest: Path) -> None:
    for info in zf.infolist():
        # Reject absolute or parent path entries
        name = info.filename
        if name.startswith("/") or name.startswith("\\"):
            raise InstallError("archive contains absolute paths")
        if ".." in Path(name).parts:
            raise InstallError("archive contains parent path traversal entries")
        target = dest / name
        # Ensure target stays within dest (defense-in-depth)
        if not _is_safe_member(dest, target):
            raise InstallError("archive entry escapes destination directory")
        if info.is_dir():
            # Explicit directory entry
            target.mkdir(parents=True, exist_ok=True)
            continue
        # File entry
        target.parent.mkdir(parents=True, exist_ok=True)
        with zf.open(info, "r") as src, open(target, "wb") as out:
            shutil.copyfileobj(src, out)


def _safe_extract_tar(tf: tarfile.TarFile, dest: Path) -> None:
    for member in tf.getmembers():
        name = member.name
        if name.startswith("/") or name.startswith("\\"):
            raise InstallError("archive contains absolute paths")
        if ".." in Path(name).parts:
            raise InstallError("archive contains parent path traversal entries")
        target = dest / name
        target_parent = target.parent
        target_parent.mkdir(parents=True, exist_ok=True)
        if member.isdir():
            target.mkdir(parents=True, exist_ok=True)
        elif member.isfile():
            with tf.extractfile(member) as src, open(target, "wb") as out:
                if src is None:
                    raise InstallError("failed to read archive entry")
                shutil.copyfileobj(src, out)
        # ignore symlinks and special files for now


def _find_plugin_root(extract_dir: Path) -> Path:
    # Require a single top-level directory
    children = [p for p in extract_dir.iterdir() if p.is_dir()]
    if len(children) == 1:
        return children[0]
    # If multiple, prefer one that contains manifest.py
    candidates = [p for p in children if (p / "manifest.py").exists()]
    if len(candidates) == 1:
        return candidates[0]
    raise InstallError("package must contain a single top-level plugin directory with manifest.py")


def _load_manifest(plugin_root: Path) -> dict:
    manifest_py = plugin_root / "manifest.py"
    if not manifest_py.exists():
        raise InstallError("manifest.py not found in plugin package")
    spec = importlib.util.spec_from_file_location("_uploaded_plugin_manifest", str(manifest_py))
    if spec is None or spec.loader is None:
        raise InstallError("failed to load manifest.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)  # type: ignore[attr-defined]
    m = getattr(mod, "PLUGIN_MANIFEST", None)
    if not isinstance(m, dict):
        raise InstallError("PLUGIN_MANIFEST dict not found in manifest.py")
    # minimal required fields
    name = m.get("name")
    entry = m.get("module")
    if not name or not entry:
        raise InstallError("manifest missing required fields: 'name' and 'module'")
    return m


def validate_and_extract(archive_bytes: bytes) -> tuple[Path, Path, dict, list[str]]:
    """Extract upload to temp dir and return (temp_dir, plugin_root, manifest, warnings)
    Caller must cleanup temp_dir when done.
    """
    temp_dir = Path(tempfile.mkdtemp(prefix="shu_plugin_upload_"))
    extract_dir = temp_dir / "extract"
    extract_dir.mkdir(parents=True, exist_ok=True)

    # Try ZIP first
    warnings: list[str] = []
    try:
        with zipfile.ZipFile(io.BytesIO(archive_bytes)) as zf:
            _safe_extract_zip(zf, extract_dir)
    except zipfile.BadZipFile:
        # Try TAR/TGZ
        try:
            with tarfile.open(fileobj=io.BytesIO(archive_bytes), mode="r:*") as tf:
                _safe_extract_tar(tf, extract_dir)
        except tarfile.TarError:
            shutil.rmtree(temp_dir, ignore_errors=True)
            raise InstallError("unsupported or corrupted archive; only .zip and .tar(.gz) are accepted")

    plugin_root = _find_plugin_root(extract_dir)
    manifest = _load_manifest(plugin_root)

    # Sanity check: entry path prefix should align with folder name
    entry = manifest.get("module", "")
    folder = plugin_root.name
    if isinstance(entry, str) and not entry.startswith(f"plugins.{folder}."):
        warnings.append(
            f"manifest.module '{entry}' does not start with 'plugins.{folder}.'; import may fail after install"
        )

    return temp_dir, plugin_root, manifest, warnings


def install_plugin(plugin_root: Path, plugins_root: Path, *, force: bool = False) -> Path:
    """Move the extracted plugin folder into plugins_root/<plugin_name>.
    Returns final install path.
    """
    if not plugin_root.is_dir():
        raise InstallError("plugin_root is not a directory")
    name = plugin_root.name
    dest = plugins_root / name
    if dest.exists():
        if not force:
            raise InstallError(f"plugin '{name}' already exists; use force to overwrite")
        # Remove existing then replace
        shutil.rmtree(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.move(str(plugin_root), str(dest))
    return dest
