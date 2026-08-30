import json

import pytest
from tornado import web
from tornado.httpclient import HTTPClientError

from shell_lab_extension.contents import (
    declared_upload_size,
    set_declared_upload_size,
)
from tests.support import fake_statvfs

CHUNK_MODEL = {"type": "file", "format": "text", "content": "hello", "chunk": 1}


@pytest.mark.parametrize(
    ("raw", "expected"),
    [("1024", 1024), ("0", 0), (None, None), ("", None), ("-5", None), ("big", None)],
)
def test_declared_upload_size_parses_the_header(raw, expected):
    set_declared_upload_size(raw)

    assert declared_upload_size() == expected


def test_available_bytes_reads_the_project_quota(manager, monkeypatch):
    monkeypatch.setattr("os.statvfs", fake_statvfs())

    assert manager.available_bytes() == 400 * 4096


def test_first_chunk_is_refused_when_the_declared_size_exceeds_the_quota(
    manager, monkeypatch, tmp_path
):
    monkeypatch.setattr(manager, "available_bytes", lambda: 1024)
    set_declared_upload_size("4096")

    with pytest.raises(web.HTTPError) as excinfo:
        manager.save(dict(CHUNK_MODEL), "upload.txt")

    assert excinfo.value.status_code == 507
    assert excinfo.value.log_message == "insufficient-storage"
    assert not (tmp_path / "upload.txt").exists()


def test_first_chunk_is_accepted_when_the_declared_size_fits(
    manager, monkeypatch, tmp_path
):
    monkeypatch.setattr(manager, "available_bytes", lambda: 1_000_000)
    set_declared_upload_size("4096")

    model = manager.save(dict(CHUNK_MODEL), "upload.txt")

    assert model["path"] == "upload.txt"
    assert (tmp_path / "upload.txt").read_text() == "hello"


def test_later_chunks_are_not_preflighted(manager, monkeypatch, tmp_path):
    monkeypatch.setattr(manager, "available_bytes", lambda: 1)
    set_declared_upload_size("4096")
    monkeypatch.setattr(manager, "available_bytes", lambda: 1_000_000)
    manager.save(dict(CHUNK_MODEL), "upload.txt")
    monkeypatch.setattr(manager, "available_bytes", lambda: 1)

    manager.save({**CHUNK_MODEL, "chunk": -1, "content": " world"}, "upload.txt")

    assert (tmp_path / "upload.txt").read_text() == "hello world"


def test_an_unchunked_save_uses_the_model_size(manager, monkeypatch, tmp_path):
    monkeypatch.setattr(manager, "available_bytes", lambda: 10)

    with pytest.raises(web.HTTPError) as excinfo:
        manager.save(
            {"type": "file", "format": "text", "content": "hello", "size": 4096},
            "plain.txt",
        )

    assert excinfo.value.status_code == 507
    assert not (tmp_path / "plain.txt").exists()


def test_saving_over_a_directory_is_refused(manager, tmp_path):
    (tmp_path / "notes").mkdir()

    with pytest.raises(web.HTTPError) as excinfo:
        manager.save(dict(CHUNK_MODEL), "notes")

    assert excinfo.value.status_code == 409
    assert excinfo.value.log_message == "directory-destination"
    assert (tmp_path / "notes").is_dir()


def test_creating_a_directory_over_a_directory_is_still_allowed(manager, tmp_path):
    (tmp_path / "notes").mkdir()

    model = manager.save({"type": "directory"}, "notes")

    assert model["type"] == "directory"


def test_saving_through_a_symlink_is_refused(manager, tmp_path):
    target = tmp_path / "secret.txt"
    target.write_text("original")
    (tmp_path / "link.txt").symlink_to(target)

    with pytest.raises(web.HTTPError) as excinfo:
        manager.save(dict(CHUNK_MODEL), "link.txt")

    assert excinfo.value.status_code == 409
    assert excinfo.value.log_message == "symlink-destination"
    assert target.read_text() == "original"


def test_saving_through_a_symlinked_directory_entry_is_refused(manager, tmp_path):
    (tmp_path / "outside").mkdir()
    (tmp_path / "shortcut").symlink_to(tmp_path / "outside")

    with pytest.raises(web.HTTPError) as excinfo:
        manager.save(dict(CHUNK_MODEL), "shortcut")

    assert excinfo.value.status_code == 409


@pytest.fixture
def jp_server_config(jp_server_config):
    jp_server_config.ServerApp.contents_manager_class = (
        "shell_lab_extension.contents.QuotaAwareFileManager"
    )
    return jp_server_config


async def test_upload_size_header_reaches_the_manager_over_http(jp_fetch):
    body = json.dumps({"type": "file", "format": "text", "content": "hi", "chunk": 1})

    with pytest.raises(HTTPClientError) as excinfo:
        await jp_fetch(
            "api",
            "contents",
            "upload.txt",
            method="PUT",
            body=body,
            headers={"X-Upload-Size": str(10**18)},
        )

    assert excinfo.value.code == 507


async def test_a_declared_size_that_fits_is_written(jp_fetch, jp_root_dir):
    body = json.dumps({"type": "file", "format": "text", "content": "hi", "chunk": 1})

    response = await jp_fetch(
        "api",
        "contents",
        "upload.txt",
        method="PUT",
        body=body,
        headers={"X-Upload-Size": "2"},
    )

    assert response.code == 201
    assert (jp_root_dir / "upload.txt").read_text() == "hi"
