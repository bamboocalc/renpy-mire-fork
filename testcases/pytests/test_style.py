"""Tests for copying and building styles."""

import renpy


def test_copy_has_its_own_property_dicts():
    """Deleting a property from a copy deleted it from the original, since the two shared their dicts."""

    original = renpy.style.Style(None, {"xpos": 1})
    copy = original.copy()

    del copy.xpos

    assert original.properties == [{"xpos": 1}]
    assert copy.properties == [{}]
