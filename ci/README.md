# ci

Shared continuous-integration scripts and reusable workflow logic for
`trustlist-core`. The GitHub Actions workflow itself lives in
`.github/workflows/ci.yml`; this directory holds the supporting scripts it
calls.

## Contents

- `check_license_headers.py` — verifies that every source file carries the
  Apache 2.0 licence header. Invoked by the `licence-header` CI job. Checks
  Python (`.py`) and TypeScript (`.ts`) sources.
- `license-header.txt` — the canonical licence-header text (without
  language-specific comment markers). The expected header to prepend to new
  source files; `check_license_headers.py` matches against a marker substring
  drawn from it.

## Licence header

Every `.py` and `.ts` source file must begin with the Apache 2.0 header. For
Python, prefix each line of `license-header.txt` with `# `; for TypeScript,
prefix each line with `// `. The check looks for the marker line
`Licensed under the Apache License, Version 2.0` within the opening bytes of
each file — it is comment-marker agnostic, so the same substring search covers
both languages. Compiled output (`dist/`), coverage reports and the Alembic
`versions/` migration tree are skipped.
