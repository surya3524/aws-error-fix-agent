"""Download a repository snapshot from Azure DevOps as a zip (no git binary needed).

Uses the Git "Items - Get" REST endpoint with Accept: application/zip, the same call the
official Microsoft SDK makes in `GitClient.get_item_zip` (azure-devops 7.1.0b4:
location fb93c0db-..., api-version 7.1-preview.1, params path / recursionLevel /
versionDescriptor.*). Auth: PAT via HTTP Basic with an empty user name.
"""

from __future__ import annotations

import base64
import urllib.parse
import urllib.request

API_VERSION = "7.1-preview.1"  # VERIFY: GA "7.1" may also work; this is what the MS SDK sends.
TIMEOUT_S = 120


class SnapshotTooLarge(Exception):
    pass


def build_items_zip_url(org: str, project: str, repo: str, version: str, version_type: str) -> str:
    if version_type not in ("commit", "branch", "tag"):
        raise ValueError("version_type must be commit, branch or tag")
    base = (
        f"https://dev.azure.com/{urllib.parse.quote(org, safe='')}/"
        f"{urllib.parse.quote(project, safe='')}/_apis/git/repositories/"
        f"{urllib.parse.quote(repo, safe='')}/items"
    )
    params = {
        "path": "/",
        "recursionLevel": "Full",
        "versionDescriptor.version": version,
        "versionDescriptor.versionType": version_type,
        "download": "true",
        "api-version": API_VERSION,
    }
    return f"{base}?{urllib.parse.urlencode(params)}"


def download_zip(org: str, project: str, repo: str, version: str, version_type: str,
                 pat: str, max_bytes: int) -> bytes:
    url = build_items_zip_url(org, project, repo, version, version_type)
    token = base64.b64encode(f":{pat}".encode()).decode()
    req = urllib.request.Request(url, headers={
        "Authorization": f"Basic {token}",
        "Accept": "application/zip",
    })
    with urllib.request.urlopen(req, timeout=TIMEOUT_S) as resp:  # noqa: S310 (fixed https host)
        data = resp.read(max_bytes + 1)
    if len(data) > max_bytes:
        raise SnapshotTooLarge(f"repository zip exceeds {max_bytes} bytes")
    if data[:4] != b"PK\x03\x04":
        # A sign-in HTML page here usually means the PAT is wrong or expired.
        raise RuntimeError("Azure DevOps did not return a zip (check PAT scope Code: Read and repo names)")
    return data
