import json

import pytest
from tornado.httpclient import HTTPClientError

from finki_lab import _drop_upload_leftovers

NOTEBOOK = {
    "cells": [],
    "metadata": {},
    "nbformat": 4,
    "nbformat_minor": 5,
}


@pytest.fixture
def jp_server_config(jp_server_config):
    # Given the lab image's contents manager, so the /api/contents override that
    # shadows these routes is genuinely in place.
    jp_server_config.ServerApp.contents_manager_class = (
        "finki_lab.contents.QuotaAwareFileManager"
    )
    return jp_server_config


async def test_the_checkpoint_list_is_not_swallowed_by_the_contents_override(
    jp_fetch, jp_root_dir
):
    # Given a file in the home directory
    (jp_root_dir / "f.txt").write_text("hello")

    # When its checkpoints are listed
    response = await jp_fetch("api", "contents", "f.txt", "checkpoints")

    # Then jupyter_server's CheckpointsHandler answered -- the greedy path_regex
    # of the contents override would otherwise have matched "/f.txt/checkpoints"
    assert response.code == 200
    assert json.loads(response.body) == []


async def test_a_checkpoint_can_be_created_and_restored(jp_fetch, jp_root_dir):
    # Given a file whose first version is checkpointed
    (jp_root_dir / "f.txt").write_text("first")
    created = await jp_fetch(
        "api", "contents", "f.txt", "checkpoints", method="POST", body=""
    )
    assert created.code == 201
    checkpoint_id = json.loads(created.body)["id"]

    # When the file is changed and the checkpoint restored
    (jp_root_dir / "f.txt").write_text("second")
    restored = await jp_fetch(
        "api",
        "contents",
        "f.txt",
        "checkpoints",
        checkpoint_id,
        method="POST",
        body="",
    )

    # Then ModifyCheckpointsHandler answered, not ContentsHandler.post
    assert restored.code == 204
    assert (jp_root_dir / "f.txt").read_text() == "first"


async def test_a_checkpoint_can_be_deleted(jp_fetch, jp_root_dir):
    (jp_root_dir / "f.txt").write_text("first")
    created = await jp_fetch(
        "api", "contents", "f.txt", "checkpoints", method="POST", body=""
    )
    checkpoint_id = json.loads(created.body)["id"]

    deleted = await jp_fetch(
        "api", "contents", "f.txt", "checkpoints", checkpoint_id, method="DELETE"
    )

    assert deleted.code == 204
    listed = await jp_fetch("api", "contents", "f.txt", "checkpoints")
    assert json.loads(listed.body) == []


async def test_the_trust_route_is_not_swallowed_by_the_contents_override(
    jp_fetch, jp_root_dir
):
    # Given a notebook in the home directory
    (jp_root_dir / "n.ipynb").write_text(json.dumps(NOTEBOOK))

    # When it is trusted
    response = await jp_fetch(
        "api", "contents", "n.ipynb", "trust", method="POST", body=""
    )

    # Then TrustNotebooksHandler answered with its 201
    assert response.code == 201


async def test_the_contents_override_still_owns_the_plain_path(jp_fetch, jp_root_dir):
    # Given a client declaring an upload larger than any real quota
    body = json.dumps({"type": "file", "format": "text", "content": "hi", "chunk": 1})

    # When the first chunk is PUT to a plain contents path
    with pytest.raises(HTTPClientError) as excinfo:
        await jp_fetch(
            "api",
            "contents",
            "upload.txt",
            method="PUT",
            body=body,
            headers={"X-Upload-Size": str(10**18)},
        )

    # Then the quota-aware override, not jupyter_server's ContentsHandler, ran
    assert excinfo.value.code == 507


def test_upload_leftovers_are_dropped_at_extension_load(tmp_path):
    # Given a home directory holding the debris of an interrupted upload
    (tmp_path / ".big.iso.part").write_text("half")
    (tmp_path / "keep.txt").write_text("mine")
    (tmp_path / ".hidden").write_text("mine")
    (tmp_path / "notes.part").write_text("mine")
    sub = tmp_path / "sub"
    sub.mkdir()
    (sub / ".nested.part").write_text("mine")

    # When the extension loads
    _drop_upload_leftovers(str(tmp_path))

    # Then only `.<name>.part` files directly under the root are gone
    assert not (tmp_path / ".big.iso.part").exists()
    assert (tmp_path / "keep.txt").exists()
    assert (tmp_path / ".hidden").exists()
    assert (tmp_path / "notes.part").exists()
    assert (sub / ".nested.part").exists()


def test_an_unreadable_root_directory_is_not_a_startup_failure(tmp_path):
    _drop_upload_leftovers(str(tmp_path / "absent"))
