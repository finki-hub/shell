import os


def fake_statvfs(
    *,
    frsize=4096,
    blocks=1000,
    bfree=400,
    bavail=400,
    files=2000,
    ffree=1500,
):
    """Build a stand-in for `os.statvfs` reporting an XFS project quota."""

    def _statvfs(_path):
        return os.statvfs_result(
            (frsize, frsize, blocks, bfree, bavail, files, ffree, ffree, 0, 255)
        )

    return _statvfs
