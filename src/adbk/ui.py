"""Small console helpers built on ``rich`` with a plain-text fallback.

We use ``rich`` for nice output when a capable terminal is present, but every
function here must also work in a basic terminal, when output is piped, or when
running non-interactively. Prompts use the built-in :func:`input`, never a heavy
TUI framework.
"""

from __future__ import annotations

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


def print_error(console: Console, message: str, hint: str = "") -> None:
    """Print an error: a highlighted header, then optional indented guidance.

    The hint is printed verbatim (minus surrounding blank lines) so a caller can
    lay out numbered steps, and is indented to sit under the message.
    """

    header = Text()
    header.append("Error: ", style="bold red")
    header.append(message)
    console.print(header)
    if hint:
        console.print()
        for row in hint.strip("\n").splitlines():
            console.print(Text("  " + row) if row.strip() else Text())


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
