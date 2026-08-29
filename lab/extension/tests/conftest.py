import pytest
from traitlets.config import Config

from shell_lab_extension.contents import QuotaAwareFileManager, set_declared_upload_size

pytest_plugins = ["pytest_jupyter.jupyter_server"]


@pytest.fixture
def jp_server_config():
    # Given a server configured the way the lab image's config file does it.
    return Config(
        {
            "ServerApp": {
                "jpserver_extensions": {
                    "jupyter_server_terminals": True,
                    "shell_lab_extension": True,
                },
            },
        }
    )


@pytest.fixture
def manager(tmp_path):
    # Given a contents manager rooted at a throwaway home directory.
    return QuotaAwareFileManager(root_dir=str(tmp_path), allow_hidden=True)


@pytest.fixture(autouse=True)
def _clear_declared_size():
    set_declared_upload_size(None)
