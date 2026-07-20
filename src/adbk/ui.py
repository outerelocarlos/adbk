"""Small console helpers built on ``rich`` with a plain-text fallback.

We use ``rich`` for nice output when a capable terminal is present, but every
function here must also work in a basic terminal, when output is piped, or when
running non-interactively. Prompts use the built-in :func:`input`, never a heavy
TUI framework.
"""

from __future__ import annotations

import re

from rich.console import Console
from rich.text import Text

from adbk import platform_support as ps

# Generous synonyms so the user isn't limited to a bare "y"/"n".
_YES = {"y", "yes", "yeah", "yep", "yup", "ya", "ok", "okay", "sure", "true", "t", "1", "aye"}
_NO = {"n", "no", "nope", "nah", "never", "false", "f", "0"}


def make_console(*, plain: bool = False) -> Console:
    """Create a Console that degrades gracefully.

    When stdout is not a real terminal (piped, redirected, CI) rich is told not
    to emit colour or control codes, so logs stay clean and readable.
    """

    force_plain = plain or not ps.stdout_is_tty()
    return Console(
        no_color=force_plain,
        highlight=False,
        emoji=False,
        soft_wrap=False,
        # Never interpret "[x]" in a filename or checkbox as style markup.
        markup=False,
    )


_COMMAND = re.compile(r"'[^']+'")
_STEP = re.compile(r"^(\s*)(\d+\.)\s+(.*)$")


def _highlight_commands(text: str) -> Text:
    """A hint line with any 'quoted command' picked out, so it is easy to spot."""

    line = Text()
    position = 0
    for match in _COMMAND.finditer(text):
        line.append(text[position : match.start()])
        line.append(match.group(0), style="bold cyan")
        position = match.end()
    line.append(text[position:])
    return line


def print_error(console: Console, message: str, hint: str = "") -> None:
    """Print an error: a highlighted header, then optional indented guidance.

    Hint lines keep the layout the caller wrote, with three touches of style so
    the block scans quickly: a line ending in ``:`` is a heading, a leading
    ``N.`` is a numbered step, and 'quoted commands' are highlighted.
    """

    header = Text()
    header.append("Error: ", style="bold red")
    header.append(message, style="bold")
    console.print(header)
    if not hint:
        return

    console.print()
    for row in hint.strip("\n").splitlines():
        if not row.strip():
            console.print()
            continue
        if row.rstrip().endswith(":"):
            console.print(Text("  " + row, style="bold"))
            continue
        step = _STEP.match(row)
        if step is not None:
            indent, number, rest = step.groups()
            line = Text("  " + indent)
            line.append(number, style="bold cyan")
            line.append(" ")
            line.append_text(_highlight_commands(rest))
            console.print(line)
            continue
        line = Text("  ")
        line.append_text(_highlight_commands(row))
        console.print(line)


def confirm(
    console: Console,
    question: str,
    *,
    assume_yes: bool = False,
    interactive: bool = True,
    default: bool = False,
) -> bool:
    """Ask a yes/no question.

    * ``assume_yes`` (``--yes``) returns ``True`` without prompting.
    * When not interactive we cannot prompt, so we return ``default``.
    * Otherwise we prompt with :func:`input` until we get a clear answer.
    """

    if assume_yes:
        return True
    if not interactive:
        return default

    suffix = " [Y/n] " if default else " [y/N] "
    while True:
        try:
            answer = input(question + suffix).strip().lower()
        except EOFError:
            return default
        if not answer:
            return default
        if answer in _YES:
            return True
        if answer in _NO:
            return False
        console.print("Please answer yes or no.")
