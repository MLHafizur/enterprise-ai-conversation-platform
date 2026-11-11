#!/usr/bin/env bash

set -Eeuo pipefail

required_variables=(
POSTGRES_USER
POSTGRES_DB
POSTGRES_APP_DATABASE
POSTGRES_APP_SCHEMA
POSTGRES_MIGRATOR_USER
POSTGRES_MIGRATOR_PASSWORD
POSTGRES_RUNTIME_USER
POSTGRES_RUNTIME_PASSWORD
)

for variable_name in "${required_variables[@]}"; do
if [[ -z "${!variable_name:-}" ]]; then
    printf 'Required environment variable %s is empty\n' \
    "$variable_name" >&2
    exit 1
fi
done

identifier_variables=(
POSTGRES_APP_DATABASE
POSTGRES_APP_SCHEMA
POSTGRES_MIGRATOR_USER
POSTGRES_RUNTIME_USER
)

for variable_name in "${identifier_variables[@]}"; do
identifier_value="${!variable_name}"

if [[ ! "$identifier_value" =~ ^[a-z][a-z0-9_]{0,62}$ ]]; then
    printf '%s must be a lowercase PostgreSQL identifier\n' \
    "$variable_name" >&2
    exit 1
fi
done

psql \
--no-psqlrc \
--set=ON_ERROR_STOP=1 \
--username="$POSTGRES_USER" \
--dbname="$POSTGRES_DB" \
--set=app_database="$POSTGRES_APP_DATABASE" \
--set=migrator_user="$POSTGRES_MIGRATOR_USER" \
--set=migrator_password="$POSTGRES_MIGRATOR_PASSWORD" \
--set=runtime_user="$POSTGRES_RUNTIME_USER" \
--set=runtime_password="$POSTGRES_RUNTIME_PASSWORD" <<'SQL'
CREATE ROLE :"migrator_user"
    WITH LOGIN
    PASSWORD :'migrator_password'
    NOSUPERUSER
    NOCREATEDB
    NOCREATEROLE
    NOINHERIT
    NOREPLICATION
    NOBYPASSRLS;

CREATE ROLE :"runtime_user"
    WITH LOGIN
    PASSWORD :'runtime_password'
    NOSUPERUSER
    NOCREATEDB
    NOCREATEROLE
    NOINHERIT
    NOREPLICATION
    NOBYPASSRLS;

CREATE DATABASE :"app_database"
    OWNER :"migrator_user";

REVOKE ALL PRIVILEGES
    ON DATABASE :"app_database"
    FROM PUBLIC;

GRANT CONNECT
    ON DATABASE :"app_database"
    TO :"migrator_user", :"runtime_user";
SQL

psql \
--no-psqlrc \
--set=ON_ERROR_STOP=1 \
--username="$POSTGRES_USER" \
--dbname="$POSTGRES_APP_DATABASE" \
--set=app_database="$POSTGRES_APP_DATABASE" \
--set=app_schema="$POSTGRES_APP_SCHEMA" \
--set=migrator_user="$POSTGRES_MIGRATOR_USER" \
--set=runtime_user="$POSTGRES_RUNTIME_USER" <<'SQL'
REVOKE ALL PRIVILEGES
    ON SCHEMA public
    FROM PUBLIC;

CREATE SCHEMA :"app_schema"
    AUTHORIZATION :"migrator_user";

GRANT USAGE
    ON SCHEMA :"app_schema"
    TO :"runtime_user";

ALTER DEFAULT PRIVILEGES
    FOR ROLE :"migrator_user"
    IN SCHEMA :"app_schema"
    GRANT SELECT, INSERT, UPDATE, DELETE
    ON TABLES
    TO :"runtime_user";

ALTER DEFAULT PRIVILEGES
    FOR ROLE :"migrator_user"
    IN SCHEMA :"app_schema"
    GRANT USAGE, SELECT
    ON SEQUENCES
    TO :"runtime_user";

ALTER DEFAULT PRIVILEGES
    FOR ROLE :"migrator_user"
    IN SCHEMA :"app_schema"
    REVOKE EXECUTE
    ON FUNCTIONS
    FROM PUBLIC;

ALTER DATABASE :"app_database"
    SET timezone TO 'UTC';

ALTER ROLE :"migrator_user"
    IN DATABASE :"app_database"
    SET search_path TO :"app_schema", pg_catalog;

ALTER ROLE :"runtime_user"
    IN DATABASE :"app_database"
    SET search_path TO :"app_schema", pg_catalog;
SQL

printf 'PostgreSQL application database and roles initialized\n'