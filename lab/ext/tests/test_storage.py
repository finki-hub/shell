import json
from pathlib import Path

import pytest
from tornado.httpclient import HTTPClientError

from finki_lab.storage import DEFAULT_HOME, home_directory, storage_report
from tests.support import fake_statvfs

REPORT_KEYS = {"bytesUsed", "bytesLimit", "inodesUsed", "inodesLimit"}


def test_storage_report_derives_quota_numbers_from_statvfs(monkeypatch):
    # Given a 1000-block quota with 400 blocks and 500 inodes still free
    monkeypatch.setattr("os.statvfs", fake_statvfs())

    # When the report is built
    report = storage_report(Path("/home/ubuntu"))

    # Then used/limit are reported in bytes and inodes
    assert report == {
        "bytesUsed": 600 * 4096,
        "bytesLimit": 1000 * 4096,
        "inodesUsed": 500,
        "inodesLimit": 2000,
    }


@pytest.mark.parametrize(
    ("environ", "expected"),
    [
        ({}, DEFAULT_HOME),
        ({"HOME": "/home/other"}, "/home/other"),
        ({"HOME": ""}, DEFAULT_HOME),
    ],
)
def test_home_directory_falls_back_to_the_contract_default(
    monkeypatch, environ, expected
):
    # Given HOME set, unset or blank
    monkeypatch.delenv("HOME", raising=False)
    for key, value in environ.items():
        monkeypatch.setenv(key, value)

    # When / Then
    assert home_directory() == Path(expected)


async def test_storage_route_answers_with_the_report_shape(jp_fetch):
    # Given the extension loaded into a running server
    # When the SPA polls the badge without touching the activity stamp
    response = await jp_fetch("lab", "storage", params={"no_track_activity": "1"})

    # Then the four contract keys come back as integers
    assert response.code == 200
    payload = json.loads(response.body)
    assert set(payload) == REPORT_KEYS
    assert all(isinstance(value, int) for value in payload.values())


async def test_storage_route_refuses_an_unauthenticated_request(jp_fetch):
    # Given no Authorization header
    # When the route is called
    with pytest.raises(HTTPClientError) as excinfo:
        await jp_fetch("lab", "storage", headers={"Authorization": ""})

    # Then the API handler answers 403 rather than redirecting to a login page
    assert excinfo.value.code == 403
