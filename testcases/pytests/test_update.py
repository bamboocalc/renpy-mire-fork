"""Tests for renpy.update: deferred deletes and ranged downloads."""

import io

import pytest

from renpy.update import deferred, download, update


@pytest.fixture
def deleted(mocker, tmp_path):
    """The deleted directory, with unlinking made to fail as it does for a file open on Windows."""

    directory = tmp_path / "deleted"
    mocker.patch.object(deferred, "DELETED_DIRECTORY", str(directory), create=True)
    mocker.patch.object(deferred, "first_deferred_delete", False)
    mocker.patch.object(deferred.os, "unlink", side_effect=PermissionError)
    return directory


def test_files_with_one_name_are_both_moved_aside(deleted, tmp_path):
    """Files were moved to deleted/ by basename, so a second file with the same name couldn't be."""

    for d in ("a", "b"):
        (tmp_path / d).mkdir()
        (tmp_path / d / "script.rpyc").write_text(d)
        deferred.delete(str(tmp_path / d / "script.rpyc"))

    assert sorted(i.read_text() for i in deleted.glob("*/script.rpyc")) == ["a", "b"]


def test_a_delete_that_fails_is_deferred(mocker, deleted, tmp_path):
    """deferred.delete swallowed its errors, so Update.delete never recorded a deferred delete."""

    mocker.patch.object(deferred.os, "rename", side_effect=PermissionError)
    defer_delete = mocker.patch.object(deferred, "defer_delete")
    path = tmp_path / "locked.rpa"
    path.write_text("x")

    with pytest.raises(PermissionError):
        deferred.delete(str(path))

    update.Update.delete(None, str(path))
    defer_delete.assert_called_once_with(str(path))


def test_multipart_ranges_follow_the_rfc():
    """The first delimiter needed a leading line break, and quoted boundaries and lowercase headers weren't read."""

    content = (
        b"--sep\r\n"
        b"content-type: application/octet-stream\r\n"
        b"content-range: bytes 0-2/10\r\n\r\n"
        b"abc\r\n"
        b"--sep\r\n"
        b"Content-Range: bytes 7-9/10\r\n\r\n"
        b"hij\r\n"
        b"--sep--\r\n"
    )
    f = io.BytesIO()

    ranges = download.write_multipart(f, {"Content-Type": 'multipart/byteranges; boundary="sep"'}, content)

    assert ranges == [(0, 2), (7, 9)]
    assert f.getvalue() == b"abc\0\0\0\0hij"


def test_a_whole_file_response_is_not_downloaded_again(mocker, tmp_path):
    """When the server ignored the range and sent the whole file, it was downloaded a second time."""

    response = mocker.Mock(status_code=200, headers={})
    response.raw.read.side_effect = [b"whole file", b""]
    get = mocker.patch.object(download.requests, "get", return_value=response)

    destination = tmp_path / "block.rpu"
    download.download("https://example.invalid/block.rpu", [(0, 5)], str(destination))

    assert get.call_count == 1
    assert destination.read_bytes() == b"whole file"
