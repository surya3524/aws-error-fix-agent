import base64
import io
import os
import tarfile
import zipfile

import pytest

import repo_fetch


def make_tar(entries):
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tf:
        for name, data, kind in entries:
            info = tarfile.TarInfo(name)
            if kind == "file":
                info.size = len(data)
                tf.addfile(info, io.BytesIO(data))
            elif kind == "symlink":
                info.type = tarfile.SYMTYPE
                info.linkname = data
                tf.addfile(info)
    return buf.getvalue()


def make_zip(entries):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for name, data in entries:
            zf.writestr(name, data)
    return buf.getvalue()


def test_tar_extract_and_unwrap_single_top_dir(tmp_path):
    data = make_tar([("proj/src/a.py", b"print(1)", "file")])
    root = repo_fetch.extract(data, str(tmp_path / "r"))
    assert root.endswith("proj")
    assert open(os.path.join(root, "src", "a.py")).read() == "print(1)"


def test_zip_extract(tmp_path):
    data = make_zip([("a.py", "x"), ("pkg/b.py", "y")])
    root = repo_fetch.extract(data, str(tmp_path / "r"))
    assert os.path.isfile(os.path.join(root, "pkg", "b.py"))


@pytest.mark.parametrize("bad", ["../evil.py", "/etc/evil", "a/../../evil"])
def test_traversal_rejected_tar(tmp_path, bad):
    with pytest.raises(repo_fetch.ArchiveError):
        repo_fetch.extract(make_tar([(bad, b"x", "file")]), str(tmp_path / "r"))


def test_traversal_rejected_zip(tmp_path):
    with pytest.raises(repo_fetch.ArchiveError):
        repo_fetch.extract(make_zip([("../evil.py", "x")]), str(tmp_path / "r"))


def test_symlinks_skipped(tmp_path):
    data = make_tar([("a.py", b"x", "file"), ("link", "/etc/passwd", "symlink")])
    root = repo_fetch.extract(data, str(tmp_path / "r"))
    assert not os.path.lexists(os.path.join(root, "link"))


def test_extraction_limit(tmp_path, monkeypatch):
    monkeypatch.setattr(repo_fetch, "MAX_EXTRACTED_BYTES", 10)
    with pytest.raises(repo_fetch.ArchiveError):
        repo_fetch.extract(make_tar([("a", b"x" * 20, "file")]), str(tmp_path / "r"))


def test_garbage_rejected(tmp_path):
    with pytest.raises(repo_fetch.ArchiveError):
        repo_fetch.extract(b"not an archive", str(tmp_path / "r"))


def test_load_archive_inline_and_scheme_check():
    assert repo_fetch.load_archive({"archive_b64": base64.b64encode(b"abc").decode()}) == b"abc"
    with pytest.raises(repo_fetch.ArchiveError):
        repo_fetch.load_archive({"archive_url": "http://example.com/x.zip"})
    with pytest.raises(repo_fetch.ArchiveError):
        repo_fetch.load_archive({"archive_url": "file:///etc/passwd"})
    with pytest.raises(repo_fetch.ArchiveError):
        repo_fetch.load_archive({})
