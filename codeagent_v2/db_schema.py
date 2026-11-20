from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

metadata = sa.MetaData()

JSONB = postgresql.JSONB
TIMESTAMPTZ = sa.TIMESTAMP(timezone=True)
NOW = sa.text("now()")


agent_runs = sa.Table(
    "agent_runs",
    metadata,
    sa.Column("id", sa.Text(), primary_key=True),
    sa.Column("org_id", sa.Text(), nullable=False),
    sa.Column("user_id", sa.Text(), nullable=False),
    sa.Column("run_type", sa.Text(), nullable=False),
    sa.Column("status", sa.Text(), nullable=False),
    sa.Column("idempotency_key", sa.Text(), nullable=False),
    sa.Column("input_json", JSONB(), nullable=False),
    sa.Column("output_json", JSONB()),
    sa.Column("error_json", JSONB()),
    sa.Column("created_at", TIMESTAMPTZ, nullable=False, server_default=NOW),
    sa.Column("started_at", TIMESTAMPTZ),
    sa.Column("ended_at", TIMESTAMPTZ),
    sa.Column("updated_at", TIMESTAMPTZ, nullable=False, server_default=NOW),
    sa.UniqueConstraint(
        "org_id",
        "idempotency_key",
        name="uq_agent_runs_org_idempotency",
    ),
)

agent_steps = sa.Table(
    "agent_steps",
    metadata,
    sa.Column("id", sa.Text(), primary_key=True),
    sa.Column(
        "run_id",
        sa.Text(),
        sa.ForeignKey("agent_runs.id", name="fk_agent_steps_run_id_agent_runs"),
        nullable=False,
    ),
    sa.Column("step_index", sa.Integer(), nullable=False),
    sa.Column("step_type", sa.Text(), nullable=False),
    sa.Column("name", sa.Text(), nullable=False),
    sa.Column("status", sa.Text(), nullable=False),
    sa.Column("input_json", JSONB()),
    sa.Column("output_json", JSONB()),
    sa.Column("error_json", JSONB()),
    sa.Column("started_at", TIMESTAMPTZ, nullable=False, server_default=NOW),
    sa.Column("ended_at", TIMESTAMPTZ),
    sa.UniqueConstraint("run_id", "step_index", name="uq_agent_steps_run_index"),
)

run_events = sa.Table(
    "run_events",
    metadata,
    sa.Column("id", sa.Text(), primary_key=True),
    sa.Column(
        "run_id",
        sa.Text(),
        sa.ForeignKey("agent_runs.id", name="fk_run_events_run_id_agent_runs"),
        nullable=False,
    ),
    sa.Column("sequence", sa.Integer(), nullable=False),
    sa.Column("event_type", sa.Text(), nullable=False),
    sa.Column("payload_json", JSONB(), nullable=False),
    sa.Column("created_at", TIMESTAMPTZ, nullable=False, server_default=NOW),
    sa.UniqueConstraint("run_id", "sequence", name="uq_run_events_run_sequence"),
)

model_calls = sa.Table(
    "model_calls",
    metadata,
    sa.Column("id", sa.Text(), primary_key=True),
    sa.Column(
        "run_id",
        sa.Text(),
        sa.ForeignKey("agent_runs.id", name="fk_model_calls_run_id_agent_runs"),
        nullable=False,
    ),
    sa.Column(
        "step_id",
        sa.Text(),
        sa.ForeignKey("agent_steps.id", name="fk_model_calls_step_id_agent_steps"),
    ),
    sa.Column("provider", sa.Text(), nullable=False),
    sa.Column("model", sa.Text(), nullable=False),
    sa.Column("request_json", JSONB(), nullable=False),
    sa.Column("response_json", JSONB()),
    sa.Column("prompt_tokens", sa.Integer()),
    sa.Column("completion_tokens", sa.Integer()),
    sa.Column("latency_ms", sa.Integer()),
    sa.Column("status", sa.Text(), nullable=False),
    sa.Column("error_json", JSONB()),
    sa.Column("created_at", TIMESTAMPTZ, nullable=False, server_default=NOW),
)

tool_calls = sa.Table(
    "tool_calls",
    metadata,
    sa.Column("id", sa.Text(), primary_key=True),
    sa.Column(
        "run_id",
        sa.Text(),
        sa.ForeignKey("agent_runs.id", name="fk_tool_calls_run_id_agent_runs"),
        nullable=False,
    ),
    sa.Column(
        "step_id",
        sa.Text(),
        sa.ForeignKey("agent_steps.id", name="fk_tool_calls_step_id_agent_steps"),
    ),
    sa.Column("tool_name", sa.Text(), nullable=False),
    sa.Column("args_json", JSONB(), nullable=False),
    sa.Column("result_json", JSONB()),
    sa.Column("status", sa.Text(), nullable=False),
    sa.Column("idempotency_key", sa.Text()),
    sa.Column("latency_ms", sa.Integer()),
    sa.Column("error_json", JSONB()),
    sa.Column("created_at", TIMESTAMPTZ, nullable=False, server_default=NOW),
    sa.Column("ended_at", TIMESTAMPTZ),
    sa.UniqueConstraint(
        "tool_name",
        "idempotency_key",
        name="uq_tool_calls_tool_idempotency",
    ),
)

checkpoints = sa.Table(
    "checkpoints",
    metadata,
    sa.Column("id", sa.Text(), primary_key=True),
    sa.Column(
        "run_id",
        sa.Text(),
        sa.ForeignKey("agent_runs.id", name="fk_checkpoints_run_id_agent_runs"),
        nullable=False,
    ),
    sa.Column("checkpoint_index", sa.Integer(), nullable=False),
    sa.Column("state_json", JSONB(), nullable=False),
    sa.Column("created_at", TIMESTAMPTZ, nullable=False, server_default=NOW),
    sa.UniqueConstraint(
        "run_id",
        "checkpoint_index",
        name="uq_checkpoints_run_index",
    ),
)

usage_records = sa.Table(
    "usage_records",
    metadata,
    sa.Column("id", sa.Text(), primary_key=True),
    sa.Column(
        "run_id",
        sa.Text(),
        sa.ForeignKey("agent_runs.id", name="fk_usage_records_run_id_agent_runs"),
        nullable=False,
    ),
    sa.Column("org_id", sa.Text(), nullable=False),
    sa.Column("user_id", sa.Text(), nullable=False),
    sa.Column("provider", sa.Text(), nullable=False),
    sa.Column("model", sa.Text()),
    sa.Column("prompt_tokens", sa.Integer(), server_default=sa.text("0")),
    sa.Column("completion_tokens", sa.Integer(), server_default=sa.text("0")),
    sa.Column("total_tokens", sa.Integer(), server_default=sa.text("0")),
    sa.Column("estimated_cost_cents", sa.Integer(), server_default=sa.text("0")),
    sa.Column("created_at", TIMESTAMPTZ, nullable=False, server_default=NOW),
)

audit_logs = sa.Table(
    "audit_logs",
    metadata,
    sa.Column("id", sa.Text(), primary_key=True),
    sa.Column("org_id", sa.Text(), nullable=False),
    sa.Column("user_id", sa.Text(), nullable=False),
    sa.Column(
        "run_id",
        sa.Text(),
        sa.ForeignKey("agent_runs.id", name="fk_audit_logs_run_id_agent_runs"),
    ),
    sa.Column("action", sa.Text(), nullable=False),
    sa.Column("target_type", sa.Text(), nullable=False),
    sa.Column("target_id", sa.Text(), nullable=False),
    sa.Column("metadata_json", JSONB()),
    sa.Column("created_at", TIMESTAMPTZ, nullable=False, server_default=NOW),
)

sa.Index(
    "idx_agent_runs_org_created",
    agent_runs.c.org_id,
    agent_runs.c.created_at.desc(),
)
sa.Index(
    "idx_agent_runs_status_created",
    agent_runs.c.status,
    agent_runs.c.created_at,
)
sa.Index(
    "idx_agent_steps_run_index",
    agent_steps.c.run_id,
    agent_steps.c.step_index,
)
sa.Index(
    "idx_run_events_run_sequence",
    run_events.c.run_id,
    run_events.c.sequence,
)
sa.Index(
    "idx_model_calls_run_created",
    model_calls.c.run_id,
    model_calls.c.created_at,
)
sa.Index(
    "idx_tool_calls_run_created",
    tool_calls.c.run_id,
    tool_calls.c.created_at,
)
sa.Index(
    "idx_tool_calls_tool_status",
    tool_calls.c.tool_name,
    tool_calls.c.status,
    tool_calls.c.created_at,
)
sa.Index(
    "idx_usage_records_org_created",
    usage_records.c.org_id,
    usage_records.c.created_at,
)
sa.Index(
    "idx_audit_logs_org_created",
    audit_logs.c.org_id,
    audit_logs.c.created_at,
)
