#!/usr/bin/env python3
"""Collect every translatable string and keep the translation files in step.

    python3 po/update.py            # rewrite po/urpm.pot and update urpm/locale/*.po
    python3 po/update.py --check    # exit 1 if anything is out of date (used by the tests)

Strings are found by reading the code (Python's own parser), so no gettext tools are
needed. New strings are added to every language untranslated (English is shown until
someone translates them); strings that were removed from the code are dropped.
"""

from __future__ import annotations

import ast
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CODE = ROOT / "urpm"
POT = ROOT / "po" / "urpm.pot"
LOCALES = CODE / "locale"
MARKERS = {"_": 1, "N_": 1, "ngettext": 2}

sys.path.insert(0, str(ROOT))
from urpm import __version__  # noqa: E402
from urpm.i18n import parse_po  # noqa: E402

HEADER = """# Translations for URPM.
# This file is distributed under the same license as URPM (MIT).
#
msgid ""
msgstr ""
"Project-Id-Version: URPM {version}\\n"
"Report-Msgid-Bugs-To: https://github.com/MrNaji1/URPM/issues\\n"
"Language: {language}\\n"
"MIME-Version: 1.0\\n"
"Content-Type: text/plain; charset=UTF-8\\n"
"Content-Transfer-Encoding: 8bit\\n"
"Plural-Forms: {plural}\\n"
"""

PLURALS = {
    "": "nplurals=INTEGER; plural=EXPRESSION;",
    "ar": "nplurals=6; plural=(n==0 ? 0 : n==1 ? 1 : n==2 ? 2 : n%100>=3 && n%100<=10 ? 3 "
          ": n%100>=11 ? 4 : 5);",
    "de": "nplurals=2; plural=(n != 1);",
    "es": "nplurals=2; plural=(n != 1);",
    "fr": "nplurals=2; plural=(n > 1);",
    "pt_BR": "nplurals=2; plural=(n > 1);",
}


class Problem(Exception):
    pass


def extract() -> dict[tuple[str, ...], list[str]]:
    """{(msgid,) or (singular, plural): ["file:line", …]} in the order they appear."""
    found: dict[tuple[str, ...], list[str]] = {}
    problems = []
    for path in sorted(CODE.rglob("*.py")):
        rel = path.relative_to(ROOT).as_posix()
        tree = ast.parse(path.read_text("utf-8"), filename=rel)
        for node in ast.walk(tree):
            if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
                    and node.func.id in MARKERS):
                continue
            count = MARKERS[node.func.id]
            args = node.args[:count]
            if any(isinstance(a, ast.JoinedStr) for a in args):
                problems.append(f"{rel}:{node.lineno}: {node.func.id}() can't translate an "
                                "f-string: use a plain string and .format() for the values")
                continue
            if len(args) < count or not all(isinstance(a, ast.Constant) and
                                            isinstance(a.value, str) for a in args):
                continue  # _(variable): a string marked with N_() somewhere else
            key = tuple(a.value for a in args)
            found.setdefault(key, []).append(f"{rel}:{node.lineno}")
    if problems:
        raise Problem("\n".join(problems))
    return dict(sorted(found.items(), key=lambda kv: (kv[1][0].split(":")[0],
                                                       int(kv[1][0].split(":")[1]))))


def quote(text: str) -> str:
    """A .po string, split after each newline like gettext does."""
    def one(part: str) -> str:
        escaped = (part.replace("\\", "\\\\").replace('"', '\\"').replace("\t", "\\t")
                   .replace("\n", "\\n"))
        return f'"{escaped}"'
    if "\n" not in text.rstrip("\n"):
        return one(text)
    parts = text.splitlines(keepends=True)
    return '""\n' + "\n".join(one(p) for p in parts)


def render(entries, translations=None, language: str = "") -> str:
    translations = translations or {}
    out = [HEADER.rstrip("\n").format(version=__version__, language=language,
                         plural=PLURALS.get(language, PLURALS["de"] if language else PLURALS[""]))]
    nplurals = 2
    plural_header = PLURALS.get(language, "")
    if "nplurals=" in plural_header and "INTEGER" not in plural_header:
        nplurals = int(plural_header.split("nplurals=")[1].split(";")[0])
    for key, where in entries.items():
        out.append("")
        out.append("#: " + " ".join(dict.fromkeys(w.split(":")[0] for w in where)))
        if any("{" in k for k in key):
            out.append("#, python-brace-format")
        values = translations.get(key)
        out.append("msgid " + quote(key[0]))
        if len(key) == 1:
            out.append("msgstr " + quote(values[0] if values else ""))
        else:
            out.append("msgid_plural " + quote(key[1]))
            for i in range(nplurals):
                value = values[i] if values and i < len(values) else ""
                out.append(f"msgstr[{i}] " + quote(value))
    return "\n".join(out) + "\n"


def main(argv: list[str]) -> int:
    check = "--check" in argv
    try:
        entries = extract()
    except Problem as exc:
        print(exc, file=sys.stderr)
        return 1
    outputs = {POT: render(entries)}
    for po in sorted(LOCALES.glob("*.po")):
        translations = parse_po(po.read_text("utf-8"))
        outputs[po] = render(entries, translations, po.stem)
    stale = [path for path, text in outputs.items()
             if not path.exists() or path.read_text("utf-8") != text]
    if check:
        for path in stale:
            print(f"{path.relative_to(ROOT)} is out of date: run python3 po/update.py",
                  file=sys.stderr)
        return 1 if stale else 0
    for path in stale:
        path.write_text(outputs[path], "utf-8")
        print(f"updated {path.relative_to(ROOT)}")
    print(f"{len(entries)} strings")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
