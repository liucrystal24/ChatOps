"""身份、知识库及授权短事务的单行锁。"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import mysql

revision = "0002_identity_knowledge_bases"
down_revision = "0001_baseline"
branch_labels = None
depends_on = None


def timestamps():
    return [
        sa.Column(
            name,
            mysql.DATETIME(fsp=6),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP(6)"),
        )
        for name in ("created_at", "updated_at")
    ]


def upgrade():
    op.create_table(
        "authorization_guard",
        sa.Column("id", mysql.TINYINT(unsigned=True), primary_key=True, autoincrement=False),
        sa.CheckConstraint("id = 1", name="ck_authorization_guard_singleton"),
        mysql_engine="InnoDB",
        mysql_charset="utf8mb4",
    )
    op.execute("INSERT INTO authorization_guard (id) VALUES (1)")
    op.create_table(
        "users",
        sa.Column("id", mysql.CHAR(36, charset="ascii"), primary_key=True),
        sa.Column("username", sa.String(64, collation="utf8mb4_bin"), nullable=False),
        sa.Column("password_hash", sa.String(255), nullable=False),
        sa.Column("is_active", sa.Boolean, nullable=False),
        sa.Column("is_admin", sa.Boolean, nullable=False),
        *timestamps(),
        sa.UniqueConstraint("username", name="uq_users_username"),
        mysql_engine="InnoDB",
        mysql_charset="utf8mb4",
    )
    op.create_table(
        "knowledge_bases",
        sa.Column("id", mysql.CHAR(36, charset="ascii"), primary_key=True),
        sa.Column("name", sa.String(120), nullable=False),
        sa.Column("description", sa.Text, nullable=False),
        sa.Column("created_by", mysql.CHAR(36, charset="ascii"), nullable=False),
        sa.Column("embedding_profile_id", sa.String(64), nullable=True),
        sa.Column("embedding_profile", mysql.JSON(none_as_null=True), nullable=True),
        sa.Column("deleted_at", mysql.DATETIME(fsp=6), nullable=True),
        *timestamps(),
        sa.ForeignKeyConstraint(["created_by"], ["users.id"], ondelete="RESTRICT"),
        sa.CheckConstraint(
            "(embedding_profile_id IS NULL AND embedding_profile IS NULL) OR "
            "(embedding_profile_id IS NOT NULL AND embedding_profile IS NOT NULL)",
            name="ck_kb_embedding_profile_pair",
        ),
        mysql_engine="InnoDB",
        mysql_charset="utf8mb4",
    )
    op.create_index(
        "ix_kb_creator_deleted_created",
        "knowledge_bases",
        ["created_by", "deleted_at", "created_at"],
    )
    op.create_table(
        "knowledge_base_members",
        sa.Column("kb_id", mysql.CHAR(36, charset="ascii"), primary_key=True),
        sa.Column("user_id", mysql.CHAR(36, charset="ascii"), primary_key=True),
        sa.Column("role", sa.String(16, collation="utf8mb4_bin"), nullable=False),
        *timestamps(),
        sa.ForeignKeyConstraint(["kb_id"], ["knowledge_bases.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="RESTRICT"),
        sa.CheckConstraint("role IN ('owner', 'editor', 'viewer')", name="ck_member_role"),
        mysql_engine="InnoDB",
        mysql_charset="utf8mb4",
    )
    op.create_index("ix_member_user_kb", "knowledge_base_members", ["user_id", "kb_id"])


def downgrade():
    op.drop_table("knowledge_base_members")
    op.drop_table("knowledge_bases")
    op.drop_table("users")
    op.drop_table("authorization_guard")
