#!/usr/bin/env bash

usl_refuse_protected_transition() {
  local root="$1" project="$2" operation="$3"
  case "$project" in
    usl-odoo-transition-?*)
      python3 "$root/scripts/transition_live.py" --root "$root" guard \
        --project "$project" --operation "$operation" >/dev/null
      ;;
  esac
}
