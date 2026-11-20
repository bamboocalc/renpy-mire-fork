"""Tests for renpy.webloader: freeing downloaded files once they expire."""

from renpy import webloader


def test_expired_files_are_freed_while_idle(mocker, tmp_path):
    """Expired files were only freed while a download was queued, and a missing one raised on every call."""

    present = tmp_path / "present.png"
    present.write_bytes(b"x")
    missing = tmp_path / "missing.png"

    mocker.patch.object(webloader, "queue", [])
    to_unlink = mocker.patch.object(webloader, "to_unlink", {str(present): 0, str(missing): 0})

    webloader.process_downloaded_resources()

    assert not present.exists()
    assert to_unlink == {}
