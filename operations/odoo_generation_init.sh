#!/usr/bin/env bash
# The postgres image runs this once, as its OS user, inside a fresh generation.
set -euo pipefail
app_password="${!USL_PG_APP_PASSWORD_KEY:?missing Odoo database password}"
psql --no-psqlrc --quiet --set=ON_ERROR_STOP=1 --username postgres --dbname "$POSTGRES_DB" <<'SQL'
\getenv app_user USL_PG_APP_USER
\getenv app_database POSTGRES_DB
CREATE ROLE :"app_user" LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS;
GRANT CONNECT, CREATE, TEMPORARY ON DATABASE :"app_database" TO :"app_user";
GRANT USAGE, CREATE ON SCHEMA public TO :"app_user";
ALTER ROLE postgres PASSWORD NULL;
SQL
# pgvector is not a trusted extension, so the NOSUPERUSER application role
# cannot create it while restoring a dump that holds a vector column. Create it
# here, as the superuser, whenever the image ships it. Images without pgvector
# are left exactly as they were.
psql --no-psqlrc --quiet --set=ON_ERROR_STOP=1 --username postgres --dbname "$POSTGRES_DB" <<'SQL'
SELECT EXISTS (SELECT FROM pg_available_extensions WHERE name = 'vector') AS has_vector \gset
\if :has_vector
CREATE EXTENSION IF NOT EXISTS vector;
\endif
SQL
# psql's password command encrypts on the client. The cleartext is never
# interpolated into a SQL statement, even when statement logging is enabled.
printf '%s\n%s\n' "$app_password" "$app_password" |
    psql --no-psqlrc --quiet --set=ON_ERROR_STOP=1 --username postgres --dbname "$POSTGRES_DB" \
        --command="\\password \"$USL_PG_APP_USER\""
unset app_password

# The operator's native docker exec/psql probes run as the container's root
# user. Map that socket identity only to the ordinary application role.
printf 'usl_runtime root "%s"\n' "$USL_PG_APP_USER" >> "$PGDATA/pg_ident.conf"
{
    printf 'local all "%s" peer map=usl_runtime\n' "$USL_PG_APP_USER"
    cat "$PGDATA/pg_hba.conf"
} > "$PGDATA/pg_hba.conf.new"
chmod 600 "$PGDATA/pg_hba.conf.new"
mv "$PGDATA/pg_hba.conf.new" "$PGDATA/pg_hba.conf"
