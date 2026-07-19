"""Tests for the console confirm() yes/no parsing."""

from __future__ import annotations

import pytest

from adbk import ui


@pytest.mark.parametrize("answer", ["y", "yes", "Yeah", "yep", "YUP", "ok", "okay", "sure", "true", "1", "aye"])
def test_confirm_accepts_many_yeses(monkeypatch: pytest.MonkeyPatch, answer: str) -> None:
    monkeypatch.setattr("builtins.input", lambda _prompt: answer)
    assert ui.confirm(ui.make_console(plain=True), "Q?", interactive=True) is True


@pytest.mark.parametrize("answer", ["n", "no", "Nope", "nah", "never", "false", "0"])
def test_confirm_accepts_many_noes(monkeypatch: pytest.MonkeyPatch, answer: str) -> None:
    monkeypatch.setattr("builtins.input", lambda _prompt: answer)
    assert ui.confirm(ui.make_console(plain=True), "Q?", interactive=True, default=True) is False


def test_confirm_empty_uses_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("builtins.input", lambda _prompt: "")
    console = ui.make_console(plain=True)
    assert ui.confirm(console, "Q?", interactive=True, default=True) is True
    assert ui.confirm(console, "Q?", interactive=True, default=False) is False


def test_confirm_assume_yes_skips_prompt() -> None:
    # No input() patched: assume_yes must not call it.
    assert ui.confirm(ui.make_console(plain=True), "Q?", assume_yes=True) is True


def test_confirm_non_interactive_returns_default() -> None:
    assert ui.confirm(ui.make_console(plain=True), "Q?", interactive=False, default=False) is False
