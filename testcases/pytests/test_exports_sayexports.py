"""Tests for renpy.exports.sayexports: reshowing the last line of dialogue."""

import renpy
from renpy.exports import sayexports


def test_reshow_say_repeats_the_last_say_without_interacting(mocker):
    """The say statement records interact, which reshow_say passed twice, raising TypeError."""

    say = mocker.patch.object(sayexports, "say")
    mocker.patch.multiple(
        renpy.store,
        _last_say_who=None,
        _last_say_what="Hello.",
        _last_say_args=("extra",),
        _last_say_kwargs={"interact": True, "color": "#f00"},
        create=True,
    )

    sayexports.reshow_say()
    say.assert_called_with(None, "Hello.", "extra", interact=False, color="#f00")

    sayexports.reshow_say(interact=True)
    say.assert_called_with(None, "Hello.", "extra", interact=True, color="#f00")
