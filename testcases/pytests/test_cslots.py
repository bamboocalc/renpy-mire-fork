"""Tests for the slot objects that AST nodes are built on."""

from renpy.cslots import Object, Slot


class Node(Object):
    a = Slot()
    b = Slot()
    c = Slot()
    d = Slot()
    e = Slot()
    f = Slot()
    g = Slot()
    h = Slot()
    i = Slot()
    j = Slot()
    k = Slot()
    l = Slot()
    m = Slot()
    n = Slot()
    o = Slot()
    p = Slot()


def test_sizeof_counts_every_value():
    """Operator precedence masked the whole size to 7 bits, so large objects reported under 128 bytes."""

    node = Node()
    for name in "abcdefghijklmnop":
        setattr(node, name, name)

    assert node.__sizeof__() >= 16 * 8
