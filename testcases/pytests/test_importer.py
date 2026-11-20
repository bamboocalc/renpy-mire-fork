"""Tests for renpy.importer: resources of modules loaded from the game directory."""

import pytest

import renpy
from renpy.importer import RenpyPath, RenpyResourceReader


def test_iterdir_of_a_file_raises():
    """iterdir returned the error from inside a generator, so iterating a file silently gave nothing."""

    with pytest.raises(NotADirectoryError):
        list(RenpyPath("script.rpy", True).iterdir())


@pytest.mark.parametrize("path", ["$game", "$game/"])
def test_files_of_the_game_directory(mocker, path):
    """A module at the top of the game directory got a reader for a path that didn't exist."""

    mocker.patch.object(renpy.loader, "tree", {"data.txt": True, "lib": {"other.txt": True}})

    assert sorted(i.name for i in RenpyResourceReader(path).files().iterdir()) == ["data.txt", "lib"]
