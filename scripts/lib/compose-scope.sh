#!/usr/bin/env bash

# A Compose project belongs to one checkout because its bind mounts and build
# context resolve from that checkout. Keep this guard dependency-free so every
# host-side helper can call it before mutating shared Docker state.

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
        printf '%s refused the canonical Compose project from a linked worktree.\n' \
            "$purpose" >&2
        printf 'Set COMPOSE_PROJECT_NAME to a dedicated project for %s.\n' \
            "$repository_root" >&2
        return 2
    fi

    local container_id project_label working_dir_label
    while IFS= read -r container_id; do
        [[ -z "$container_id" ]] && continue
        project_label="$(
            docker inspect --format \
                '{{ index .Config.Labels "com.docker.compose.project" }}' \
                "$container_id"
        )"
        working_dir_label="$(
            docker inspect --format \
                '{{ index .Config.Labels "com.docker.compose.project.working_dir" }}' \
                "$container_id"
        )"
        if [[ "$project_label" != "$project" \
            || "$working_dir_label" != "$repository_root" ]]; then
            printf '%s found a foreign Compose resource: %s\n' \
                "$purpose" "$container_id" >&2
            printf 'Expected project=%s working_dir=%s; found project=%s working_dir=%s.\n' \
                "$project" "$repository_root" \
                "${project_label:-<unset>}" "${working_dir_label:-<unset>}" >&2
            return 2
        fi
    done < <(
        docker ps -aq \
            --filter "label=com.docker.compose.project=$project"
    )
}

usl_require_explicit_compose_project() {
    local purpose="${1:-Docker operation}"
    if [[ -z "${COMPOSE_PROJECT_NAME:-}" ]]; then
        printf '%s requires an explicit COMPOSE_PROJECT_NAME.\n' "$purpose" >&2
        return 2
    fi
}
