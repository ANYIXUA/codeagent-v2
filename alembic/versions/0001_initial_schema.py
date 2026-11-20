"""create run persistence schema

Revision ID: 0001_initial_schema
Revises:
Create Date: 2026-11-20 00:00:00

"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0001_initial_schema"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

JSONB = postgresql.JSONB
TIMESTAMPTZ = sa.TIMESTAMP(timezone=True)
NOW = sa.text("now()")


def upgrade() -> None:
    op.create_table(
        "agent_runs",
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

    op.create_table(
        "agent_steps",
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

    op.create_table(
        "run_events",
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

    op.create_table(
        "model_calls",
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

    op.create_table(
        "tool_calls",
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

    op.create_table(
        "checkpoints",
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

    op.create_table(
        "usage_records",
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

    op.create_table(
        "audit_logs",
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

    op.create_index(
        "idx_agent_runs_org_created",
        "agent_runs",
        ["org_id", sa.text("created_at DESC")],
    )
    op.create_index(
        "idx_agent_runs_status_created",
        "agent_runs",
        ["status", "created_at"],
    )
    op.create_index(
        "idx_agent_steps_run_index",
        "agent_steps",
        ["run_id", "step_index"],
    )
    op.create_index(
        "idx_run_events_run_sequence",
        "run_events",
        ["run_id", "sequence"],
    )
    op.create_index(
        "idx_model_calls_run_created",
        "model_calls",
        ["run_id", "created_at"],
    )
    op.create_index(
        "idx_tool_calls_run_created",
        "tool_calls",
        ["run_id", "created_at"],
    )
    op.create_index(
        "idx_tool_calls_tool_status",
        "tool_calls",
        ["tool_name", "status", "created_at"],
    )
    op.create_index(
        "idx_usage_records_org_created",
        "usage_records",
        ["org_id", "created_at"],
    )
    op.create_index(
        "idx_audit_logs_org_created",
        "audit_logs",
        ["org_id", "created_at"],
    )


def downgrade() -> None:
    op.drop_table("audit_logs")
    op.drop_table("usage_records")
    op.drop_table("checkpoints")
    op.drop_table("tool_calls")
    op.drop_table("model_calls")
    op.drop_table("run_events")
    op.drop_table("agent_steps")
    op.drop_table("agent_runs")
