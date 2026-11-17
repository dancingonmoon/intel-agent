#!/usr/bin/env bash
# Remove generated PDF/HTML and PDF preview assets, keeping Markdown reports.
# Usage: ./scripts/clean_reports.sh [--yes] [reports_root]
set -euo pipefail

YES=0
if [[ "${1:-}" == "--yes" ]]; then
  YES=1
  shift
fi
ROOT="${1:-$HOME/reports/intel}"

if [[ ! -d "$ROOT" ]]; then
  echo "reports directory not found: $ROOT" >&2
  exit 1
fi

mapfile -t FILES < <(find "$ROOT" -type f \( -name '*.pdf' -o -name '*.html' \) -print | sort)
if ((${#FILES[@]} == 0)); then
  echo "nothing to clean in $ROOT"
  exit 0
fi

printf '%s\n' "${FILES[@]}"
if ((YES == 0)); then
  echo "dry run; rerun with --yes to delete the files above and assets/ directories"
  exit 0
fi
printf '%s\0' "${FILES[@]}" | xargs -0 rm -f --
find "$ROOT" -type d -name assets -prune -exec rm -rf {} +
echo "cleaned ${#FILES[@]} files and assets/ directories"
