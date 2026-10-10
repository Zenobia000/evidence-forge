# Third-party skill provenance

These skills are vendored into this private repository so the same development setup is available on every workstation.

## Emil Kowalski skills

- Source: <https://github.com/emilkowalski/skills>
- Revision: `d23d7f88a2e21c9e4b1418c7abe420f5c1052ba7`
- License: [`_licenses/EMIL-KOWALSKI-MIT.txt`](_licenses/EMIL-KOWALSKI-MIT.txt)
- Imported: 2026-09-03
- Directories: `animate-expo`, `animate`, `animation-vocabulary`, `apple-design`, `ask-sonner`, `emil-design-eng`, `find-animation-opportunities`, `improve-animations`, `pick-ui-library`, `review-animations`, `write-swift`, `emil-prototype`

Local modification: the upstream `prototype` directory was imported as `emil-prototype`, and its `SKILL.md` frontmatter name was changed from `prototype` to `emil-prototype`. This preserves the project's pre-existing, unrelated `prototype` skill without a name collision. No other Emil skill files were modified.

## Impeccable

- Source: <https://github.com/pbakaus/impeccable>
- Revision: `5a7e2837d2036b2ea8386031c2cd9a539b0dab13`
- License: [`_licenses/IMPECCABLE-APACHE-2.0.txt`](_licenses/IMPECCABLE-APACHE-2.0.txt)
- Imported: 2026-09-03
- Directory: `impeccable`
- Local modifications: none

## Archify

- Source: <https://github.com/tt-a1i/archify>
- Revision: `06dd052602dd9a369e4d034e24faef0917b5a60c` (v2.17.0-dev.1, 2026-09-02)
- License: [`_licenses/ARCHIFY-MIT.txt`](_licenses/ARCHIFY-MIT.txt); third-party marks in `archify/THIRD_PARTY_NOTICES.md`
- Imported: 2026-09-04
- Directory: `archify` (upstream `archify/` sub-directory, the skill payload)
- Local modifications: `test/` (upstream's own suite, 130 files) and `package-lock.json` removed — nothing else touched; `node bin/archify.mjs doctor` still reports all 13 checks ok on Node 24
- Note: `scripts/check-update.mjs` performs a network call to check for upstream releases; the skill text says to run it once after the first candidate. It never installs anything.
- Used by: `dev-ticket-dag` upstream (renders the workflow IR it produces); that skill is not shipped in this template
