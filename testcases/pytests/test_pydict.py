"""Tests for the dictionary snapshots used to find store changes for rollback."""

import pytest

from renpy.pydict import DictItems, find_changes


def test_find_changes_reports_changed_added_and_removed_keys():
    deleted = object()

    changes = find_changes(
        DictItems({"same": 1, "changed": 2, "removed": 3}), DictItems({"same": 1, "changed": 4, "added": 5}), deleted
    )

    assert changes == {"changed": 2, "removed": 3, "added": deleted}


@pytest.mark.parametrize("which", ["old", "new"])
def test_find_changes_rejects_none(which):
    """A None snapshot was read through a null pointer."""

    items = {"old": DictItems({}), "new": DictItems({})}
    items[which] = None

    with pytest.raises(TypeError):
        find_changes(items["old"], items["new"], object())
