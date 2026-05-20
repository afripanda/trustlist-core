# Copyright 2026 The TrustList Foundation
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Verify that source files carry the Apache 2.0 licence header.

Run from CI (`python ci/check_license_headers.py`) or locally. Exits 0 when
every checked source file carries the header — and also exits 0 when there are
no source files yet, since the CI wiring is intentionally green on an empty
skeleton. Exits 1, listing the offending files, when any header is missing.
"""

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent

MARKER = 'Licensed under the Apache License, Version 2.0'

CHECKED_SUFFIXES = ('.py',)

SKIP_DIRS = {
    '.git',
    '__pycache__',
    '.venv',
    'venv',
    '.mypy_cache',
    '.ruff_cache',
    '.pytest_cache',
    'node_modules',
    'versions',
}


def iter_source_files(root: Path) -> list[Path]:
    """Return every checked source file under `root`, skipping ignored trees."""
    files: list[Path] = []
    for path in sorted(root.rglob('*')):
        if path.is_dir():
            continue
        if any(part in SKIP_DIRS for part in path.parts):
            continue
        if path.suffix in CHECKED_SUFFIXES:
            files.append(path)
    return files


def has_header(path: Path) -> bool:
    """Return True when the file's opening bytes contain the licence marker."""
    text = path.read_text(encoding='utf-8', errors='replace')[:4096]
    return MARKER in text


def main() -> int:
    """Check every source file and report any missing licence headers."""
    checked = iter_source_files(REPO_ROOT)
    missing = [path for path in checked if not has_header(path)]
    if missing:
        print(f'Licence-header check FAILED — {len(missing)} file(s) missing the header:')
        for path in missing:
            print(f'  - {path.relative_to(REPO_ROOT)}')
        return 1
    print(f'Licence-header check passed — {len(checked)} source file(s) checked.')
    return 0


if __name__ == '__main__':
    sys.exit(main())
