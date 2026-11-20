# Copyright 2004-2026 Tom Rothamel <pytom@bishoujo.us>
#
# Permission is hereby granted, free of charge, to any person
# obtaining a copy of this software and associated documentation files
# (the "Software"), to deal in the Software without restriction,
# including without limitation the rights to use, copy, modify, merge,
# publish, distribute, sublicense, and/or sell copies of the Software,
# and to permit persons to whom the Software is furnished to do so,
# subject to the following conditions:
#
# The above copyright notice and this permission notice shall be
# included in all copies or substantial portions of the Software.
#
# THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND,
# EXPRESS OR IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF
# MERCHANTABILITY, FITNESS FOR A PARTICULAR PURPOSE AND
# NONINFRINGEMENT. IN NO EVENT SHALL THE AUTHORS OR COPYRIGHT HOLDERS
# BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER LIABILITY, WHETHER IN AN
# ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM, OUT OF OR IN
# CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
# SOFTWARE.

"""
Tests for renpy.translation: generation of translation identifiers from translatable statements, and the
translation of strings.
"""

import json
from types import SimpleNamespace

import pytest

import renpy
from renpy import ast, translation

################################################################################
# Restructurer translation ids
################################################################################


class TranslatableStatement(ast.Node):
    """
    A minimal non-say translatable node used to verify which statement code contributes to translation IDs.
    """

    translatable = True
    code: str = ""

    def __init__(self, code: str) -> None:
        super().__init__(("game/translation.rpy", 2))
        self.code = code

    def get_code(self) -> str:
        return self.code


@pytest.fixture
def translation_environment(mocker):
    """
    Provides an empty translator so generated translation IDs are not changed by existing identifiers.
    """

    mocker.patch.object(
        renpy.game,
        "script",
        SimpleNamespace(translator=SimpleNamespace(default_translates={})),
    )


def create_translation_id(non_say_code: str) -> str:
    """
    Creates an identifier for a Say node followed by a non-say translatable node.
    """

    say = ast.Say(("game/translation.rpy", 1), "e", "Hello.", None)
    say.name = ("translation",)

    statements = [say, TranslatableStatement(non_say_code)]
    return translation.Restructurer([]).create_translate(statements)[0].identifier


def test_tlid_only_considers_say_excludes_non_say_code(mocker, translation_environment):
    """
    Checks that the default configuration keeps non-say statements from changing identifiers for dialogue.
    """

    mocker.patch.object(renpy.config, "tlid_only_considers_say", True)

    first = create_translation_id("voice first.ogg")
    second = create_translation_id("voice second.ogg")

    assert first == second


def test_tlid_includes_non_say_code_when_configured(mocker, translation_environment):
    """
    Checks that disabling the say-only compatibility behavior includes non-say statements in identifiers.
    """

    mocker.patch.object(renpy.config, "tlid_only_considers_say", False)

    first = create_translation_id("voice first.ogg")
    second = create_translation_id("voice second.ogg")

    assert first != second


GOLDEN_SCRIPT = """\
label start:
    e "Hello."
    e "Hello."
    e happy "Goodbye, \\"friend\\"." (what_italic=True)
    "Narration."
    e "Hello." id explicit_one

label other.sub:
    e "Hello."
"""
"A small script that exercises label prefixes, duplicate lines, explicit ids, and quoting."


def test_translation_ids_are_stable(mocker, translation_environment):
    """
    The identifiers generated for a fixed script are pinned to the values below.

    A translation identifier is the translate statement's name in saves and in translators' tl/ files, so a
    change in how it is computed silently orphans every existing translation. If this test fails because
    the algorithm was changed on purpose, the change needs a compatibility switch like
    config.tlid_only_considers_say, rather than an update to these values.

    The ids come from parsing real script text. A line's id is a hash of its canonical say statement,
    including the speaker, attributes, and arguments, prefixed with the enclosing label. A repeated line gets
    a numeric suffix, a dotted label has its dot replaced, and an explicit id wins, with the generated id
    kept as the alternate.
    """

    mocker.patch.object(renpy.config, "tlid_only_considers_say", True)
    mocker.patch.object(renpy.parser, "parsed_expressions", [])

    stmts = renpy.parser.parse("game/translation.rpy", GOLDEN_SCRIPT)

    for number, node in enumerate(renpy.script.collapse_stmts(stmts)):
        # The restructurer reads names, which the script loader normally assigns. Labels already have theirs.
        if not isinstance(node, ast.Label):
            node.name = ("golden", number)

    translation.restructure(stmts)

    says = [node for node in renpy.script.collapse_stmts(stmts) if isinstance(node, ast.TranslateSay)]

    assert [(node.what, node.identifier, node.alternate) for node in says] == [
        ("Hello.", "start_4e73b00f", None),
        ("Hello.", "start_4e73b00f_1", None),
        ('Goodbye, "friend".', "start_5fc7bae6", None),
        ("Narration.", "start_822c98ab", None),
        ("Hello.", "explicit_one", "start_ffcfebec"),
        ("Hello.", "other_sub_4e73b00f", None),
    ]


################################################################################
# StringTranslator
################################################################################


@pytest.fixture
def translator(mocker):
    """Gives the test a fresh translator, with recording of unknown strings disabled."""

    mocker.patch.object(translation, "update_translations", False)
    return translation.StringTranslator()


@pytest.mark.parametrize(
    "old, entries, expected",
    [
        ("Item", {}, "Item"),
        ("Item", {"Item": "Objet"}, "Objet"),
        ("Item", {"Item": ""}, ""),
        ("Item", {"Other": "Autre"}, "Item"),
        ("Item {#gallery}", {"Item ": "Objet"}, "Objet"),
        ("Item {#gallery}", {"Item {#gallery}": "Exact", "Item ": "Fallback"}, "Exact"),
        ("Item {#gallery}", {}, "Item {#gallery}"),
        ("Item {#gallery}", {"Other": "Autre"}, "Item {#gallery}"),
        ("{#one}Item{#two}", {"Item": "Objet"}, "Objet"),
        ("Item {#unfinished", {"Item ": "Wrong"}, "Item {#unfinished"),
        ("Item {#two\nlines}", {"Item ": "Wrong"}, "Item {#two\nlines}"),
        ("", {}, ""),
        ("{color=#fff}Item{/color}", {}, "{color=#fff}Item{/color}"),
        ("Élément [item]", {}, "Élément [item]"),
    ],
)
def test_translate(translator, old, entries, expected):
    """Exact matches win, then matches with {#...} tags removed, and otherwise the original is returned."""

    translator.translations.update(entries)
    assert translator.translate(old) == expected


def test_unknown_tagged_string_is_recorded(mocker, translator):
    """In update mode, an unknown tagged string is recorded once, and its tagless translation is still used."""

    mocker.patch.object(translation, "update_translations", True)
    translator.translations["Item "] = "Objet"

    assert translator.translate("Item {#gallery}") == "Objet"
    assert translator.unknown == ["Item {#gallery}"]

    # The recorded identity translation is now an exact match.
    assert translator.translate("Item {#gallery}") == "Item {#gallery}"
    assert translator.unknown == ["Item {#gallery}"]


def test_unknown_plain_string_is_recorded(mocker, translator):
    """In update mode, an unknown plain string is recorded once."""

    mocker.patch.object(translation, "update_translations", True)

    assert translator.translate("Item") == "Item"
    assert translator.translate("Item") == "Item"
    assert translator.unknown == ["Item"]
    assert translator.translations == {"Item": "Item"}


def test_table_changes_are_seen(translator):
    """An earlier miss does not hide translations added, replaced or removed later."""

    assert translator.translate("Item") == "Item"

    translator.add("Item", "Objet", None)
    assert translator.translate("Item") == "Objet"

    translator.translations["Item"] = "Article"
    assert translator.translate("Item") == "Article"

    del translator.translations["Item"]
    assert translator.translate("Item") == "Item"


@pytest.mark.parametrize("old", [123, b"Item {#tag}"])
def test_non_string_raises(translator, old):
    """Translating something that is not a string raises TypeError."""

    with pytest.raises(TypeError):
        translator.translate(old)


################################################################################
# Runtime lookup and locale detection
################################################################################


def test_translation_written_for_the_alternate_identifier_is_found(mocker):
    """The alternate identifier was looked up in the language's place, so its translation was never found."""

    mocker.patch.object(renpy.game, "preferences", SimpleNamespace(language="french"))
    translator = translation.ScriptTranslator()
    translator.default_translates["start_1234abcd"] = SimpleNamespace(block=["default"])
    translator.language_translates["start_9876fedc", "french"] = SimpleNamespace(block=["french"])

    assert translator.lookup_translate("start_1234abcd", "start_9876fedc") == ("french", True)


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        ("en_US", ("en", "us")),
        ("zh-hans_CN", ("zh-hans", "cn")),
        ("C", ("C", "C")),
        ("c", ("C", "C")),
    ],
)
def test_detect_user_locale_splits_language_and_region(mocker, name, expected):
    """Script-tagged names, as macOS gives for Chinese, weren't split, and a name normalized to C raised."""

    for platform in ("windows", "android", "ios", "macintosh"):
        mocker.patch.object(renpy, platform, False)
    mocker.patch("locale.getdefaultlocale", return_value=(name, None))

    assert translation.detect_user_locale() == expected


################################################################################
# Translation tools
################################################################################


@pytest.mark.parametrize(
    ("source", "expected"),
    [
        ('_("a\\\nb")', "ab"),
        ('_("a" \\\n "b")', "ab"),
        ('_("""a\\\\\nb""")', "a\\\nb"),
    ],
    ids=["continued-string", "continued-call", "escaped-backslash"],
)
def test_scan_strings_reads_continued_lines_like_python(mocker, source, expected):
    """Removing every backslash-newline split an escaped backslash from its pair before a newline."""

    mocker.patch.object(
        renpy.game, "script", SimpleNamespace(translator=SimpleNamespace(additional_strings={"game/a.rpy": []}))
    )
    mocker.patch.object(renpy.lexer, "list_logical_lines", return_value=[("game/a.rpy", 1, source)])
    mocker.patch.object(renpy.config, "commondir", "renpy/common")
    mocker.patch.object(renpy.config, "gamedir", "game")

    assert [s.text for s in translation.scanstrings.scan_strings("game/a.rpy")] == [expected]


def test_extract_strings_merges_into_an_existing_file(mocker, tmp_path):
    """json.load lost its encoding argument in Python 3.9, so merging raised TypeError."""

    strings = SimpleNamespace(translations={"Start": "Commencer", "Same": "Same"})
    mocker.patch.object(renpy.game, "script", SimpleNamespace(translator=SimpleNamespace(strings={"french": strings})))
    destination = tmp_path / "strings.json"
    destination.write_text('{"Quit": "Quitter"}', encoding="utf-8")

    translation.extract.extract_strings_core("french", str(destination), merge=True)

    assert json.loads(destination.read_text(encoding="utf-8")) == {"Quit": "Quitter", "Start": "Commencer"}


def test_dialogue_from_a_ren_py_file_is_translated_into_an_rpy_file(mocker):
    """Dialogue translations were written to a _ren.py file, which Ren'Py reads as python."""

    mocker.patch.object(renpy.config, "commondir", "renpy/common")
    mocker.patch.object(renpy.config, "gamedir", "game")
    say = SimpleNamespace(get_code=lambda filter=None: 'e "Hello."')
    translate = SimpleNamespace(
        identifier="start_1", alternate=None, filename="game/script_ren.py", linenumber=3, block=[say]
    )
    translator = SimpleNamespace(file_translates={"game/script_ren.py": [("start", translate)]}, language_translates={})
    mocker.patch.object(renpy.game, "script", SimpleNamespace(translator=translator))
    open_tl_file = mocker.patch.object(translation.generation, "open_tl_file")

    translation.generation.write_translates("game/script_ren.py", "french", None)

    assert open_tl_file.call_args.args[0].replace("\\", "/") == "game/tl/french/script.rpy"


def test_merge_strings_replaces_multiline_strings_without_moving_later_edits(mocker, tmp_path):
    """Replacing a multiline new string shifted the lines after it, so later edits hit the wrong line."""

    tl = tmp_path / "strings.rpy"
    tl.write_text(
        'translate french strings:\n\n    old "A"\n    new """A\nline"""\n\n    old "B"\n    new "B"\n',
        encoding="utf-8",
    )
    filename = tl.as_posix()
    source = tmp_path / "strings.json"
    source.write_text(json.dumps({"A": "Alpha", "B": "Beta"}), encoding="utf-8")

    args = SimpleNamespace(language="french", source=str(source), reverse=False, replace=True)
    mocker.patch.object(renpy.arguments, "ArgumentParser").return_value.parse_args.return_value = args
    strings = SimpleNamespace(
        translations={"A": "A\nline", "B": "B"}, translation_loc={"A": (filename, 4), "B": (filename, 8)}
    )
    mocker.patch.object(renpy.game, "script", SimpleNamespace(translator=SimpleNamespace(strings={"french": strings})))
    mocker.patch.object(renpy.config, "clear_lines", True)
    mocker.patch.object(renpy.scriptedit, "files", set())
    mocker.patch.object(renpy.scriptedit, "lines", {})
    mocker.patch.object(renpy.loader, "add_auto")

    translation.merge.merge_strings()

    assert tl.read_text(encoding="utf-8").splitlines()[3:] == ['    new "Alpha"', "", '    old "B"', '    new "Beta"']


def test_plain_text_dialogue_export_filters_and_orders_menu_captions(mocker, tmp_path):
    """Menu captions were always written tab-delimited and unfiltered, after all of the file's dialogue."""

    mocker.patch.object(renpy.config, "commondir", "renpy/common")
    say = ast.Say(("game/script.rpy", 5), "e", "Hello.", None)
    translator = SimpleNamespace(
        file_translates={
            "game/script.rpy": [("start", SimpleNamespace(identifier="start_1", linenumber=5, block=[say]))]
        },
        strings={None: SimpleNamespace(translate=lambda s: s)},
    )
    mocker.patch.object(renpy.game, "script", SimpleNamespace(translator=translator))
    menu = SimpleNamespace(items=[("{b}Choose{/b}", "True", None)], filename="game/script.rpy", linenumber=2)
    output = tmp_path / "dialogue.txt"

    translation.dialogue.DialogueFile(
        "game/script.rpy", str(output), tdf=False, notags=True, escape=False, menu_statements=[menu]
    )

    assert output.read_text(encoding="utf-8").splitlines() == ["Choose", "Hello."]
