# Translating URPM

URPM is in English, Arabic, German, Spanish, French and Brazilian Portuguese. Adding a
language, or fixing a word that sounds wrong, is very welcome and needs no programming.

## What you need

- A text editor, or [Poedit](https://poedit.net/) (free, and the easiest way).
- About 390 short strings. Most are a few words, like button labels.

## Start a new language

1. Copy `po/urpm.pot` to `urpm/locale/<code>.po`. The code is your language's code, with
   the country only when it matters: `it`, `nl`, `tr`, `zh_CN`, `pt_PT`.
2. Open it in Poedit, or in a text editor. Fill in the two header lines:
   - `"Language: it\n"`
   - `"Plural-Forms: …\n"` with your language's rule. Poedit fills this in for you; it's
     also listed [in the gettext manual](https://www.gnu.org/software/gettext/manual/html_node/Plural-forms.html).
3. Translate each `msgstr`. Anything you leave empty shows in English, so you can send a
   partial translation too. A language ships in a release once every string is done.
4. Add your language's own name to `NATIVE_NAMES` in `urpm/i18n.py` so it shows up nicely
   in Settings, for example `"it": "Italiano"`.
5. Check it, then open a pull request (or attach the file to an issue).

## Fix an existing translation

Open `urpm/locale/<code>.po`, search for the English text, and change its `msgstr`.

## Rules that keep URPM working

- **Keep `{names}` in curly braces exactly as they are.** `{name}`, `{n}`, `{size}`… are
  filled in by URPM. You can move them around, but don't translate or remove them.
- **Plurals** (`msgid_plural`) have one `msgstr[…]` line per form your language uses.
  A form may leave out `{n}` (like "one day" instead of "1 day").
- **Keep the emoji**, ideally at the start, as in English.
- **Don't translate** product and program names: URPM, APT, Flatpak, Snap, AppImage, DNF,
  pacman, pip, npm, Cargo, Homebrew, Nix, Flatseal, sudo, and text in `backticks`.
- **Dates:** `%b %d, %Y` and `%A, %b %d, %Y` are date formats. Reorder the `%` codes into
  your language's usual order, for example `%d %b %Y`. Month and day names are translated
  separately (`Jan`, `Feb`… and `Monday`…).
- **The command-line help** (the long text starting with `URPM {version}:`) is shown in a
  terminal. Translate the descriptions, but keep the command words (`list`, `search`…),
  the options (`--mine`…) and the line breaks.
- Keep the friendly, short tone. "Your whole home folder" beats "Full access to the
  user's home directory".

## Check your work

```bash
python3 -m unittest tests.test_reach   # every {placeholder} kept, and nothing left out
LANGUAGE=it python3 -m urpm            # see it in the window
LANGUAGE=it python3 -m urpm updates    # and on the command line
```

## For developers: new strings

Wrap text the user sees in `_("…")`, or `ngettext("one", "many", n)` for counts, and put
values in with `.format()` (never an f-string, which can't be translated). For text in a
table that's built when the program starts, mark it with `N_("…")` and call `_()` where
it's shown. Then run:

```bash
make pot
```

This updates `po/urpm.pot` and adds the new strings to every language (untranslated, so
they show in English until someone translates them). The tests fail if you forget.
