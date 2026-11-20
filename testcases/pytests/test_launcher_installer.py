"""Tests for launcher/game/installer.py, the API that launcher extension manifests use."""

import hashlib
import importlib.util
import pathlib
import sys
from types import SimpleNamespace

import pytest

import renpy.ecsign

SOURCE = pathlib.Path(__file__).parents[2] / "launcher" / "game" / "installer.py"


@pytest.fixture
def installer(mocker, tmp_path):
    """The module, loaded with a stand-in for the launcher's store, and Ren'Py's base in a temporary directory."""

    store = SimpleNamespace(
        _=lambda s: s,
        config=SimpleNamespace(renpy_base=str(tmp_path)),
        interface=mocker.Mock(),
        project=None,
        Jump=None,
    )
    mocker.patch.dict(sys.modules, {"store": store})
    mocker.patch.object(sys, "dont_write_bytecode", True)

    spec = importlib.util.spec_from_file_location("test_launcher_installer_module", SOURCE)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    module.set_target(str(tmp_path))
    return module


def serve(mocker, installer, data):
    response = mocker.Mock(headers={})
    response.iter_content.return_value = [data]
    return mocker.patch.object(installer.requests, "get", return_value=response)


def test_download_checks_the_expected_hash(mocker, installer, tmp_path):
    """The hash was compared with the hash builtin, and the check raised whenever the installer wasn't quiet."""

    serve(mocker, installer, b"data")

    installer.download("https://example.invalid/file", "file", hash=hashlib.sha256(b"data").hexdigest())
    assert (tmp_path / "file").read_bytes() == b"data"
    installer.interface.error.assert_not_called()

    installer.download("https://example.invalid/file", "other", hash="0" * 64)
    installer.interface.error.assert_called_once()

    installer.quiet = True
    with pytest.raises(Exception, match="Hash check failed"):
        installer.download("https://example.invalid/file", "third", hash="0" * 64)


def test_remove_backs_up_into_a_directory_that_exists(installer, tmp_path):
    """set_target removes empty temporary directories, so the backups directory was gone when a file moved into it."""

    (tmp_path / "old.txt").write_text("old")

    installer.remove("old.txt")

    assert not (tmp_path / "old.txt").exists()
    assert len(list((tmp_path / "tmp" / "installer" / "backups").iterdir())) == 1


def test_manifest_functions_see_its_other_names(installer, tmp_path):
    """Separate globals and locals hid a manifest's top-level names from the functions it defined."""

    out = tmp_path / "out.txt"
    manifest = tmp_path / "manifest.py"
    manifest.write_text(f"VALUE = 'seen'\ndef write():\n    open({str(out)!r}, 'w').write(VALUE)\nwrite()\n")

    installer.local_manifest(str(manifest), renpy=True)

    assert out.read_text() == "seen"


def test_bad_manifest_signature_reports_an_error(mocker, installer, tmp_path):
    """VerifyingKey.verify raises on a bad signature, which escaped instead of reporting it."""

    def download(url, filename, **kwargs):
        (tmp_path / "tmp" / "installer" / filename.partition(":")[2]).write_bytes(b"x = 1\n")

    mocker.patch.object(installer, "download", side_effect=download)
    mocker.patch.object(installer._renpy.exports, "open_file", create=True)
    key = mocker.Mock()
    key.verify.side_effect = renpy.ecsign.BadSignatureError("bad")
    mocker.patch.object(renpy.ecsign.VerifyingKey, "from_pem", return_value=key)

    installer.manifest("https://example.invalid/manifest.py", renpy=True)

    installer.interface.error.assert_called_once_with("The manifest signature is not valid.")
