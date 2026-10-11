"""Translations.

Strings are marked with ``_("…")`` and ``ngettext("…", "…", n)``. Translations live in
``urpm/locale/<language>.po``: the standard gettext format that Poedit, Weblate and
every translation tool understand. They're read directly, so there's no build step and
nothing to compile. No GTK in this module.

To add a language, copy ``po/urpm.pot`` to ``urpm/locale/<code>.po`` and translate it
(see docs/TRANSLATING.md).
"""

from __future__ import annotations

import ast
import gettext
import io
import json
import os
import struct
import time
from pathlib import Path

LOCALE_DIR = Path(__file__).resolve().parent / "locale"
RTL_LANGUAGES = {"ar", "fa", "he", "ur", "yi", "ps", "ckb"}
# Each language's own name for itself, for the Settings picker.
NATIVE_NAMES = {"ar": "العربية", "de": "Deutsch", "en": "English", "es": "Español",
                "fr": "Français", "it": "Italiano", "pt_BR": "Português (Brasil)",
                "ru": "Русский", "tr": "Türkçe", "zh_CN": "简体中文"}

_current: gettext.NullTranslations = gettext.NullTranslations()
_language = "en"


def _(text: str) -> str:
    return _current.gettext(text)


def ngettext(singular: str, plural: str, n: int) -> str:
    return _current.ngettext(singular, plural, n)


def N_(text: str) -> str:
    """Mark a string for translation without translating it yet (for tables built at
    import time). Translate it with _() where it's shown."""
    return text


# Month and weekday names, so dates follow URPM's language rather than the system's.
MONTHS = (N_("Jan"), N_("Feb"), N_("Mar"), N_("Apr"), N_("May"), N_("Jun"), N_("Jul"),
          N_("Aug"), N_("Sep"), N_("Oct"), N_("Nov"), N_("Dec"))
WEEKDAYS = (N_("Monday"), N_("Tuesday"), N_("Wednesday"), N_("Thursday"), N_("Friday"),
            N_("Saturday"), N_("Sunday"))


def strftime(fmt: str, when) -> str:
    """time.strftime, with %b and %A (month, weekday) in URPM's language."""
    month = _(MONTHS[when.tm_mon - 1]).replace("%", "%%")
    weekday = _(WEEKDAYS[when.tm_wday]).replace("%", "%%")
    return time.strftime(fmt.replace("%b", month).replace("%A", weekday), when)


# ---------------------------------------------------------------- reading .po files

def parse_po(text: str) -> dict[tuple[str, ...], list[str]]:
    """{(msgid,) or (msgid, msgid_plural): [msgstr, …]} for every translated entry.

    Untranslated and fuzzy entries are skipped, so English is shown instead.
    """
    entries: dict[tuple[str, ...], list[str]] = {}
    current: dict[str, object] = {}
    last = None
    fuzzy = False

    def finish():
        nonlocal current, fuzzy
        msgid = current.get("msgid")
        strs = current.get("msgstr", {})
        if msgid is not None and not fuzzy and strs:
            key = (msgid,) if "msgid_plural" not in current else (msgid, current["msgid_plural"])
            values = [strs[i] for i in sorted(strs)]
            if all(values) or msgid == "":
                entries[key] = values
        current, fuzzy = {}, False

    for raw in text.splitlines():
        line = raw.strip()
        if not line:
            finish()
            last = None
            continue
        if line.startswith("#,") and "fuzzy" in line:
            if "msgid" in current:
                finish()
            fuzzy = True
            continue
        if line.startswith("#"):
            continue
        keyword, _sep, rest = line.partition(" ")
        if line.startswith('"'):
            value = ast.literal_eval(line)
            if last == "msgid" or last == "msgid_plural":
                current[last] = current[last] + value
            elif last is not None:
                strs = current["msgstr"]
                strs[last] += value
            continue
        if keyword in ("msgid",) and "msgid" in current and current.get("msgstr"):
            finish()
        if keyword in ("msgid", "msgid_plural"):
            current[keyword] = ast.literal_eval(rest)
            last = keyword
        elif keyword == "msgstr":
            current.setdefault("msgstr", {})[0] = ast.literal_eval(rest)
            last = 0
        elif keyword.startswith("msgstr["):
            index = int(keyword[7:-1])
            current.setdefault("msgstr", {})[index] = ast.literal_eval(rest)
            last = index
        elif keyword == "msgctxt":
            last = None  # contexts aren't used by URPM
    finish()
    return entries


def compile_mo(entries: dict[tuple[str, ...], list[str]]) -> bytes:
    """Build a GNU .mo file in memory, so the standard library can do lookups and plurals."""
    pairs = []
    for key, values in entries.items():
        msgid = "\x00".join(key).encode("utf-8")
        msgstr = "\x00".join(values).encode("utf-8")
        pairs.append((msgid, msgstr))
    pairs.sort()
    ids = b"".join(k + b"\x00" for k, _v in pairs)
    strs = b"".join(v + b"\x00" for _k, v in pairs)
    count = len(pairs)
    header_size = 7 * 4
    ids_start = header_size + count * 16
    strs_start = ids_start + len(ids)
    id_table, str_table = [], []
    offset = 0
    for k, _v in pairs:
        id_table += [len(k), ids_start + offset]
        offset += len(k) + 1
    offset = 0
    for _k, v in pairs:
        str_table += [len(v), strs_start + offset]
        offset += len(v) + 1
    header = struct.pack("<7I", 0x950412DE, 0, count, header_size, header_size + count * 8, 0, 0)
    return (header + struct.pack(f"<{len(id_table)}I", *id_table)
            + struct.pack(f"<{len(str_table)}I", *str_table) + ids + strs)


def load_po(path: Path) -> gettext.GNUTranslations:
    return gettext.GNUTranslations(io.BytesIO(compile_mo(parse_po(path.read_text("utf-8")))))


# ---------------------------------------------------------------- choosing a language

def available() -> list[str]:
    """Language codes URPM has translations for, plus English."""
    found = sorted(p.stem for p in LOCALE_DIR.glob("*.po"))
    return ["en", *found]


def system_languages() -> list[str]:
    """The user's preferred languages, from the usual environment variables."""
    for var in ("LANGUAGE", "LC_ALL", "LC_MESSAGES", "LANG"):
        value = os.environ.get(var, "")
        if value and value not in ("C", "POSIX") and not value.startswith(("C.", "POSIX.")):
            return [v for v in value.split(":") if v] if var == "LANGUAGE" else [value]
    return []


def match(wanted: list[str], have: list[str]) -> str:
    """Best available language: pt_BR.UTF-8 -> pt_BR, then pt, then any other pt_*."""
    for code in wanted:
        code = code.split(".")[0].split("@")[0]
        base = code.split("_")[0]
        same = [h for h in have if h.split("_")[0] == base]
        for candidate in (code, base, *same):
            if candidate in have:
                return candidate
    return "en"


def saved_language() -> str:
    """The language picked in Settings ("" means the system's)."""
    base = os.environ.get("XDG_CONFIG_HOME") or os.path.expanduser("~/.config")
    try:
        value = json.loads((Path(base) / "urpm" / "settings.json").read_text()).get("language")
    except (OSError, ValueError, AttributeError):
        return ""
    return value if isinstance(value, str) else ""


def setup(language: str | None = None) -> str:
    """Switch to `language` ("" or None: the system's). Returns the code in use."""
    global _current, _language
    have = available()
    wanted = [language] if language else system_languages()
    code = match(wanted, have)
    path = LOCALE_DIR / f"{code}.po"
    if code != "en" and path.exists():
        try:
            _current, _language = load_po(path), code
            return code
        except (OSError, ValueError, SyntaxError):
            pass  # a broken translation file must never stop URPM from starting
    _current, _language = gettext.NullTranslations(), "en"
    return "en"


def current() -> str:
    return _language


def is_rtl(code: str | None = None) -> bool:
    return (code or _language).split("_")[0] in RTL_LANGUAGES
