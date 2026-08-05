#!/usr/bin/env bash

# Small, dependency-free output helpers for human-facing repository commands.
# Keep output stable in CI and readable in terminals without relying on color.

usl_cli_title() {
    printf '\nUSL Odoo Distribution — %s\n' "$1"
}

usl_cli_context() {
    local checkout="$1"
    local branch="$2"
    local project="$3"
    local database="$4"
    local ports="$5"
    printf '  Checkout: %s (%s)\n' "$checkout" "$branch"
    printf '  Project:  %s\n' "$project"
    printf '  Database: %s\n' "$database"
    printf '  Ports:    %s\n' "$ports"
}

usl_cli_step() {
    printf '\n[%s] %s\n' "$1" "$2"
}

usl_cli_success() {
    printf '\nDone — %s\n' "$1"
}

usl_cli_blocked() {
    local reason="$1"
    local why="$2"
    local line
    shift 2
    printf '\nBlocked\n' >&2
    printf '  %s\n' "$reason" >&2
    printf '\nWhy\n' >&2
    while IFS= read -r line; do
        printf '  %s\n' "$line" >&2
    done <<<"$why"
    printf '\nNo changes were made.\n' >&2
    if (($#)); then
        printf '\nNext steps\n' >&2
        while (($#)); do
            while IFS= read -r line; do
                printf '  %s\n' "$line" >&2
            done <<<"$1"
            shift
        done
    fi
}
