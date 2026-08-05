#!/usr/bin/env bash

# A Compose project belongs to one checkout because its bind mounts and build
# context resolve from that checkout. Keep this guard dependency-free so every
# host-side helper can call it before mutating shared Docker state.

if ! declare -F usl_cli_blocked >/dev/null 2>&1; then
    # shellcheck source=cli-ui.sh
    source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/cli-ui.sh"
fi

usl_compose_scope_scan() {
    local project="$1"
    local repository_root="$2"
    local owner_count resources

    repository_root="$(cd "$repository_root" && pwd -P)"
    if ! resources="$(
        docker ps -a \
            --filter "label=com.docker.compose.project=$project" \
            --format '{{.ID}}|{{.Names}}|{{.State}}|{{.Label "com.docker.compose.project.working_dir"}}|{{.Label "com.docker.compose.service"}}|{{.Status}}|{{.Label "com.docker.compose.oneoff"}}' \
            2>/dev/null
    )"; then
        USL_COMPOSE_SCOPE_STATE="unavailable"
        USL_COMPOSE_SCOPE_RESOURCES=""
        USL_COMPOSE_SCOPE_OWNERS=""
        return 0
    fi
    USL_COMPOSE_SCOPE_RESOURCES="$resources"
    USL_COMPOSE_SCOPE_OWNERS="$(
        printf '%s\n' "$USL_COMPOSE_SCOPE_RESOURCES" \
            | awk -F'|' 'NF >= 4 && $4 != "" {print $4}' \
            | sort -u
    )"
    owner_count="$(
        printf '%s\n' "$USL_COMPOSE_SCOPE_OWNERS" \
            | awk 'NF {count += 1} END {print count + 0}'
    )"

    if [[ -z "$USL_COMPOSE_SCOPE_RESOURCES" ]]; then
        USL_COMPOSE_SCOPE_STATE="unused"
    elif ((owner_count > 1)); then
        USL_COMPOSE_SCOPE_STATE="mixed"
    elif [[ "$USL_COMPOSE_SCOPE_OWNERS" == "$repository_root" ]]; then
        USL_COMPOSE_SCOPE_STATE="owned"
    else
        USL_COMPOSE_SCOPE_STATE="foreign"
    fi
}

usl_compose_owner_branch() {
    local owner="$1"
    local branch
    if [[ ! -d "$owner" ]]; then
        printf '<checkout unavailable>'
        return
    fi
    branch="$(git -C "$owner" branch --show-current 2>/dev/null || true)"
    printf '%s' "${branch:-<detached>}"
}

usl_compose_owner_summary() {
    local owner
    while IFS= read -r owner; do
        [[ -z "$owner" ]] && continue
        printf '%s (%s)\n' "$owner" "$(usl_compose_owner_branch "$owner")"
    done <<<"${USL_COMPOSE_SCOPE_OWNERS:-}"
}

usl_print_compose_inventory() {
    local project="$1"
    local repository_root="$2"
    local owner branch count

    usl_compose_scope_scan "$project" "$repository_root"
    printf '  Ownership: %s\n' "$USL_COMPOSE_SCOPE_STATE"
    if [[ "$USL_COMPOSE_SCOPE_STATE" == "unavailable" ]]; then
        printf '  Services:  Docker is unavailable\n'
        return
    fi
    if [[ "$USL_COMPOSE_SCOPE_STATE" == "unused" ]]; then
        printf '  Services:  none\n'
        return
    fi
    printf '  Owners:\n'
    while IFS= read -r owner; do
        [[ -z "$owner" ]] && continue
        branch="$(usl_compose_owner_branch "$owner")"
        count="$(
            printf '%s\n' "$USL_COMPOSE_SCOPE_RESOURCES" \
                | awk -F'|' -v owner="$owner" '$4 == owner {count += 1} END {print count + 0}'
        )"
        printf '    - %s (%s, %s containers)\n' "$owner" "$branch" "$count"
    done <<<"$USL_COMPOSE_SCOPE_OWNERS"
    printf '  Services:\n'
    while IFS='|' read -r container_id container_name state owner service container_status oneoff; do
        [[ -z "$container_id" ]] && continue
        printf '    - %-34s %-22s %-28s %s\n' \
            "$container_name" "${service:-<unknown>}" \
            "${container_status:-$state}" "${owner:-<unknown>}"
    done <<<"$USL_COMPOSE_SCOPE_RESOURCES"
}

usl_compose_database_status() {
    local project="$1"
    local repository_root="$2"
    local database="$3"
    local container_id state query_output

    case "$database" in
        ""|*[!A-Za-z0-9_]*)
            USL_COMPOSE_DATABASE_STATUS="unsafe"
            return 2
            ;;
    esac
    usl_compose_scope_scan "$project" "$repository_root"
    container_id="$(
        printf '%s\n' "$USL_COMPOSE_SCOPE_RESOURCES" \
            | awk -F'|' '$5 == "db" && $3 == "running" {print $1; exit}'
    )"
    if [[ -z "$container_id" ]]; then
        USL_COMPOSE_DATABASE_STATUS="not-running"
        return 0
    fi
    if ! query_output="$(
        docker exec "$container_id" \
            psql -X -qAt -U "${ODOO_DB_USER:-odoo}" -d postgres \
            -c "SELECT 1 FROM pg_database WHERE datname = '$database'" \
            2>/dev/null
    )"; then
        USL_COMPOSE_DATABASE_STATUS="unavailable"
        return 0
    fi
    if [[ "$query_output" == "1" ]]; then
        state="present"
    else
        state="missing"
    fi
    USL_COMPOSE_DATABASE_STATUS="$state"
}

usl_compose_active_unsafe_resources() {
    local row container_id container_name state owner service container_status oneoff
    while IFS= read -r row; do
        [[ -z "$row" ]] && continue
        IFS='|' read -r container_id container_name state owner service container_status oneoff <<<"$row"
        case "$state" in
            running|restarting|paused|created|removing) ;;
            *) continue ;;
        esac
        if [[ "$oneoff" == "True" || "$oneoff" == "true" ]]; then
            printf '%s\n' "$row"
            continue
        fi
        case "$service" in
            init-db|test|*-migration|*-init|*restore*)
                printf '%s\n' "$row"
                ;;
        esac
    done <<<"${USL_COMPOSE_SCOPE_RESOURCES:-}"
}

usl_format_compose_resources() {
    local resources="$1"
    local container_id container_name state owner service container_status oneoff
    while IFS='|' read -r container_id container_name state owner service container_status oneoff; do
        [[ -z "$container_id" ]] && continue
        printf '%s (%s, %s, %s)\n' \
            "$container_name" "${service:-<unknown service>}" \
            "${container_status:-$state}" "${owner:-<unknown checkout>}"
    done <<<"$resources"
}

usl_remove_compose_containers() {
    local container_id container_name state owner service container_status oneoff
    while IFS='|' read -r container_id container_name state owner service container_status oneoff; do
        [[ -z "$container_id" ]] && continue
        docker rm --force "$container_id"
    done <<<"${USL_COMPOSE_SCOPE_RESOURCES:-}"
}

usl_verify_compose_scope() {
    local project="$1"
    local repository_root="$2"
    local purpose="${3:-Docker operation}"
    local canonical_project="${ODOO_CANONICAL_COMPOSE_PROJECT:-usl-odoo-saas-19-2}"

    case "$project" in
        ""|*[!a-zA-Z0-9_.-]*)
            printf '%s refused an unsafe Compose project: %s\n' \
                "$purpose" "${project:-<unset>}" >&2
            return 2
            ;;
    esac

    repository_root="$(cd "$repository_root" && pwd -P)"
    if [[ -f "$repository_root/.git" && "$project" == "$canonical_project" ]]; then
        usl_cli_blocked \
            "$purpose cannot use the canonical project from a linked worktree." \
            "The canonical project belongs exclusively to the main checkout." \
            "Use a dedicated COMPOSE_PROJECT and non-conflicting ports for $repository_root." \
            "Run: make doctor"
        return 2
    fi

    usl_compose_scope_scan "$project" "$repository_root"
    case "$USL_COMPOSE_SCOPE_STATE" in
        unused|owned) return 0 ;;
        foreign)
            usl_cli_blocked \
                "$purpose cannot safely use Compose project $project." \
                "Its containers belong to another checkout:
$(usl_compose_owner_summary)" \
                "Run: make doctor" \
                "From the main checkout, reclaim containers without deleting data: make dev-reclaim CONFIRM=$project"
            ;;
        mixed)
            usl_cli_blocked \
                "$purpose cannot safely use Compose project $project." \
                "The project is mixed across multiple checkouts:
$(usl_compose_owner_summary)" \
                "Run: make doctor" \
                "From the main checkout, reclaim containers without deleting data: make dev-reclaim CONFIRM=$project"
            ;;
        unavailable)
            usl_cli_blocked \
                "$purpose cannot inspect Compose project $project." \
                "Docker is unavailable or the current user cannot access it." \
                "Start Docker Desktop or Docker Engine, then run: make doctor"
            ;;
    esac
    return 2
}

usl_require_explicit_compose_project() {
    local purpose="${1:-Docker operation}"
    if [[ -z "${COMPOSE_PROJECT_NAME:-}" ]]; then
        printf '%s requires an explicit COMPOSE_PROJECT_NAME.\n' "$purpose" >&2
        return 2
    fi
}
