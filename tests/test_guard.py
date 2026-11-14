from __future__ import annotations

import _winapi
import os

import pytest

from conftest import make_old
from systemautobot.safety.guard import GuardRefusal


def test_deletes_old_files_inside_temp(sandbox):
    guard, temp, docs = sandbox
    f = temp / "sub" / "junk.tmp"
    f.parent.mkdir()
    f.write_bytes(b"x" * 1000)
    make_old(f)
    rep = guard.delete_contents(temp)
    assert not f.exists()
    assert rep.count == 1 and rep.bytes_freed == 1000
    assert rep.files[0].action == "deleted"
    assert temp.exists(), "the temp root itself is never deleted"


def test_skips_recent_files(sandbox):
    guard, temp, _ = sandbox
    f = temp / "fresh.tmp"
    f.write_text("installer in progress")
    rep = guard.delete_contents(temp)
    assert f.exists() and rep.skipped_recent == 1


def test_skips_files_in_use(sandbox):
    guard, temp, _ = sandbox
    f = temp / "locked.tmp"
    f.write_text("open")
    make_old(f)
    with open(f, "rb"):  # Python opens without FILE_SHARE_DELETE, like a busy app
        rep = guard.delete_contents(temp)
    assert f.exists() and rep.skipped_in_use == 1


@pytest.mark.parametrize("target", ["docs_file", "docs_dir", "outside", "root"])
def test_refuses_outside_allowed_or_protected(sandbox, tmp_path, target):
    guard, temp, docs = sandbox
    (docs / "thesis.docx").write_text("precious")
    (tmp_path / "other.txt").write_text("x")
    path = {"docs_file": docs / "thesis.docx", "docs_dir": docs,
            "outside": tmp_path / "other.txt", "root": temp}[target]
    with pytest.raises(GuardRefusal):
        guard.check_delete(path)


def test_refuses_dotdot_escape(sandbox):
    guard, temp, docs = sandbox
    (docs / "thesis.docx").write_text("precious")
    with pytest.raises(GuardRefusal):
        guard.check_delete(temp / ".." / "Documents" / "thesis.docx")


def test_junction_into_documents_is_not_followed(sandbox):
    guard, temp, docs = sandbox
    secret = docs / "thesis.docx"
    secret.write_text("precious")
    make_old(secret)
    _winapi.CreateJunction(str(docs), str(temp / "sneaky"))
    rep = guard.delete_contents(temp)
    assert secret.exists()
    assert rep.skipped_links == 1
    with pytest.raises(GuardRefusal):
        guard.check_delete(temp / "sneaky" / "thesis.docx")


def test_refuses_drive_root(sandbox):
    guard, _, _ = sandbox
    with pytest.raises(GuardRefusal):
        guard.check_delete("C:\\")


def test_refuses_cleaning_a_folder_that_is_not_allowed(sandbox):
    guard, _, docs = sandbox
    with pytest.raises(GuardRefusal):
        guard.delete_contents(docs)


def test_process_guard_refuses_system_and_self(sandbox):
    import psutil

    guard, _, _ = sandbox
    with pytest.raises(GuardRefusal):
        guard.check_close(os.getpid())
    with pytest.raises(GuardRefusal):
        guard.check_close(4)
    lsass = next((p.pid for p in psutil.process_iter(["name"]) if (p.info["name"] or "").lower() == "lsass.exe"), None)
    if lsass:
        with pytest.raises(GuardRefusal):
            guard.check_close(lsass)


def test_default_guard_protects_real_documents():
    from systemautobot.safety.guard import _known_folder, default_guard

    guard = default_guard()
    docs = _known_folder("Documents")
    assert docs is not None
    with pytest.raises(GuardRefusal):
        guard.check_delete(docs / "anything.txt")
    with pytest.raises(GuardRefusal):
        guard.check_delete(os.environ["USERPROFILE"])
