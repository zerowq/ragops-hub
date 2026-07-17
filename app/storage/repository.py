from __future__ import annotations

import json
import sqlite3
import threading
import uuid
from pathlib import Path
from typing import Any

from app.domain.models import Chunk, Principal, SearchHit, utc_now
from app.rag.tokenization import to_fts_query, tokenize


class DuplicateDocumentError(Exception):
    pass


class SQLiteRepository:
    """Operational metadata repository used by the local profile.

    The schema keeps tenant IDs on every business row. The same repository
    boundary can be replaced by PostgreSQL without changing retrieval or agent
    services.
    """

    def __init__(self, path: Path) -> None:
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self.fts_enabled = False
        self.ticket_fts_enabled = False
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, check_same_thread=False)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute("PRAGMA journal_mode=WAL")
        return connection

    def _initialize(self) -> None:
        statements = [
            """
            CREATE TABLE IF NOT EXISTS documents (
                id TEXT PRIMARY KEY,
                tenant_id TEXT NOT NULL,
                department_id TEXT NOT NULL,
                owner_user_id TEXT NOT NULL,
                title TEXT NOT NULL,
                source TEXT NOT NULL,
                visibility TEXT NOT NULL,
                version INTEGER NOT NULL,
                content_hash TEXT NOT NULL,
                status TEXT NOT NULL,
                created_at TEXT NOT NULL,
                UNIQUE(tenant_id, source, version)
            )
            """,
            """
            CREATE TABLE IF NOT EXISTS chunks (
                id TEXT PRIMARY KEY,
                tenant_id TEXT NOT NULL,
                department_id TEXT NOT NULL,
                document_id TEXT NOT NULL,
                document_version INTEGER NOT NULL,
                visibility TEXT NOT NULL,
                content TEXT NOT NULL,
                source TEXT NOT NULL,
                title TEXT NOT NULL,
                position INTEGER NOT NULL,
                metadata_json TEXT NOT NULL,
                FOREIGN KEY(document_id) REFERENCES documents(id) ON DELETE CASCADE
            )
            """,
            """
            CREATE TABLE IF NOT EXISTS orders (
                id TEXT PRIMARY KEY,
                tenant_id TEXT NOT NULL,
                user_id TEXT NOT NULL,
                status TEXT NOT NULL,
                product_name TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """,
            """
            CREATE TABLE IF NOT EXISTS customers (
                id TEXT NOT NULL,
                tenant_id TEXT NOT NULL,
                name TEXT NOT NULL,
                company_name TEXT NOT NULL,
                level TEXT NOT NULL,
                industry TEXT NOT NULL,
                company_size TEXT NOT NULL,
                phone_masked TEXT NOT NULL,
                email_masked TEXT NOT NULL,
                last_contact_at TEXT NOT NULL,
                PRIMARY KEY(id, tenant_id)
            )
            """,
            """
            CREATE TABLE IF NOT EXISTS support_cases (
                id TEXT PRIMARY KEY,
                tenant_id TEXT NOT NULL,
                customer_user_id TEXT NOT NULL,
                customer_name TEXT NOT NULL,
                company_name TEXT NOT NULL,
                order_id TEXT,
                subject TEXT NOT NULL,
                preview TEXT NOT NULL,
                priority TEXT NOT NULL,
                status TEXT NOT NULL,
                channel TEXT NOT NULL,
                assignee_user_id TEXT NOT NULL,
                sla_due_at TEXT NOT NULL,
                ticket_id TEXT,
                updated_at TEXT NOT NULL
            )
            """,
            """
            CREATE TABLE IF NOT EXISTS tickets (
                id TEXT PRIMARY KEY,
                tenant_id TEXT NOT NULL,
                user_id TEXT NOT NULL,
                subject TEXT NOT NULL,
                description TEXT NOT NULL,
                status TEXT NOT NULL,
                idempotency_key TEXT NOT NULL,
                customer_user_id TEXT NOT NULL DEFAULT '',
                case_id TEXT NOT NULL DEFAULT '',
                order_id TEXT NOT NULL DEFAULT '',
                handoff_summary TEXT NOT NULL DEFAULT '',
                resolution TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL,
                UNIQUE(tenant_id, idempotency_key)
            )
            """,
            """
            CREATE TABLE IF NOT EXISTS conversations (
                id TEXT PRIMARY KEY,
                tenant_id TEXT NOT NULL,
                user_id TEXT NOT NULL,
                case_id TEXT NOT NULL DEFAULT '',
                pending_action_json TEXT,
                summary TEXT NOT NULL DEFAULT '',
                summarized_message_count INTEGER NOT NULL DEFAULT 0,
                summary_updated_at TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """,
            """
            CREATE TABLE IF NOT EXISTS messages (
                id TEXT PRIMARY KEY,
                conversation_id TEXT NOT NULL,
                tenant_id TEXT NOT NULL,
                role TEXT NOT NULL,
                content TEXT NOT NULL,
                metadata_json TEXT NOT NULL,
                created_at TEXT NOT NULL
            )
            """,
            """
            CREATE TABLE IF NOT EXISTS audit_logs (
                id TEXT PRIMARY KEY,
                tenant_id TEXT NOT NULL,
                user_id TEXT NOT NULL,
                action TEXT NOT NULL,
                resource_type TEXT NOT NULL,
                resource_id TEXT NOT NULL,
                payload_json TEXT NOT NULL,
                created_at TEXT NOT NULL
            )
            """,
        ]
        with self._lock, self._connect() as connection:
            for statement in statements:
                connection.execute(statement)
            order_columns = {
                row["name"]
                for row in connection.execute("PRAGMA table_info(orders)").fetchall()
            }
            order_migrations = {
                "product_version": "TEXT NOT NULL DEFAULT '专业版'",
                "purchased_at": "TEXT NOT NULL DEFAULT ''",
                "valid_until": "TEXT NOT NULL DEFAULT ''",
                "amount_cents": "INTEGER NOT NULL DEFAULT 0",
                "seats_total": "INTEGER NOT NULL DEFAULT 0",
                "seats_used": "INTEGER NOT NULL DEFAULT 0",
                "knowledge_quota_gb": "INTEGER NOT NULL DEFAULT 0",
                "knowledge_used_gb": "INTEGER NOT NULL DEFAULT 0",
                "api_quota": "INTEGER NOT NULL DEFAULT 0",
                "api_used": "INTEGER NOT NULL DEFAULT 0",
            }
            for name, definition in order_migrations.items():
                if name not in order_columns:
                    connection.execute(
                        f"ALTER TABLE orders ADD COLUMN {name} {definition}"
                    )
            ticket_columns = {
                row["name"]
                for row in connection.execute("PRAGMA table_info(tickets)").fetchall()
            }
            ticket_migrations = {
                "customer_user_id": "TEXT NOT NULL DEFAULT ''",
                "case_id": "TEXT NOT NULL DEFAULT ''",
                "order_id": "TEXT NOT NULL DEFAULT ''",
                "handoff_summary": "TEXT NOT NULL DEFAULT ''",
                "resolution": "TEXT NOT NULL DEFAULT ''",
            }
            for name, definition in ticket_migrations.items():
                if name not in ticket_columns:
                    connection.execute(
                        f"ALTER TABLE tickets ADD COLUMN {name} {definition}"
                    )
            conversation_columns = {
                row["name"]
                for row in connection.execute(
                    "PRAGMA table_info(conversations)"
                ).fetchall()
            }
            conversation_migrations = {
                "case_id": "TEXT NOT NULL DEFAULT ''",
                "summary": "TEXT NOT NULL DEFAULT ''",
                "summarized_message_count": "INTEGER NOT NULL DEFAULT 0",
                "summary_updated_at": "TEXT NOT NULL DEFAULT ''",
            }
            for name, definition in conversation_migrations.items():
                if name not in conversation_columns:
                    connection.execute(
                        f"ALTER TABLE conversations ADD COLUMN {name} {definition}"
                    )
            columns = {
                row["name"]
                for row in connection.execute("PRAGMA table_info(documents)").fetchall()
            }
            if "owner_user_id" not in columns:
                connection.execute(
                    "ALTER TABLE documents ADD COLUMN owner_user_id TEXT NOT NULL DEFAULT ''"
                )
                connection.execute(
                    """
                    UPDATE documents
                    SET owner_user_id=COALESCE(
                        (
                            SELECT json_extract(chunks.metadata_json, '$.owner_user_id')
                            FROM chunks
                            WHERE chunks.document_id=documents.id
                            LIMIT 1
                        ),
                        ''
                    )
                    """
                )
            connection.execute("DROP INDEX IF EXISTS uq_documents_tenant_hash_version")
            connection.execute(
                """
                CREATE UNIQUE INDEX IF NOT EXISTS uq_documents_tenant_hash_version
                ON documents(tenant_id, content_hash, version)
                WHERE status IN ('processing', 'ready')
                """
            )
            connection.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_documents_access
                ON documents(tenant_id, status, visibility, department_id, owner_user_id)
                """
            )
            connection.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_chunks_access
                ON chunks(tenant_id, visibility, department_id, document_id)
                """
            )
            connection.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_support_cases_queue
                ON support_cases(tenant_id, assignee_user_id, status, updated_at)
                """
            )
            connection.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_messages_conversation
                ON messages(tenant_id, conversation_id, created_at)
                """
            )
            connection.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_tickets_customer_history
                ON tickets(tenant_id, customer_user_id, created_at)
                """
            )
            try:
                connection.execute(
                    """
                    CREATE VIRTUAL TABLE IF NOT EXISTS chunks_fts
                    USING fts5(chunk_id UNINDEXED, tokens)
                    """
                )
                self.fts_enabled = True
            except sqlite3.OperationalError:
                self.fts_enabled = False
            try:
                connection.execute(
                    """
                    CREATE VIRTUAL TABLE IF NOT EXISTS tickets_fts
                    USING fts5(ticket_id UNINDEXED, tokens)
                    """
                )
                self.ticket_fts_enabled = True
            except sqlite3.OperationalError:
                self.ticket_fts_enabled = False
            connection.commit()
        if self.fts_enabled:
            self._rebuild_fts_if_needed()
        self.seed_demo_data()
        if self.ticket_fts_enabled:
            self._rebuild_ticket_fts_if_needed()

    def _rebuild_fts_if_needed(self) -> None:
        with self._lock, self._connect() as connection:
            chunk_count = connection.execute("SELECT COUNT(*) AS count FROM chunks").fetchone()[
                "count"
            ]
            fts_count = connection.execute(
                "SELECT COUNT(*) AS count FROM chunks_fts"
            ).fetchone()["count"]
            if chunk_count == fts_count:
                return
            connection.execute("DELETE FROM chunks_fts")
            rows = connection.execute("SELECT id, content FROM chunks").fetchall()
            connection.executemany(
                "INSERT INTO chunks_fts(chunk_id, tokens) VALUES (?, ?)",
                [(row["id"], " ".join(tokenize(row["content"]))) for row in rows],
            )
            connection.commit()

    def _rebuild_ticket_fts_if_needed(self) -> None:
        with self._lock, self._connect() as connection:
            ticket_count = connection.execute(
                "SELECT COUNT(*) AS count FROM tickets"
            ).fetchone()["count"]
            fts_count = connection.execute(
                "SELECT COUNT(*) AS count FROM tickets_fts"
            ).fetchone()["count"]
            if ticket_count == fts_count:
                return
            connection.execute("DELETE FROM tickets_fts")
            rows = connection.execute(
                "SELECT id, subject, description, resolution FROM tickets"
            ).fetchall()
            connection.executemany(
                "INSERT INTO tickets_fts(ticket_id, tokens) VALUES (?, ?)",
                [
                    (
                        row["id"],
                        " ".join(
                            tokenize(
                                " ".join(
                                    [
                                        row["subject"],
                                        row["description"],
                                        row["resolution"],
                                    ]
                                )
                            )
                        ),
                    )
                    for row in rows
                ],
            )
            connection.commit()

    def health(self) -> bool:
        try:
            with self._connect() as connection:
                return connection.execute("SELECT 1").fetchone()[0] == 1
        except sqlite3.Error:
            return False

    def seed_demo_data(self) -> None:
        with self._lock, self._connect() as connection:
            connection.execute(
                """
                INSERT INTO customers(
                    id, tenant_id, name, company_name, level, industry, company_size,
                    phone_masked, email_masked, last_contact_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(id, tenant_id) DO UPDATE SET
                    name=excluded.name,
                    company_name=excluded.company_name,
                    level=excluded.level,
                    industry=excluded.industry,
                    company_size=excluded.company_size,
                    phone_masked=excluded.phone_masked,
                    email_masked=excluded.email_masked,
                    last_contact_at=excluded.last_contact_at
                """,
                (
                    "demo-user",
                    "demo-company",
                    "王晨",
                    "云帆科技（北京）有限公司",
                    "标准客户",
                    "互联网 / SaaS",
                    "51-200 人",
                    "138****5678",
                    "wa***@yunfan.example",
                    "2026-07-15T08:42:00+00:00",
                ),
            )
            connection.execute(
                """
                INSERT INTO orders(
                    id, tenant_id, user_id, status, product_name, updated_at,
                    product_version, purchased_at, valid_until, amount_cents,
                    seats_total, seats_used, knowledge_quota_gb, knowledge_used_gb,
                    api_quota, api_used
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    tenant_id=excluded.tenant_id,
                    user_id=excluded.user_id,
                    status=excluded.status,
                    product_name=excluded.product_name,
                    updated_at=excluded.updated_at,
                    product_version=excluded.product_version,
                    purchased_at=excluded.purchased_at,
                    valid_until=excluded.valid_until,
                    amount_cents=excluded.amount_cents,
                    seats_total=excluded.seats_total,
                    seats_used=excluded.seats_used,
                    knowledge_quota_gb=excluded.knowledge_quota_gb,
                    knowledge_used_gb=excluded.knowledge_used_gb,
                    api_quota=excluded.api_quota,
                    api_used=excluded.api_used
                """,
                (
                    "ORD-1001",
                    "demo-company",
                    "demo-user",
                    "已生效",
                    "企业知识库专业版",
                    utc_now(),
                    "专业版",
                    "2026-06-20",
                    "2027-06-19",
                    3_600_000,
                    10,
                    6,
                    1024,
                    320,
                    100_000,
                    18_460,
                ),
            )
            cases = [
                (
                    "CASE-1001",
                    "demo-company",
                    "demo-user",
                    "王晨",
                    "云帆科技",
                    "ORD-1001",
                    "登录失败与退款政策咨询",
                    "企业知识库专业版登录失败，咨询退款资格",
                    "high",
                    "open",
                    "在线客服",
                    "agent-chenyu",
                    "2026-07-18T02:24:00+00:00",
                    "2026-07-17T02:24:00+00:00",
                ),
                (
                    "CASE-1002",
                    "demo-company",
                    "customer-li",
                    "李想",
                    "星河制造",
                    "ORD-0998",
                    "知识库容量扩容",
                    "团队文档增长较快，希望扩容知识库",
                    "medium",
                    "open",
                    "邮件",
                    "agent-chenyu",
                    "2026-07-18T01:55:00+00:00",
                    "2026-07-17T01:55:00+00:00",
                ),
                (
                    "CASE-1003",
                    "demo-company",
                    "customer-zhao",
                    "赵敏",
                    "智联咨询",
                    "ORD-0995",
                    "SSE 接入配置报错",
                    "生产环境接收流式事件时连接中断",
                    "medium",
                    "open",
                    "在线客服",
                    "agent-chenyu",
                    "2026-07-18T01:17:00+00:00",
                    "2026-07-17T01:17:00+00:00",
                ),
                (
                    "CASE-1004",
                    "demo-company",
                    "customer-wu",
                    "吴涛",
                    "启明教育",
                    "ORD-0992",
                    "子账号权限不足",
                    "新建子账号无法访问部门知识库",
                    "low",
                    "waiting",
                    "企业微信",
                    "agent-chenyu",
                    "2026-07-18T00:46:00+00:00",
                    "2026-07-17T00:46:00+00:00",
                ),
                (
                    "CASE-1005",
                    "demo-company",
                    "customer-sun",
                    "孙悦",
                    "北辰医疗",
                    "ORD-0989",
                    "API 调用限流说明",
                    "请求了解当前套餐的 API 限流策略",
                    "low",
                    "open",
                    "邮件",
                    "agent-chenyu",
                    "2026-07-17T22:33:00+00:00",
                    "2026-07-16T09:33:00+00:00",
                ),
            ]
            connection.executemany(
                """
                INSERT INTO support_cases(
                    id, tenant_id, customer_user_id, customer_name, company_name,
                    order_id, subject, preview, priority, status, channel,
                    assignee_user_id, sla_due_at, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    subject=excluded.subject,
                    preview=excluded.preview,
                    priority=excluded.priority,
                    status=CASE
                        WHEN support_cases.status='escalated' THEN support_cases.status
                        ELSE excluded.status
                    END,
                    assignee_user_id=excluded.assignee_user_id,
                    sla_due_at=excluded.sla_due_at,
                    updated_at=excluded.updated_at
                """,
                cases,
            )
            connection.executemany(
                """
                INSERT OR IGNORE INTO tickets(
                    id, tenant_id, user_id, subject, description, status,
                    idempotency_key, created_at, customer_user_id, case_id, order_id,
                    handoff_summary, resolution
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    (
                        "TKT-HIST-1001",
                        "demo-company",
                        "agent-chenyu",
                        "企业知识库专业版登录失败",
                        "客户在 SSO 登录后回到登录页，已确认订单和服务期正常。",
                        "resolved",
                        "demo-history-login-1001",
                        "2026-05-21T09:30:00+00:00",
                        "demo-user",
                        "CASE-HIST-1001",
                        "ORD-1001",
                        "历史工单：已收集浏览器、SSO 回调和账号状态信息。",
                        "技术支持发现 SSO 回调地址与生产域名不一致；修正配置并清理浏览器 Cookie 后恢复登录。",
                    ),
                    (
                        "TKT-HIST-1002",
                        "demo-company",
                        "agent-chenyu",
                        "退款资格与专业版服务期咨询",
                        "客户咨询企业知识库专业版退款资格和剩余服务期。",
                        "closed",
                        "demo-history-refund-1002",
                        "2026-03-14T08:10:00+00:00",
                        "demo-user",
                        "CASE-HIST-1002",
                        "ORD-1001",
                        "历史工单：已核对合同、生效日期和退款政策。",
                        "已向客户说明退款政策与合同约定，并由客户确认保留当前专业版订阅。",
                    ),
                ],
            )
            connection.commit()

    def create_document(
        self,
        *,
        tenant_id: str,
        department_id: str,
        owner_user_id: str,
        title: str,
        source: str,
        visibility: str,
        version: int,
        content_hash: str,
    ) -> str:
        document_id = str(uuid.uuid4())
        with self._lock, self._connect() as connection:
            if self.fts_enabled:
                connection.execute(
                    """
                    DELETE FROM chunks_fts
                    WHERE chunk_id IN (
                        SELECT chunks.id FROM chunks
                        JOIN documents ON documents.id=chunks.document_id
                        WHERE documents.tenant_id=?
                          AND documents.source=?
                          AND documents.version=?
                          AND documents.status='failed'
                    )
                    """,
                    (tenant_id, source, version),
                )
            connection.execute(
                """
                DELETE FROM documents
                WHERE tenant_id=? AND source=? AND version=? AND status='failed'
                """,
                (tenant_id, source, version),
            )
            cursor = connection.execute(
                """
                INSERT OR IGNORE INTO documents
                    (id, tenant_id, department_id, owner_user_id, title, source, visibility,
                     version, content_hash, status, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'processing', ?)
                """,
                (
                    document_id,
                    tenant_id,
                    department_id,
                    owner_user_id,
                    title,
                    source,
                    visibility,
                    version,
                    content_hash,
                    utc_now(),
                ),
            )
            if cursor.rowcount == 0:
                raise DuplicateDocumentError(
                    "Document content or source version is already being processed"
                )
            connection.commit()
        return document_id

    def document_exists(self, tenant_id: str, content_hash: str, version: int) -> bool:
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT 1 FROM documents
                WHERE tenant_id=? AND content_hash=? AND version=?
                  AND status IN ('processing', 'ready')
                LIMIT 1
                """,
                (tenant_id, content_hash, version),
            ).fetchone()
        return row is not None

    def get_ready_chunks_by_hash(
        self, tenant_id: str, content_hash: str, version: int
    ) -> list[Chunk]:
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT chunks.* FROM chunks
                JOIN documents ON documents.id=chunks.document_id
                WHERE documents.tenant_id=?
                  AND documents.content_hash=?
                  AND documents.version=?
                  AND documents.status='ready'
                ORDER BY chunks.position
                """,
                (tenant_id, content_hash, version),
            ).fetchall()
        return [self._row_to_chunk(row) for row in rows]

    def list_ready_chunks(self) -> list[Chunk]:
        """Return chunks used to rebuild a non-persistent vector store."""
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT chunks.* FROM chunks
                JOIN documents ON documents.id=chunks.document_id
                WHERE documents.status='ready'
                ORDER BY chunks.tenant_id, chunks.document_id, chunks.position
                """
            ).fetchall()
        return [self._row_to_chunk(row) for row in rows]

    def save_chunks(self, chunks: list[Chunk]) -> None:
        rows = [
            (
                chunk.id,
                chunk.tenant_id,
                chunk.department_id,
                chunk.document_id,
                chunk.document_version,
                chunk.visibility,
                chunk.content,
                chunk.source,
                chunk.title,
                chunk.position,
                json.dumps(chunk.metadata, ensure_ascii=False),
            )
            for chunk in chunks
        ]
        with self._lock, self._connect() as connection:
            connection.executemany(
                """
                INSERT INTO chunks
                    (id, tenant_id, department_id, document_id, document_version,
                     visibility, content, source, title, position, metadata_json)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                rows,
            )
            if self.fts_enabled:
                connection.executemany(
                    "INSERT INTO chunks_fts(chunk_id, tokens) VALUES (?, ?)",
                    [
                        (chunk.id, " ".join(tokenize(chunk.content)))
                        for chunk in chunks
                    ],
                )
            if chunks:
                connection.execute(
                    "UPDATE documents SET status='ready' WHERE id=?", (chunks[0].document_id,)
                )
            connection.commit()

    def mark_document_failed(self, document_id: str) -> None:
        with self._lock, self._connect() as connection:
            connection.execute("UPDATE documents SET status='failed' WHERE id=?", (document_id,))
            connection.commit()

    def get_chunk(self, chunk_id: str) -> Chunk | None:
        with self._connect() as connection:
            row = connection.execute("SELECT * FROM chunks WHERE id=?", (chunk_id,)).fetchone()
        return self._row_to_chunk(row) if row else None

    def list_accessible_chunks(self, principal: Principal) -> list[Chunk]:
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT chunks.* FROM chunks
                JOIN documents ON documents.id=chunks.document_id
                WHERE chunks.tenant_id=?
                  AND documents.status='ready'
                  AND (
                    chunks.visibility='public'
                    OR (chunks.visibility='department' AND chunks.department_id=?)
                    OR (
                        chunks.visibility='private'
                        AND json_extract(chunks.metadata_json, '$.owner_user_id')=?
                    )
                  )
                """,
                (principal.tenant_id, principal.department_id, principal.user_id),
            ).fetchall()
        return [self._row_to_chunk(row) for row in rows]

    def filter_accessible_chunk_ids(
        self, principal: Principal, chunk_ids: list[str]
    ) -> set[str]:
        if not chunk_ids:
            return set()
        placeholders = ",".join("?" for _ in chunk_ids)
        with self._connect() as connection:
            rows = connection.execute(
                f"""
                SELECT chunks.id FROM chunks
                JOIN documents ON documents.id=chunks.document_id
                WHERE chunks.id IN ({placeholders})
                  AND chunks.tenant_id=?
                  AND documents.status='ready'
                  AND (
                    chunks.visibility='public'
                    OR (chunks.visibility='department' AND chunks.department_id=?)
                    OR (
                        chunks.visibility='private'
                        AND json_extract(chunks.metadata_json, '$.owner_user_id')=?
                    )
                  )
                """,
                (*chunk_ids, principal.tenant_id, principal.department_id, principal.user_id),
            ).fetchall()
        return {row["id"] for row in rows}

    def search_sparse(
        self, query: str, principal: Principal, limit: int
    ) -> list[SearchHit] | None:
        if not self.fts_enabled:
            return None
        fts_query = to_fts_query(query)
        if not fts_query:
            return []
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT chunks.*, bm25(chunks_fts) AS fts_rank
                FROM chunks_fts
                JOIN chunks ON chunks.id=chunks_fts.chunk_id
                JOIN documents ON documents.id=chunks.document_id
                WHERE chunks_fts MATCH ?
                  AND chunks.tenant_id=?
                  AND documents.status='ready'
                  AND (
                    chunks.visibility='public'
                    OR (chunks.visibility='department' AND chunks.department_id=?)
                    OR (
                        chunks.visibility='private'
                        AND json_extract(chunks.metadata_json, '$.owner_user_id')=?
                    )
                  )
                ORDER BY fts_rank ASC
                LIMIT ?
                """,
                (
                    fts_query,
                    principal.tenant_id,
                    principal.department_id,
                    principal.user_id,
                    limit,
                ),
            ).fetchall()
        return [
            SearchHit(
                chunk=self._row_to_chunk(row),
                score=max(0.0, -float(row["fts_rank"])),
                sparse_rank=rank,
            )
            for rank, row in enumerate(rows, start=1)
        ]

    def list_documents(self, principal: Principal) -> list[dict[str, Any]]:
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT id, title, source, visibility, version, status, created_at
                FROM documents
                WHERE tenant_id=?
                  AND (
                    visibility='public'
                    OR (visibility='department' AND department_id=?)
                    OR (visibility='private' AND owner_user_id=?)
                  )
                ORDER BY created_at DESC
                """,
                (principal.tenant_id, principal.department_id, principal.user_id),
            ).fetchall()
        return [dict(row) for row in rows]

    def mark_document_deleting(
        self, principal: Principal, document_id: str
    ) -> list[str] | None:
        with self._lock, self._connect() as connection:
            is_admin = "admin" in principal.roles or "knowledge_admin" in principal.roles
            row = connection.execute(
                """
                SELECT id FROM documents
                WHERE id=? AND tenant_id=? AND (owner_user_id=? OR ?)
                """,
                (document_id, principal.tenant_id, principal.user_id, is_admin),
            ).fetchone()
            if not row:
                return None
            ids = [
                item["id"]
                for item in connection.execute(
                    "SELECT id FROM chunks WHERE document_id=?", (document_id,)
                ).fetchall()
            ]
            connection.execute(
                "UPDATE documents SET status='deleting' WHERE id=?", (document_id,)
            )
            connection.commit()
        return ids

    def finalize_document_delete(self, principal: Principal, document_id: str) -> bool:
        with self._lock, self._connect() as connection:
            is_admin = "admin" in principal.roles or "knowledge_admin" in principal.roles
            chunk_ids = [
                row["id"]
                for row in connection.execute(
                    "SELECT id FROM chunks WHERE document_id=?", (document_id,)
                ).fetchall()
            ]
            cursor = connection.execute(
                """
                DELETE FROM documents
                WHERE id=? AND tenant_id=? AND status='deleting'
                  AND (owner_user_id=? OR ?)
                """,
                (document_id, principal.tenant_id, principal.user_id, is_admin),
            )
            if cursor.rowcount and self.fts_enabled and chunk_ids:
                placeholders = ",".join("?" for _ in chunk_ids)
                connection.execute(
                    f"DELETE FROM chunks_fts WHERE chunk_id IN ({placeholders})", chunk_ids
                )
            connection.commit()
        return bool(cursor.rowcount)

    def query_order(self, principal: Principal, order_id: str) -> dict[str, Any] | None:
        with self._connect() as connection:
            if self._is_tenant_admin(principal):
                row = connection.execute(
                    "SELECT * FROM orders WHERE id=? AND tenant_id=?",
                    (order_id, principal.tenant_id),
                ).fetchone()
            elif self._is_support_agent(principal):
                row = connection.execute(
                    """
                    SELECT orders.* FROM orders
                    WHERE orders.id=? AND orders.tenant_id=?
                      AND EXISTS (
                        SELECT 1 FROM support_cases
                        WHERE support_cases.tenant_id=orders.tenant_id
                          AND support_cases.order_id=orders.id
                          AND support_cases.assignee_user_id=?
                      )
                    """,
                    (order_id, principal.tenant_id, principal.user_id),
                ).fetchone()
            else:
                row = connection.execute(
                    "SELECT * FROM orders WHERE id=? AND tenant_id=? AND user_id=?",
                    (order_id, principal.tenant_id, principal.user_id),
                ).fetchone()
        return dict(row) if row else None

    def list_support_cases(self, principal: Principal) -> list[dict[str, Any]]:
        if not self._is_support_agent(principal):
            return []
        with self._connect() as connection:
            if self._is_tenant_admin(principal):
                rows = connection.execute(
                    """
                    SELECT * FROM support_cases
                    WHERE tenant_id=?
                    ORDER BY
                        CASE priority WHEN 'high' THEN 0 WHEN 'medium' THEN 1 ELSE 2 END,
                        updated_at DESC
                    """,
                    (principal.tenant_id,),
                ).fetchall()
            else:
                rows = connection.execute(
                    """
                    SELECT * FROM support_cases
                    WHERE tenant_id=? AND assignee_user_id=?
                    ORDER BY
                        CASE priority WHEN 'high' THEN 0 WHEN 'medium' THEN 1 ELSE 2 END,
                        updated_at DESC
                    """,
                    (principal.tenant_id, principal.user_id),
                ).fetchall()
        return [dict(row) for row in rows]

    def get_support_case(
        self, principal: Principal, case_id: str
    ) -> dict[str, Any] | None:
        if not self._is_support_agent(principal):
            return None
        with self._connect() as connection:
            if self._is_tenant_admin(principal):
                case_row = connection.execute(
                    "SELECT * FROM support_cases WHERE id=? AND tenant_id=?",
                    (case_id, principal.tenant_id),
                ).fetchone()
            else:
                case_row = connection.execute(
                    """
                    SELECT * FROM support_cases
                    WHERE id=? AND tenant_id=? AND assignee_user_id=?
                    """,
                    (case_id, principal.tenant_id, principal.user_id),
                ).fetchone()
            if not case_row:
                return None
            customer_row = connection.execute(
                """
                SELECT * FROM customers
                WHERE id=? AND tenant_id=?
                """,
                (case_row["customer_user_id"], principal.tenant_id),
            ).fetchone()
            order_row = (
                connection.execute(
                    "SELECT * FROM orders WHERE id=? AND tenant_id=?",
                    (case_row["order_id"], principal.tenant_id),
                ).fetchone()
                if case_row["order_id"]
                else None
            )
        return {
            "case": dict(case_row),
            "customer": dict(customer_row) if customer_row else None,
            "order": dict(order_row) if order_row else None,
        }

    def mark_support_case_escalated(
        self,
        principal: Principal,
        case_id: str,
        ticket_id: str,
    ) -> bool:
        if not case_id or not self._is_support_agent(principal):
            return False
        with self._lock, self._connect() as connection:
            if self._is_tenant_admin(principal):
                cursor = connection.execute(
                    """
                    UPDATE support_cases
                    SET status='escalated', ticket_id=?, updated_at=?
                    WHERE id=? AND tenant_id=?
                    """,
                    (ticket_id, utc_now(), case_id, principal.tenant_id),
                )
            else:
                cursor = connection.execute(
                    """
                    UPDATE support_cases
                    SET status='escalated', ticket_id=?, updated_at=?
                    WHERE id=? AND tenant_id=? AND assignee_user_id=?
                    """,
                    (
                        ticket_id,
                        utc_now(),
                        case_id,
                        principal.tenant_id,
                        principal.user_id,
                    ),
                )
            connection.commit()
        return bool(cursor.rowcount)

    def set_pending_action(self, conversation_id: str, principal: Principal, action: dict[str, Any]) -> None:
        now = utc_now()
        with self._lock, self._connect() as connection:
            cursor = connection.execute(
                """
                INSERT INTO conversations(id, tenant_id, user_id, pending_action_json, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET pending_action_json=excluded.pending_action_json,
                                              updated_at=excluded.updated_at
                WHERE conversations.tenant_id=excluded.tenant_id
                  AND conversations.user_id=excluded.user_id
                """,
                (conversation_id, principal.tenant_id, principal.user_id, json.dumps(action, ensure_ascii=False), now, now),
            )
            if cursor.rowcount == 0:
                raise PermissionError("conversation_id belongs to another principal")
            connection.commit()

    def get_pending_action(
        self, conversation_id: str, principal: Principal
    ) -> dict[str, Any] | None:
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT pending_action_json FROM conversations
                WHERE id=? AND tenant_id=? AND user_id=?
                """,
                (conversation_id, principal.tenant_id, principal.user_id),
            ).fetchone()
            if not row or not row["pending_action_json"]:
                return None
        return json.loads(row["pending_action_json"])

    def clear_pending_action(
        self,
        conversation_id: str,
        principal: Principal,
        action_id: str,
    ) -> bool:
        with self._lock, self._connect() as connection:
            cursor = connection.execute(
                """
                UPDATE conversations
                SET pending_action_json=NULL, updated_at=?
                WHERE id=? AND tenant_id=? AND user_id=?
                  AND json_extract(pending_action_json, '$.action_id')=?
                """,
                (
                    utc_now(),
                    conversation_id,
                    principal.tenant_id,
                    principal.user_id,
                    action_id,
                ),
            )
            connection.commit()
        return bool(cursor.rowcount)

    def create_ticket(
        self,
        principal: Principal,
        subject: str,
        description: str,
        idempotency_key: str,
        *,
        customer_user_id: str = "",
        case_id: str = "",
        order_id: str = "",
        handoff_summary: str = "",
        resolution: str = "",
    ) -> dict[str, Any]:
        with self._lock, self._connect() as connection:
            ticket_id = f"TKT-{uuid.uuid4().hex[:8].upper()}"
            cursor = connection.execute(
                """
                INSERT OR IGNORE INTO tickets(
                    id, tenant_id, user_id, subject, description, status,
                    idempotency_key, created_at, customer_user_id, case_id, order_id,
                    handoff_summary, resolution
                )
                VALUES (?, ?, ?, ?, ?, 'open', ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    ticket_id,
                    principal.tenant_id,
                    principal.user_id,
                    subject,
                    description,
                    idempotency_key,
                    utc_now(),
                    customer_user_id,
                    case_id,
                    order_id,
                    handoff_summary,
                    resolution,
                ),
            )
            if cursor.rowcount and self.ticket_fts_enabled:
                connection.execute(
                    "DELETE FROM tickets_fts WHERE ticket_id=?",
                    (ticket_id,),
                )
                connection.execute(
                    "INSERT INTO tickets_fts(ticket_id, tokens) VALUES (?, ?)",
                    (
                        ticket_id,
                        self._ticket_tokens(subject, description, resolution),
                    ),
                )
            connection.commit()
            row = connection.execute(
                "SELECT * FROM tickets WHERE tenant_id=? AND idempotency_key=?",
                (principal.tenant_id, idempotency_key),
            ).fetchone()
        return dict(row)

    def list_customer_ticket_history(
        self,
        principal: Principal,
        case_id: str,
        limit: int = 5,
    ) -> list[dict[str, Any]]:
        case_context = self.get_support_case(principal, case_id)
        if case_context is None:
            return []
        customer_user_id = str(case_context["case"]["customer_user_id"])
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT id, subject, description, status, resolution, case_id, order_id,
                       created_at
                FROM tickets
                WHERE tenant_id=? AND customer_user_id=?
                  AND (case_id<>? OR case_id='')
                ORDER BY created_at DESC
                LIMIT ?
                """,
                (
                    principal.tenant_id,
                    customer_user_id,
                    case_id,
                    max(1, limit),
                ),
            ).fetchall()
        return [dict(row) for row in rows]

    def search_similar_customer_tickets(
        self,
        principal: Principal,
        case_id: str,
        query: str,
        limit: int = 3,
    ) -> list[dict[str, Any]]:
        case_context = self.get_support_case(principal, case_id)
        if case_context is None:
            return []
        customer_user_id = str(case_context["case"]["customer_user_id"])
        fts_query = to_fts_query(query)
        if not fts_query:
            return []
        if not self.ticket_fts_enabled:
            return self._search_customer_ticket_history_fallback(
                principal,
                customer_user_id,
                case_id,
                query,
                limit,
            )
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT tickets.id, tickets.subject, tickets.description, tickets.status,
                       tickets.resolution, tickets.case_id, tickets.order_id,
                       tickets.created_at, bm25(tickets_fts) AS fts_rank
                FROM tickets_fts
                JOIN tickets ON tickets.id=tickets_fts.ticket_id
                WHERE tickets_fts MATCH ?
                  AND tickets.tenant_id=? AND tickets.customer_user_id=?
                  AND (tickets.case_id<>? OR tickets.case_id='')
                ORDER BY
                    CASE tickets.status
                        WHEN 'resolved' THEN 0
                        WHEN 'closed' THEN 1
                        ELSE 2
                    END,
                    fts_rank ASC
                LIMIT ?
                """,
                (
                    fts_query,
                    principal.tenant_id,
                    customer_user_id,
                    case_id,
                    max(1, limit),
                ),
            ).fetchall()
        results: list[dict[str, Any]] = []
        for row in rows:
            ticket = dict(row)
            rank = float(ticket.pop("fts_rank"))
            ticket["similarity_score"] = round(max(0.0, -rank), 6)
            results.append(ticket)
        return results

    def _search_customer_ticket_history_fallback(
        self,
        principal: Principal,
        customer_user_id: str,
        case_id: str,
        query: str,
        limit: int,
    ) -> list[dict[str, Any]]:
        query_tokens = set(tokenize(query))
        if not query_tokens:
            return []
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT id, subject, description, status, resolution, case_id, order_id,
                       created_at
                FROM tickets
                WHERE tenant_id=? AND customer_user_id=?
                  AND (case_id<>? OR case_id='')
                """,
                (principal.tenant_id, customer_user_id, case_id),
            ).fetchall()
        scored: list[dict[str, Any]] = []
        for row in rows:
            ticket = dict(row)
            ticket_tokens = set(
                tokenize(
                    " ".join(
                        [ticket["subject"], ticket["description"], ticket["resolution"]]
                    )
                )
            )
            overlap = len(query_tokens.intersection(ticket_tokens))
            if overlap:
                ticket["similarity_score"] = round(overlap / len(query_tokens), 6)
                scored.append(ticket)
        scored.sort(
            key=lambda ticket: (
                ticket["status"] not in {"resolved", "closed"},
                -float(ticket["similarity_score"]),
                ticket["created_at"],
            )
        )
        return scored[: max(1, limit)]

    @staticmethod
    def _ticket_tokens(subject: str, description: str, resolution: str) -> str:
        return " ".join(tokenize(" ".join([subject, description, resolution])))

    def get_ops_summary(self, principal: Principal) -> dict[str, Any]:
        with self._connect() as connection:
            metrics = {
                "documents": connection.execute(
                    "SELECT COUNT(*) FROM documents WHERE tenant_id=? AND status='ready'",
                    (principal.tenant_id,),
                ).fetchone()[0],
                "chunks": connection.execute(
                    "SELECT COUNT(*) FROM chunks WHERE tenant_id=?",
                    (principal.tenant_id,),
                ).fetchone()[0],
                "open_cases": connection.execute(
                    """
                    SELECT COUNT(*) FROM support_cases
                    WHERE tenant_id=? AND status IN ('open', 'waiting')
                    """,
                    (principal.tenant_id,),
                ).fetchone()[0],
                "open_tickets": connection.execute(
                    "SELECT COUNT(*) FROM tickets WHERE tenant_id=? AND status='open'",
                    (principal.tenant_id,),
                ).fetchone()[0],
                "conversations": connection.execute(
                    "SELECT COUNT(*) FROM conversations WHERE tenant_id=?",
                    (principal.tenant_id,),
                ).fetchone()[0],
                "audit_events": connection.execute(
                    "SELECT COUNT(*) FROM audit_logs WHERE tenant_id=?",
                    (principal.tenant_id,),
                ).fetchone()[0],
            }
            audits = connection.execute(
                """
                SELECT action, resource_type, resource_id, created_at
                FROM audit_logs
                WHERE tenant_id=?
                ORDER BY created_at DESC
                LIMIT 8
                """,
                (principal.tenant_id,),
            ).fetchall()
        return {"metrics": metrics, "recent_audits": [dict(row) for row in audits]}

    def save_message(
        self,
        conversation_id: str,
        principal: Principal,
        role: str,
        content: str,
        metadata: dict[str, Any] | None = None,
        *,
        case_id: str = "",
    ) -> None:
        now = utc_now()
        with self._lock, self._connect() as connection:
            cursor = connection.execute(
                """
                INSERT OR IGNORE INTO conversations(id, tenant_id, user_id, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                (conversation_id, principal.tenant_id, principal.user_id, now, now),
            )
            if cursor.rowcount == 0:
                owner = connection.execute(
                    "SELECT tenant_id, user_id, case_id FROM conversations WHERE id=?",
                    (conversation_id,),
                ).fetchone()
                if (
                    not owner
                    or owner["tenant_id"] != principal.tenant_id
                    or owner["user_id"] != principal.user_id
                ):
                    raise PermissionError("conversation_id belongs to another principal")
                if case_id and owner["case_id"] and owner["case_id"] != case_id:
                    raise PermissionError("conversation_id belongs to another support case")
            if case_id:
                connection.execute(
                    """
                    UPDATE conversations
                    SET case_id=CASE WHEN case_id='' THEN ? ELSE case_id END
                    WHERE id=? AND tenant_id=? AND user_id=?
                    """,
                    (
                        case_id,
                        conversation_id,
                        principal.tenant_id,
                        principal.user_id,
                    ),
                )
            connection.execute(
                """
                INSERT INTO messages(id, conversation_id, tenant_id, role, content,
                                     metadata_json, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    str(uuid.uuid4()),
                    conversation_id,
                    principal.tenant_id,
                    role,
                    content,
                    json.dumps(metadata or {}, ensure_ascii=False),
                    now,
                ),
            )
            connection.execute(
                """
                UPDATE conversations SET updated_at=?
                WHERE id=? AND tenant_id=? AND user_id=?
                """,
                (now, conversation_id, principal.tenant_id, principal.user_id),
            )
            connection.commit()

    def list_conversation_messages(
        self,
        conversation_id: str,
        principal: Principal,
        limit: int | None = None,
    ) -> list[dict[str, Any]]:
        with self._connect() as connection:
            owner = connection.execute(
                "SELECT tenant_id, user_id FROM conversations WHERE id=?",
                (conversation_id,),
            ).fetchone()
            if not owner:
                return []
            if (
                owner["tenant_id"] != principal.tenant_id
                or owner["user_id"] != principal.user_id
            ):
                raise PermissionError("conversation_id belongs to another principal")
            if limit is None:
                rows = connection.execute(
                    """
                    SELECT role, content, metadata_json, created_at
                    FROM messages
                    WHERE conversation_id=? AND tenant_id=?
                    ORDER BY rowid ASC
                    """,
                    (conversation_id, principal.tenant_id),
                ).fetchall()
            else:
                rows = connection.execute(
                    """
                    SELECT role, content, metadata_json, created_at
                    FROM (
                        SELECT rowid AS message_rowid, role, content, metadata_json,
                               created_at
                        FROM messages
                        WHERE conversation_id=? AND tenant_id=?
                        ORDER BY rowid DESC
                        LIMIT ?
                    )
                    ORDER BY message_rowid ASC
                    """,
                    (conversation_id, principal.tenant_id, max(1, limit)),
                ).fetchall()
        return [
            {
                "role": row["role"],
                "content": row["content"],
                "metadata": json.loads(row["metadata_json"]),
                "created_at": row["created_at"],
            }
            for row in rows
        ]

    def get_conversation_memory(
        self,
        conversation_id: str,
        principal: Principal,
        recent_limit: int,
    ) -> dict[str, Any]:
        with self._connect() as connection:
            conversation = connection.execute(
                """
                SELECT tenant_id, user_id, case_id, summary,
                       summarized_message_count, summary_updated_at
                FROM conversations
                WHERE id=?
                """,
                (conversation_id,),
            ).fetchone()
            if not conversation:
                return {
                    "conversation_id": conversation_id,
                    "case_id": "",
                    "summary": "",
                    "recent_messages": [],
                    "message_count": 0,
                    "summarized_message_count": 0,
                    "summary_updated_at": "",
                }
            if (
                conversation["tenant_id"] != principal.tenant_id
                or conversation["user_id"] != principal.user_id
            ):
                raise PermissionError("conversation_id belongs to another principal")
            message_count = connection.execute(
                """
                SELECT COUNT(*) FROM messages
                WHERE conversation_id=? AND tenant_id=?
                """,
                (conversation_id, principal.tenant_id),
            ).fetchone()[0]
        return {
            "conversation_id": conversation_id,
            "case_id": conversation["case_id"],
            "summary": conversation["summary"],
            "recent_messages": self.list_conversation_messages(
                conversation_id,
                principal,
                recent_limit,
            ),
            "message_count": message_count,
            "summarized_message_count": conversation[
                "summarized_message_count"
            ],
            "summary_updated_at": conversation["summary_updated_at"],
        }

    def update_conversation_summary(
        self,
        conversation_id: str,
        principal: Principal,
        summary: str,
        summarized_message_count: int,
    ) -> None:
        now = utc_now()
        with self._lock, self._connect() as connection:
            cursor = connection.execute(
                """
                UPDATE conversations
                SET summary=?, summarized_message_count=?,
                    summary_updated_at=?, updated_at=?
                WHERE id=? AND tenant_id=? AND user_id=?
                """,
                (
                    summary,
                    summarized_message_count,
                    now,
                    now,
                    conversation_id,
                    principal.tenant_id,
                    principal.user_id,
                ),
            )
            if cursor.rowcount == 0:
                raise PermissionError("conversation_id belongs to another principal")
            connection.commit()

    def audit(
        self,
        principal: Principal,
        action: str,
        resource_type: str,
        resource_id: str,
        payload: dict[str, Any] | None = None,
    ) -> None:
        with self._lock, self._connect() as connection:
            connection.execute(
                """
                INSERT INTO audit_logs(id, tenant_id, user_id, action, resource_type,
                                       resource_id, payload_json, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    str(uuid.uuid4()),
                    principal.tenant_id,
                    principal.user_id,
                    action,
                    resource_type,
                    resource_id,
                    json.dumps(payload or {}, ensure_ascii=False),
                    utc_now(),
                ),
            )
            connection.commit()

    @staticmethod
    def _row_to_chunk(row: sqlite3.Row) -> Chunk:
        return Chunk(
            id=row["id"],
            tenant_id=row["tenant_id"],
            department_id=row["department_id"],
            document_id=row["document_id"],
            document_version=row["document_version"],
            visibility=row["visibility"],
            content=row["content"],
            source=row["source"],
            title=row["title"],
            position=row["position"],
            metadata=json.loads(row["metadata_json"]),
        )

    @staticmethod
    def _is_support_agent(principal: Principal) -> bool:
        return bool(
            {"support_agent", "support_manager", "admin"}.intersection(principal.roles)
        )

    @staticmethod
    def _is_tenant_admin(principal: Principal) -> bool:
        return bool({"support_manager", "admin"}.intersection(principal.roles))
