"""Tests for copying and building styles."""

import renpy


def test_copy_has_its_own_property_dicts():
    """Deleting a property from a copy deleted it from the original, since the two shared their dicts."""

    original = renpy.style.Style(None, {"xpos": 1})
    copy = original.copy()

    del copy.xpos

    assert original.properties == [{"xpos": 1}]
    assert copy.properties == [{}]


def test_button_in_current_store_copies_its_style(mocker):
    """Button._in_current_store assigned the copy's name from Python, which Style rejects as an unknown property."""

    mocker.patch.dict(renpy.style.styles, {("default",): renpy.style.Style(None, {})})
    mocker.patch.object(renpy.display.displayable, "default_style", renpy.style.Style("default"))

    button = renpy.display.behavior.Button(renpy.display.layout.Null(), style=("menu_button", "big"))
    copy = button._in_current_store()

    assert copy.style is not button.style
    assert copy.style.parent == ("menu_button", "big")
    assert copy.locked
