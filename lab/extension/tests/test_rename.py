import pytest
from tornado import web


def test_rename_replaces_an_existing_file(manager, tmp_path):
    (tmp_path / ".notes.txt.part").write_text("new")
    (tmp_path / "notes.txt").write_text("old")

    manager.rename_file(".notes.txt.part", "notes.txt")

    assert (tmp_path / "notes.txt").read_text() == "new"
    assert not (tmp_path / ".notes.txt.part").exists()


def test_rename_onto_a_directory_is_refused(manager, tmp_path):
    (tmp_path / "part").write_text("new")
    (tmp_path / "notes").mkdir()

    with pytest.raises(web.HTTPError) as excinfo:
        manager.rename_file("part", "notes")

    assert excinfo.value.status_code == 409
    assert (tmp_path / "notes").is_dir()


def test_rename_onto_a_symlink_is_refused(manager, tmp_path):
    target = tmp_path / "secret.txt"
    target.write_text("original")
    (tmp_path / "notes.txt").symlink_to(target)
    (tmp_path / "part").write_text("new")

    with pytest.raises(web.HTTPError) as excinfo:
        manager.rename_file("part", "notes.txt")

    assert excinfo.value.status_code == 409
    assert target.read_text() == "original"


def test_rename_to_a_free_name_still_works(manager, tmp_path):
    (tmp_path / "part").write_text("new")

    manager.rename_file("part", "notes.txt")

    assert (tmp_path / "notes.txt").read_text() == "new"


def test_rename_of_a_missing_source_is_a_404(manager, tmp_path):
    (tmp_path / "notes.txt").write_text("old")

    with pytest.raises(web.HTTPError) as excinfo:
        manager.rename_file("part", "notes.txt")

    assert excinfo.value.status_code == 404
