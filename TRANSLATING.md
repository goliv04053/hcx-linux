# Translating HCX

HCX's interface is in English, with translations in `hcx/locale/`:

| Language | File | Status |
|---|---|---|
| Português (Brasil) | `pt_BR.po` | complete |
| Español | `es.po` | complete |
| 日本語 | `ja.po` | partial (menus from the original HCX) |

Anyone can add a language or improve one. The files are standard gettext
`.po` files, so you can use any translation editor (Poedit, Gtranslator,
Lokalize, Weblate…) or a plain text editor. HCX reads the `.po` files
directly: there is nothing to compile.

## Add a new language

1. Create the file for your language. Use its code: `fr`, `de`, `it`, or a
   regional one like `pt_PT` or `zh_CN`:

   ```bash
   python3 tools/i18n.py new fr
   ```

2. Open `hcx/locale/fr.po` and fill in every `msgstr ""` under its
   `msgid`. Keep placeholders such as `%s` and `%d`, and the `…` at the end
   of menu items that open a dialog.

   ```po
   msgid "Open Stack…"
   msgstr "Ouvrir une pile…"
   ```

3. Try it without changing your system language:

   ```bash
   HCX_LANG=fr python3 -m hcx
   ```

   You can also pick it in the app's menu, under **Language**. The menu lists
   every `.po` file it finds.

4. If your language is missing from `LANGUAGE_NAMES` in `hcx/i18n.py`, add
   its name written in that language, so the menu shows "Français" instead
   of "fr".

5. Open a pull request with your `.po` file.

## Update a translation after the code changes

When new interface strings are added to the code, run:

```bash
python3 tools/i18n.py update
```

This rebuilds `hcx/locale/hcx.pot` (the template) and adds the new strings,
untranslated, to every `.po` file. Strings that are no longer used are kept
at the end of the file as comments (`#~`). `python3 tools/i18n.py stats`
shows how complete each language is.

Untranslated or `#, fuzzy` entries simply show in English.

## For developers

Wrap every interface string in `_()` (from `hcx.i18n`). For a string that
must be stored untranslated and translated later, such as a table of
labels, mark it with `N_()` and call `_()` where it is shown. Then run
`python3 tools/i18n.py update`.

The HyperTalk language itself stays in English, like on the Mac: commands,
messages, menu names that scripts use (`doMenu "Next"`), and the values of
`the result`. Only the HCX interface is translated.

---

## Em português

Para traduzir o HCX para outro idioma, rode `python3 tools/i18n.py new xx`
(onde `xx` é o código do idioma), preencha os `msgstr` do arquivo
`hcx/locale/xx.po` com qualquer editor de traduções (Poedit, por exemplo) e
teste com `HCX_LANG=xx python3 -m hcx`. Depois é só mandar um pull request.
