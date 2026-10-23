"""Install a NIOS temporary license through the console CLI `set temp_license`."""

from __future__ import annotations

import re

from dominion.src.appliance import Appliance

_MENU_PROMPT = r"Select license[^\n]*quit:"
_WARNING = "**Temporary License Warning**"
_MENU_ENTRY = re.compile(r"^\s*(\d+)\.\s+(.*?)\s*$", re.MULTILINE)
_YN = r"\(y or n\)[:?]?"
_STEP = _YN + r"|already exists|UI restarted|Infoblox\s*>"


def _parse_menu(text: str) -> dict[str, str]:
    """Menu number -> entry text. The warning block also has "1. 2. 3." lines."""
    menu = text.replace("\r", "").split(_WARNING)[0]
    return {n: t for n, t in _MENU_ENTRY.findall(menu)}


def install_temp_license(app: Appliance, match: str, timeout: float = 120) -> str:
    """Install the temp license whose `set temp_license` menu entry matches.

    Args:
        app: Lab appliance (console_connect is used).
        match: Case-insensitive regex searched in the menu entry text,
            e.g. "Cloud Platform". Must match exactly one entry.
        timeout: Seconds to wait for each CLI step.

    Returns:
        The selected menu entry text.

    Raises:
        RuntimeError: If zero or several entries match (the menu is listed),
            or the CLI never confirms the install.
    """
    with app.console_connect_session(timeout=timeout) as c:
        c.sendline("set temp_license")
        menu = _parse_menu(c.expect(_MENU_PROMPT, timeout=timeout))
        hits = {n: t for n, t in menu.items() if re.search(match, t, re.IGNORECASE)}
        if len(hits) != 1:
            c.sendline("q")
            c.expect(r"Infoblox\s*>", timeout=timeout)
            listing = "\n".join(f"{n}. {t}" for n, t in menu.items())
            raise RuntimeError(
                f"{len(hits)} temp license entries match {match!r}, need exactly 1."
                f" Menu:\n{listing}"
            )

        (number, entry), = hits.items()
        app._logger.info("Selecting temp license %s. %s", number, entry)
        c.sendline(number)

        seen = ""
        while True:
            out = c.expect(_STEP, timeout=timeout)
            seen += out
            if re.search(_YN + "$", out):
                c.sendline("y")
            elif re.search(r"already exists$", out):
                app._logger.info("Temp license %r already exists", entry)
                c.expect(r"Infoblox\s*>", timeout=timeout)
                return entry
            else:
                break

    if not re.search(r"Temporary license is installed|UI restarted", seen):
        raise RuntimeError(f"No install confirmation for {entry!r}. Output:\n{seen}")
    return entry
