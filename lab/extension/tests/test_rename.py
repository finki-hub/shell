"""The `.part` commit rename, which must replace an existing file."""

import pytest
from tornado import web


def test_rename_replaces_an_existing_file(manager, tmp_path):
    # Given a completed .part upload and an older file of the same name
    (tmp_path / ".notes.txt.part").write_text("new")
    (tmp_path / "notes.txt").write_text("old")

    # When the upload is committed
    manager.rename_file(".notes.txt.part", "notes.txt")

    # Then the rename replaced the file atomically
    assert (tmp_path / "notes.txt").read_text() == "new"
    assert not (tmp_path / ".notes.txt.part").exists()


def test_rename_onto_a_directory_is_refused(manager, tmp_path):
    # Given a directory in the way
    (tmp_path / "part").write_text("new")
    (tmp_path / "notes").mkdir()

    # When the rename is attempted
    with pytest.raises(web.HTTPError) as excinfo:
        manager.rename_file("part", "notes")

    # Then it is refused and the directory survives
    assert excinfo.value.status_code == 409
    assert (tmp_path / "notes").is_dir()


def test_rename_onto_a_symlink_is_refused(manager, tmp_path):
    # Given a symlink in the way
    target = tmp_path / "secret.txt"
    target.write_text("original")
    (tmp_path / "notes.txt").symlink_to(target)
    (tmp_path / "part").write_text("new")

    # When the rename is attempted
    with pytest.raises(web.HTTPError) as excinfo:
        manager.rename_file("part", "notes.txt")

    # Then the link is never followed
    assert excinfo.value.status_code == 409
    assert target.read_text() == "original"


def test_rename_to_a_free_name_still_works(manager, tmp_path):
    # Given nothing at the destination
    (tmp_path / "part").write_text("new")

    # When the rename is attempted
    manager.rename_file("part", "notes.txt")

    # Then the base manager moved it
    assert (tmp_path / "notes.txt").read_text() == "new"


def test_rename_of_a_missing_source_is_a_404(manager, tmp_path):
    # Given only the destination exists
    (tmp_path / "notes.txt").write_text("old")

    # When a rename names a source that is gone
    with pytest.raises(web.HTTPError) as excinfo:
        manager.rename_file("part", "notes.txt")

    # Then the client is told so
    assert excinfo.value.status_code == 404
