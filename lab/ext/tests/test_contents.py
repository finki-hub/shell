import json

import pytest
from tornado import web
from tornado.httpclient import HTTPClientError

from finki_lab.contents import (
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
    # Given an X-Upload-Size header value
    set_declared_upload_size(raw)

    # When / Then
    assert declared_upload_size() == expected


def test_available_bytes_reads_the_project_quota(manager, monkeypatch):
    # Given 400 free blocks of 4096 bytes
    monkeypatch.setattr("os.statvfs", fake_statvfs())

    # When / Then
    assert manager.available_bytes() == 400 * 4096


def test_first_chunk_is_refused_when_the_declared_size_exceeds_the_quota(
    manager, monkeypatch, tmp_path
):
    # Given 1 KiB of quota left and a client declaring a 4 KiB upload
    monkeypatch.setattr(manager, "available_bytes", lambda: 1024)
    set_declared_upload_size("4096")

    # When the first chunk arrives
    with pytest.raises(web.HTTPError) as excinfo:
        manager.save(dict(CHUNK_MODEL), "upload.txt")

    # Then it is refused before anything is written
    assert excinfo.value.status_code == 507
    assert excinfo.value.log_message == "insufficient-storage"
    assert not (tmp_path / "upload.txt").exists()


def test_first_chunk_is_accepted_when_the_declared_size_fits(
    manager, monkeypatch, tmp_path
):
    # Given plenty of quota left
    monkeypatch.setattr(manager, "available_bytes", lambda: 1_000_000)
    set_declared_upload_size("4096")

    # When the first chunk arrives
    model = manager.save(dict(CHUNK_MODEL), "upload.txt")

    # Then it is written
    assert model["path"] == "upload.txt"
    assert (tmp_path / "upload.txt").read_text() == "hello"


def test_later_chunks_are_not_preflighted(manager, monkeypatch, tmp_path):
    # Given a quota that would refuse the declared size
    monkeypatch.setattr(manager, "available_bytes", lambda: 1)
    set_declared_upload_size("4096")
    monkeypatch.setattr(manager, "available_bytes", lambda: 1_000_000)
    manager.save(dict(CHUNK_MODEL), "upload.txt")
    monkeypatch.setattr(manager, "available_bytes", lambda: 1)

    # When a continuation chunk arrives
    manager.save({**CHUNK_MODEL, "chunk": -1, "content": " world"}, "upload.txt")

    # Then only chunk 1 was gated
    assert (tmp_path / "upload.txt").read_text() == "hello world"


def test_an_unchunked_save_uses_the_model_size(manager, monkeypatch, tmp_path):
    # Given no header but a model declaring its size
    monkeypatch.setattr(manager, "available_bytes", lambda: 10)

    # When an unchunked save arrives
    with pytest.raises(web.HTTPError) as excinfo:
        manager.save(
            {"type": "file", "format": "text", "content": "hello", "size": 4096},
            "plain.txt",
        )

    # Then the preflight still runs
    assert excinfo.value.status_code == 507
    assert not (tmp_path / "plain.txt").exists()


def test_saving_over_a_directory_is_refused(manager, tmp_path):
    # Given an existing directory
    (tmp_path / "notes").mkdir()

    # When a file is saved onto it
    with pytest.raises(web.HTTPError) as excinfo:
        manager.save(dict(CHUNK_MODEL), "notes")

    # Then the destination is refused and the directory survives
    assert excinfo.value.status_code == 409
    assert excinfo.value.log_message == "directory-destination"
    assert (tmp_path / "notes").is_dir()


def test_creating_a_directory_over_a_directory_is_still_allowed(manager, tmp_path):
    # Given an existing directory
    (tmp_path / "notes").mkdir()

    # When mkdir is repeated
    model = manager.save({"type": "directory"}, "notes")

    # Then it stays idempotent
    assert model["type"] == "directory"


def test_saving_through_a_symlink_is_refused(manager, tmp_path):
    # Given a symlink pointing at a file outside the upload path
    target = tmp_path / "secret.txt"
    target.write_text("original")
    (tmp_path / "link.txt").symlink_to(target)

    # When a save targets the link
    with pytest.raises(web.HTTPError) as excinfo:
        manager.save(dict(CHUNK_MODEL), "link.txt")

    # Then the link is never followed
    assert excinfo.value.status_code == 409
    assert excinfo.value.log_message == "symlink-destination"
    assert target.read_text() == "original"


def test_saving_through_a_symlinked_directory_entry_is_refused(manager, tmp_path):
    # Given a symlink to a directory
    (tmp_path / "outside").mkdir()
    (tmp_path / "shortcut").symlink_to(tmp_path / "outside")

    # When a save targets a path under the link's own name
    with pytest.raises(web.HTTPError) as excinfo:
        manager.save(dict(CHUNK_MODEL), "shortcut")

    # Then it is refused
    assert excinfo.value.status_code == 409


@pytest.fixture
def jp_server_config(jp_server_config):
    jp_server_config.ServerApp.contents_manager_class = (
        "finki_lab.contents.QuotaAwareFileManager"
    )
    return jp_server_config


async def test_upload_size_header_reaches_the_manager_over_http(jp_fetch):
    # Given a client declaring an upload larger than any real quota
    body = json.dumps({"type": "file", "format": "text", "content": "hi", "chunk": 1})

    # When the first chunk is PUT
    with pytest.raises(HTTPClientError) as excinfo:
        await jp_fetch(
            "api",
            "contents",
            "upload.txt",
            method="PUT",
            body=body,
            headers={"X-Upload-Size": str(10**18)},
        )

    # Then the override handler passed the header through and the manager refused
    assert excinfo.value.code == 507


async def test_a_declared_size_that_fits_is_written(jp_fetch, jp_root_dir):
    # Given a small declared upload
    body = json.dumps({"type": "file", "format": "text", "content": "hi", "chunk": 1})

    # When the first chunk is PUT
    response = await jp_fetch(
        "api",
        "contents",
        "upload.txt",
        method="PUT",
        body=body,
        headers={"X-Upload-Size": "2"},
    )

    # Then it lands in the root directory
    assert response.code == 201
    assert (jp_root_dir / "upload.txt").read_text() == "hi"
