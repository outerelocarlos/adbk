"""Tests for the pure adaptive tree rendering and selection walking."""

from __future__ import annotations

from adbk import tree
from adbk.models import AccessState, EntryType, TreeNode


def _dir(name: str, path: str, children: list[TreeNode] | None = None) -> TreeNode:
    return TreeNode(name, path, EntryType.DIRECTORY, children=children or [])


def _file(name: str, path: str, size: int = 10) -> TreeNode:
    return TreeNode(name, path, EntryType.FILE, size=size)


def _lines(node: TreeNode) -> list[str]:
    return [line.plain for line in tree.render_lines(node)]


def _text(node: TreeNode) -> str:
    return "\n".join(_lines(node))


def test_three_file_rule_lists_up_to_three() -> None:
    node = _dir("d", "/d", [_file(f"f{i}", f"/d/f{i}") for i in range(3)])
    lines = _lines(node)
    assert any("f0" in line for line in lines)
    assert any("f2" in line for line in lines)
    assert not any("files," in line for line in lines)  # no summary


def test_four_files_summarised() -> None:
    node = _dir("d", "/d", [_file(f"f{i}", f"/d/f{i}", size=100) for i in range(4)])
    lines = _lines(node)
    assert not any("f0" in line for line in lines)  # individual names hidden
    assert any("4 files" in line for line in lines)


def test_four_files_with_subdirs_still_shows_subdirs() -> None:
    node = _dir(
        "d", "/d",
        [_file(f"f{i}", f"/d/f{i}") for i in range(4)] + [_dir("sub", "/d/sub", [_file("g", "/d/sub/g")])],
    )
    lines = _lines(node)
    assert any("4 files" in line and "1 subdirs" in line for line in lines)
    assert any("sub" in line for line in lines)


def test_single_child_chain_collapses() -> None:
    leaf = _dir("Media", "/a/b/c/Media", [_file("x", "/a/b/c/Media/x")])
    chain = _dir("a", "/a", [_dir("b", "/a/b", [_dir("c", "/a/b/c", [leaf])])])
    label, effective = tree.collapse_chain(chain)
    assert label == "a > b > c > Media"
    assert effective is leaf


def test_chain_stops_at_branch() -> None:
    branch = _dir("b", "/a/b", [
        _dir("c1", "/a/b/c1", [_file("f1", "/a/b/c1/f1")]),
        _dir("c2", "/a/b/c2", [_file("f2", "/a/b/c2/f2")]),
    ])
    node = _dir("a", "/a", [branch])
    label, effective = tree.collapse_chain(node)
    assert label == "a > b"
    assert effective is branch


def test_chain_stops_at_non_readable_child() -> None:
    inaccessible = _dir("secret", "/a/secret")
    inaccessible.access_state = AccessState.INACCESSIBLE
    node = _dir("a", "/a", [inaccessible])
    label, _effective = tree.collapse_chain(node)
    assert label == "a"  # did not collapse into the inaccessible child


def test_iter_included_files_respects_exclusion() -> None:
    excluded = _file("skip", "/d/skip")
    node = _dir("d", "/d", [_file("keep", "/d/keep"), excluded])
    tree.set_included(excluded, False)
    names = [f.name for f in tree.iter_included_files(node)]
    assert names == ["keep"]


def test_empty_and_inaccessible_annotated() -> None:
    empty = _dir("e", "/e")
    empty.access_state = AccessState.EMPTY
    assert "(empty)" in _lines(empty)[0]

    blocked = _dir("b", "/b")
    blocked.access_state = AccessState.INACCESSIBLE
    assert "(inaccessible)" in _lines(blocked)[0]


def test_empty_subfolders_are_hidden() -> None:
    empty1 = _dir("empty1", "/root/empty1")
    empty1.access_state = AccessState.EMPTY
    nested = _dir("nested", "/root/empty2/nested")
    nested.access_state = AccessState.EMPTY
    empty2 = _dir("empty2", "/root/empty2", [nested])  # only empty children
    node = _dir("root", "/root", [_file("a.txt", "/root/a.txt"), empty1, empty2])
    text = _text(node)
    assert "a.txt" in text
    assert "empty1" not in text
    assert "empty2" not in text


def test_junk_folders_and_files_are_hidden() -> None:
    node = _dir("root", "/root", [
        _file("a.txt", "/root/a.txt"),
        _file(".nomedia", "/root/.nomedia"),
        _dir(".thumbnails", "/root/.thumbnails", [_file("t.jpg", "/root/.thumbnails/t.jpg")]),
        _dir(".trash", "/root/.trash", [_file("g", "/root/.trash/g")]),
    ])
    text = _text(node)
    assert "a.txt" in text
    assert ".nomedia" not in text
    assert ".thumbnails" not in text
    assert ".trash" not in text


def test_inaccessible_and_unexpanded_folders_are_kept() -> None:
    blocked = _dir("secret", "/root/secret")
    blocked.access_state = AccessState.INACCESSIBLE
    unexpanded = _dir("apps", "/root/apps")
    unexpanded.warning = "253 subfolders (not expanded)"
    node = _dir("root", "/root", [blocked, unexpanded])
    text = _text(node)
    assert "secret" in text  # inaccessible: informative, kept
    assert "apps" in text  # not walked yet, may hold data, kept


def test_human_size() -> None:
    assert tree.human_size(0) == "0 B"
    assert tree.human_size(1536).endswith("KiB")
