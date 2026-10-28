"""Get the code snapshot into the session filesystem, safely.

The archive comes from the trusted orchestrator, but we still treat it as untrusted
input: no absolute paths, no '..', no links or device files, and hard size caps.
"""

from __future__ import annotations

import base64
import io
import os
import shutil
import stat
import tarfile
import urllib.request
import zipfile

MAX_ARCHIVE_BYTES = int(os.environ.get("MAX_ARCHIVE_BYTES", str(200 * 1024 * 1024)))
MAX_EXTRACTED_BYTES = int(os.environ.get("MAX_EXTRACTED_BYTES", str(1024 * 1024 * 1024)))
MAX_FILES = int(os.environ.get("MAX_FILES", "100000"))
DOWNLOAD_TIMEOUT_S = 120


class ArchiveError(Exception):
    pass


def load_archive(repo: dict) -> bytes:
    """Return archive bytes from either a pre-signed HTTPS URL or inline base64."""
    if repo.get("archive_b64"):
        data = base64.b64decode(repo["archive_b64"], validate=True)
    elif repo.get("archive_url"):
        url = repo["archive_url"]
        if not url.startswith("https://"):
            raise ArchiveError("archive_url must be https")
        req = urllib.request.Request(url, method="GET")
        with urllib.request.urlopen(req, timeout=DOWNLOAD_TIMEOUT_S) as resp:  # noqa: S310 (scheme checked)
            data = resp.read(MAX_ARCHIVE_BYTES + 1)
    else:
        raise ArchiveError("payload.repo needs archive_url or archive_b64")
    if len(data) > MAX_ARCHIVE_BYTES:
        raise ArchiveError("archive too large")
    return data


def _check_member_name(name: str) -> str:
    norm = os.path.normpath(name.replace("\\", "/")).lstrip("/")
    if name.startswith(("/", "\\")) or norm.startswith("..") or os.path.isabs(norm) or ":" in norm.split("/")[0]:
        raise ArchiveError(f"unsafe path in archive: {name!r}")
    return norm


def extract(data: bytes, dest: str) -> str:
    """Extract zip or tar(.gz) bytes into dest. Returns the repository root directory."""
    os.makedirs(dest, exist_ok=True)
    total = 0
    count = 0

    def budget(size: int) -> None:
        nonlocal total, count
        total += size
        count += 1
        if total > MAX_EXTRACTED_BYTES or count > MAX_FILES:
            raise ArchiveError("archive exceeds extraction limits")

    if data[:4] == b"PK\x03\x04":
        with zipfile.ZipFile(io.BytesIO(data)) as zf:
            for info in zf.infolist():
                name = _check_member_name(info.filename)
                mode = (info.external_attr >> 16) & 0o170000
                if mode and not (stat.S_ISREG(mode) or stat.S_ISDIR(mode)):
                    continue  # skip symlinks and special files
                target = os.path.join(dest, name)
                if info.is_dir():
                    os.makedirs(target, exist_ok=True)
                    continue
                budget(info.file_size)
                os.makedirs(os.path.dirname(target), exist_ok=True)
                with zf.open(info) as src, open(target, "wb") as out:
                    shutil.copyfileobj(src, out)
    else:
        try:
            tf = tarfile.open(fileobj=io.BytesIO(data), mode="r:*")
        except tarfile.TarError as exc:
            raise ArchiveError("archive is neither zip nor tar") from exc
        with tf:
            for member in tf.getmembers():
                name = _check_member_name(member.name)
                if name in ("", "."):
                    continue
                target = os.path.join(dest, name)
                if member.isdir():
                    os.makedirs(target, exist_ok=True)
                    continue
                if not member.isfile():
                    continue  # skip symlinks, hard links, devices, fifos
                budget(member.size)
                os.makedirs(os.path.dirname(target), exist_ok=True)
                src = tf.extractfile(member)
                if src is None:
                    continue
                with src, open(target, "wb") as out:
                    shutil.copyfileobj(src, out)

    # Many archivers wrap everything in one top-level folder; use it as the root.
    entries = [e for e in os.listdir(dest) if not e.startswith(".")]
    if len(entries) == 1 and os.path.isdir(os.path.join(dest, entries[0])) and len(os.listdir(dest)) == 1:
        return os.path.join(dest, entries[0])
    return dest
