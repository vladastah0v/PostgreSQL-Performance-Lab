"""Add indexes for the optimized API queries."""

import sqlalchemy as sa
from alembic import op

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_index(
        "ix_users_city_status_id", "users", [sa.text("lower(city)"), "status", "id"]
    )
    op.create_index("ix_orders_created_id", "orders", ["created_at", "id"])
    op.create_index(
        "ix_orders_status_created_id", "orders", ["status", "created_at", "id"]
    )
    op.create_index(
        "ix_orders_user_created_id", "orders", ["user_id", "created_at", "id"]
    )
    op.create_index("ix_order_items_order_id_id", "order_items", ["order_id", "id"])
    op.create_index("ix_payments_order_id_id", "payments", ["order_id", "id"])
    op.create_index(
        "ix_user_events_user_created_id", "user_events", ["user_id", "created_at", "id"]
    )
    op.create_index(
        "ix_orders_sales_created_cover",
        "orders",
        ["created_at"],
        postgresql_include=["user_id", "total_amount"],
        postgresql_where=sa.text("status IN ('paid', 'shipped', 'delivered')"),
    )


def downgrade() -> None:
    op.drop_index("ix_orders_sales_created_cover", table_name="orders")
    op.drop_index("ix_user_events_user_created_id", table_name="user_events")
    op.drop_index("ix_payments_order_id_id", table_name="payments")
    op.drop_index("ix_order_items_order_id_id", table_name="order_items")
    op.drop_index("ix_orders_user_created_id", table_name="orders")
    op.drop_index("ix_orders_status_created_id", table_name="orders")
    op.drop_index("ix_orders_created_id", table_name="orders")
    op.drop_index("ix_users_city_status_id", table_name="users")
