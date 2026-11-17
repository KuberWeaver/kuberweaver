"""Read-only SQL surface for external dashboards.

The platform does not draw charts. Grafana, Metabase or a Python script query
these views directly, so a dashboard can be replaced without touching the
product, and a visualisation can join the platform's facts with anything else in
the database.

Only PostgreSQL gets the schema: SQLite deployments keep the application tables
and the API. Every view is created independently, so one malformed legacy row
does not take the whole surface down.
"""
from __future__ import annotations

import json

SCHEMA = 'reporting'

VIEWS: dict[str, str] = {
    'jobs': """
        CREATE OR REPLACE VIEW {schema}.jobs AS
        SELECT id,
               body::jsonb ->> 'name' AS name,
               body::jsonb ->> 'environment' AS environment,
               body::jsonb ->> 'operation' AS operation,
               body::jsonb ->> 'status' AS status,
               body::jsonb ->> 'created_by' AS created_by,
               NULLIF(body::jsonb ->> 'approved_by', '') AS approved_by,
               NULLIF(body::jsonb ->> 'created_at', '')::timestamptz AS created_at,
               NULLIF(body::jsonb ->> 'approved_at', '')::timestamptz AS approved_at,
               NULLIF(body::jsonb ->> 'finished_at', '')::timestamptz AS finished_at,
               COALESCE(jsonb_array_length(body::jsonb -> 'graph' -> 'nodes'), 0) AS nodes,
               (body::jsonb -> 'cost' ->> 'total_monthly') AS cost_monthly
        FROM jobs
    """,
    'deployments': """
        CREATE OR REPLACE VIEW {schema}.deployments AS
        SELECT DISTINCT ON (environment, name)
               environment,
               name,
               plugin,
               job_id,
               deployed_at,
               finished_at,
               deployed_by
        FROM (
            SELECT body::jsonb ->> 'environment' AS environment,
                   node ->> 'label' AS name,
                   node ->> 'plugin_id' AS plugin,
                   jobs.id AS job_id,
                   NULLIF(body::jsonb ->> 'created_at', '')::timestamptz AS deployed_at,
                   NULLIF(body::jsonb ->> 'finished_at', '')::timestamptz AS finished_at,
                   body::jsonb ->> 'created_by' AS deployed_by
            FROM jobs
            CROSS JOIN LATERAL jsonb_array_elements(
                COALESCE(body::jsonb -> 'graph' -> 'nodes', '[]'::jsonb)) AS node
            WHERE body::jsonb ->> 'status' = 'succeeded'
        ) AS applied
        ORDER BY environment, name, deployed_at DESC
    """,
    'dashboards': """
        CREATE OR REPLACE VIEW {schema}.dashboards AS
        SELECT id,
               body::jsonb ->> 'label' AS label,
               body::jsonb ->> 'kind' AS kind,
               body::jsonb ->> 'url' AS url,
               COALESCE((body::jsonb ->> 'enabled')::boolean, true) AS enabled,
               COALESCE((body::jsonb ->> 'embed')::boolean, false) AS embed,
               NULLIF(body::jsonb ->> 'created_at', '')::timestamptz AS created_at
        FROM dashboards
    """,
    'drift': """
        CREATE OR REPLACE VIEW {schema}.drift AS
        SELECT id,
               body::jsonb ->> 'environment' AS environment,
               body::jsonb ->> 'status' AS status,
               NULLIF(body::jsonb ->> 'checked_at', '')::timestamptz AS checked_at,
               jsonb_array_length(COALESCE(body::jsonb -> 'differences', '[]'::jsonb)) AS differences
        FROM environment_state_drift
    """,
}


def ensure_reporting(store) -> dict:
    """Create the reporting schema and views. Returns what was created."""
    if store.backend != 'postgresql':
        return {'available': False, 'reason': 'reporting views require PostgreSQL', 'views': []}
    created, failed = [], []
    try:
        store.execute(f'CREATE SCHEMA IF NOT EXISTS {SCHEMA}')
    except Exception as error:  # noqa: BLE001 - reported, never fatal
        return {'available': False, 'reason': str(error), 'views': []}
    for name, statement in VIEWS.items():
        try:
            store.execute(statement.format(schema=SCHEMA))
            created.append(name)
        except Exception as error:  # noqa: BLE001 - one legacy table must not stop the rest
            failed.append({'view': name, 'error': str(error).splitlines()[0]})
    return {'available': True, 'views': created, 'failed': failed}


def describe(store) -> dict:
    """Report the surface without exposing credentials."""
    if store.backend != 'postgresql':
        return {'available': False, 'reason': 'reporting views require PostgreSQL'}
    rows = store.all(
        'SELECT table_name AS name FROM information_schema.views '
        'WHERE table_schema = %s ORDER BY 1', (SCHEMA,))
    names = [row['name'] if isinstance(row, dict) else row[0] for row in rows]
    return {
        'available': True,
        'schema': SCHEMA,
        'views': names or sorted(VIEWS),
        'example': 'SELECT * FROM ' + SCHEMA + '.deployments ORDER BY deployed_at DESC LIMIT 50',
        'note': 'The platform exposes data, not charts. Point Grafana, Metabase or pandas at these views.',
    }