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
    local no_changes_message="${USL_CLI_NO_CHANGES_MESSAGE:-No changes were made.}"
    local line
    shift 2
    printf '\nBlocked\n' >&2
    printf '  %s\n' "$reason" >&2
    printf '\nWhy\n' >&2
    while IFS= read -r line; do
        printf '  %s\n' "$line" >&2
    done <<<"$why"
    printf '\n%s\n' "$no_changes_message" >&2
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

# Docker Compose reads the repository .env automatically, but host-side helper
# scripts do not. Read only the four numeric UI ports so command summaries and
# final URLs describe the same runtime Compose will start. Explicit shell
# variables keep precedence over local files.
usl_cli_load_local_port_defaults() {
    local repository_root="$1"
    shift
    local file key value
    local files=(
        "$repository_root/.env"
        "$repository_root/.pocket-id.env"
        "$@"
    )
    local keys=(
        ODOO_HTTP_PORT
        ODOO_GEVENT_PORT
        POCKET_ID_HTTP_PORT
        PAPERLESS_HTTP_PORT
    )

    for file in "${files[@]}"; do
        [[ -n "$file" ]] || continue
        [[ -f "$file" ]] || continue
        for key in "${keys[@]}"; do
            [[ -z "${!key:-}" ]] || continue
            value="$(
                awk -F= -v key="$key" '
                    $1 == key {
                        sub(/^[^=]*=/, "")
                        result = $0
                    }
                    END {print result}
                ' "$file"
            )"
            [[ "$value" =~ ^[0-9]+$ ]] || continue
            ((value >= 1 && value <= 65535)) || continue
            printf -v "$key" '%s' "$value"
            export "$key"
        done
    done
}
