import json
from pathlib import Path

import pytest
from tornado.httpclient import HTTPClientError

from shell_lab_extension.storage import DEFAULT_HOME, home_directory, storage_report
from tests.support import fake_statvfs

REPORT_KEYS = {"bytesUsed", "bytesLimit", "inodesUsed", "inodesLimit"}


def test_storage_report_derives_quota_numbers_from_statvfs(monkeypatch):
    monkeypatch.setattr("os.statvfs", fake_statvfs())

    report = storage_report(Path("/home/ubuntu"))

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
    monkeypatch.delenv("HOME", raising=False)
    for key, value in environ.items():
        monkeypatch.setenv(key, value)

    assert home_directory() == Path(expected)


async def test_storage_route_answers_with_the_report_shape(jp_fetch):
    response = await jp_fetch("lab", "storage", params={"no_track_activity": "1"})

    assert response.code == 200
    payload = json.loads(response.body)
    assert set(payload) == REPORT_KEYS
    assert all(isinstance(value, int) for value in payload.values())


async def test_storage_route_refuses_an_unauthenticated_request(jp_fetch):
    with pytest.raises(HTTPClientError) as excinfo:
        await jp_fetch("lab", "storage", headers={"Authorization": ""})

    assert excinfo.value.code == 403
