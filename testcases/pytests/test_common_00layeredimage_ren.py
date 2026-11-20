"""Tests for renpy/common/00layeredimage_ren.py: auto groups."""

import importlib.util
import pathlib
import sys

import pytest

import renpy


@pytest.fixture
def layeredimage(mocker):
    """The module, loaded from its source since common scripts don't run during these tests."""

    source = pathlib.Path(renpy.__file__).parent / "common" / "00layeredimage_ren.py"
    spec = importlib.util.spec_from_file_location("test_00layeredimage_ren", source)

    # Keeps bytecode for this file out of renpy/common, where a distribution could pick it up.
    mocker.patch.object(sys, "dont_write_bytecode", True)

    # In a game, the engine module carries the exports, and the file registers its statement at load.
    mocker.patch.object(renpy, "register_statement", create=True)

    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_auto_group_skips_an_image_named_like_its_prefix(mocker, layeredimage):
    """An image whose name was exactly the auto prefix unpacked an empty split, raising ValueError."""

    mocker.patch.object(renpy, "list_images", return_value=["eileen_base_", "eileen_base_happy"], create=True)
    mocker.patch.object(renpy, "displayable", side_effect=lambda name: name, create=True)
    mocker.patch.object(layeredimage, "Attribute", side_effect=lambda group, attribute, image, **kwargs: attribute)

    group = layeredimage.RawAttributeGroup("eileen", "base")
    group.final_properties["auto"] = True

    assert group.execute() == ["happy"]
