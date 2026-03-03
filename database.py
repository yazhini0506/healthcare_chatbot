"""
database.py – SQLite Database Initialisation & Helpers
Manages all persistent storage: knowledge base, conversations, leads.
"""

import sqlite3
import os
import logging

logger = logging.getLogger(__name__)

DB_PATH = os.path.join(os.path.dirname(__file__), "healthcare_agent.db")


def get_db_connection() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    """Create all tables if they do not already exist."""
    conn = get_db_connection()
    cur = conn.cursor()

    # ── Knowledge Base ──────────────────────────────────────────────────────────
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS knowledge_base (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            title       TEXT NOT NULL,
            content     TEXT NOT NULL,
            category    TEXT,
            source_url  TEXT,
            scraped_at  TEXT,
            UNIQUE(title, source_url)
        )
        """
    )

    # ── Conversations ───────────────────────────────────────────────────────────
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS conversations (
            id            TEXT PRIMARY KEY,
            started_at    TEXT NOT NULL,
            last_active   TEXT,
            intent_tags   TEXT,
            lead_status   TEXT DEFAULT 'none',
            summary       TEXT
        )
        """
    )

    # ── Messages ────────────────────────────────────────────────────────────────
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS messages (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            conversation_id TEXT NOT NULL,
            role            TEXT NOT NULL,
            content         TEXT NOT NULL,
            timestamp       TEXT NOT NULL,
            FOREIGN KEY (conversation_id) REFERENCES conversations(id)
        )
        """
    )

    # ── Leads ───────────────────────────────────────────────────────────────────
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS leads (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            conversation_id TEXT NOT NULL,
            company_name    TEXT,
            contact_name    TEXT,
            designation     TEXT,
            territory       TEXT,
            product_interest TEXT,
            expected_volume TEXT,
            email           TEXT,
            phone           TEXT,
            intent_tags     TEXT,
            created_at      TEXT NOT NULL,
            email_sent      INTEGER DEFAULT 0,
            FOREIGN KEY (conversation_id) REFERENCES conversations(id)
        )
        """
    )

    # ── Products ────────────────────────────────────────────────────────────────
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS products (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            product_name    TEXT NOT NULL,
            category        TEXT NOT NULL,
            description     TEXT,
            manufacturer    TEXT,
            source_url      TEXT,
            scraped_at      TEXT,
            UNIQUE(product_name, category)
        )
        """
    )

    conn.commit()
    conn.close()
    logger.info("Database initialised successfully.")


# ─── Conversation helpers ────────────────────────────────────────────────────

def create_conversation(conv_id: str):
    from datetime import datetime
    conn = get_db_connection()
    conn.execute(
        "INSERT OR IGNORE INTO conversations (id, started_at) VALUES (?, ?)",
        (conv_id, datetime.utcnow().isoformat()),
    )
    conn.commit()
    conn.close()


def save_message(conv_id: str, role: str, content: str):
    from datetime import datetime
    conn = get_db_connection()
    conn.execute(
        "INSERT INTO messages (conversation_id, role, content, timestamp) VALUES (?, ?, ?, ?)",
        (conv_id, role, content, datetime.utcnow().isoformat()),
    )
    conn.execute(
        "UPDATE conversations SET last_active = ? WHERE id = ?",
        (datetime.utcnow().isoformat(), conv_id),
    )
    conn.commit()
    conn.close()


def get_conversation_history(conv_id: str, limit: int = 20) -> list[dict]:
    conn = get_db_connection()
    rows = conn.execute(
        "SELECT role, content FROM messages WHERE conversation_id = ? ORDER BY id DESC LIMIT ?",
        (conv_id, limit),
    ).fetchall()
    conn.close()
    return [dict(r) for r in reversed(rows)]


def update_intent_tags(conv_id: str, tags: list[str]):
    conn = get_db_connection()
    conn.execute(
        "UPDATE conversations SET intent_tags = ? WHERE id = ?",
        (",".join(tags), conv_id),
    )
    conn.commit()
    conn.close()


def get_knowledge_base(query_terms: list[str] | None = None, limit: int = 8) -> list[dict]:
    """Simple keyword search over the knowledge base."""
    conn = get_db_connection()
    if query_terms:
        conditions = " OR ".join(
            ["title LIKE ? OR content LIKE ? OR category LIKE ?"] * len(query_terms)
        )
        params = []
        for term in query_terms:
            like = f"%{term}%"
            params.extend([like, like, like])
        rows = conn.execute(
            f"SELECT title, content, category, source_url FROM knowledge_base WHERE {conditions} LIMIT ?",
            params + [limit],
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT title, content, category, source_url FROM knowledge_base LIMIT ?",
            (limit,),
        ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def save_lead(conv_id: str, lead_data: dict) -> int:
    from datetime import datetime
    conn = get_db_connection()
    cur = conn.execute(
        """
        INSERT INTO leads
            (conversation_id, company_name, contact_name, designation, territory,
             product_interest, expected_volume, email, phone, intent_tags, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            conv_id,
            lead_data.get("company_name"),
            lead_data.get("contact_name"),
            lead_data.get("designation"),
            lead_data.get("territory"),
            lead_data.get("product_interest"),
            lead_data.get("expected_volume"),
            lead_data.get("email"),
            lead_data.get("phone"),
            lead_data.get("intent_tags"),
            datetime.utcnow().isoformat(),
        ),
    )
    lead_id = cur.lastrowid
    conn.execute(
        "UPDATE conversations SET lead_status = 'qualified' WHERE id = ?", (conv_id,)
    )
    conn.commit()
    conn.close()
    return lead_id


def mark_email_sent(lead_id: int):
    conn = get_db_connection()
    conn.execute("UPDATE leads SET email_sent = 1 WHERE id = ?", (lead_id,))
    conn.commit()
    conn.close()


def get_all_leads() -> list[dict]:
    conn = get_db_connection()
    rows = conn.execute("SELECT * FROM leads ORDER BY created_at DESC").fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_all_conversations() -> list[dict]:
    conn = get_db_connection()
    rows = conn.execute(
        "SELECT c.*, COUNT(m.id) as message_count "
        "FROM conversations c LEFT JOIN messages m ON c.id = m.conversation_id "
        "GROUP BY c.id ORDER BY c.last_active DESC"
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_conversation_messages(conv_id: str) -> list[dict]:
    conn = get_db_connection()
    rows = conn.execute(
        "SELECT role, content, timestamp FROM messages WHERE conversation_id = ? ORDER BY id",
        (conv_id,),
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_kb_stats() -> dict:
    conn = get_db_connection()
    total = conn.execute("SELECT COUNT(*) as c FROM knowledge_base").fetchone()["c"]
    cats = conn.execute(
        "SELECT category, COUNT(*) as cnt FROM knowledge_base GROUP BY category"
    ).fetchall()
    conn.close()
    return {"total": total, "categories": [dict(r) for r in cats]}


# ─── Product helpers ─────────────────────────────────────────────────────────

def save_product(product: dict) -> bool:
    """Insert or ignore a single product record. Returns True if newly inserted."""
    from datetime import datetime
    conn = get_db_connection()
    cur = conn.execute(
        """
        INSERT OR IGNORE INTO products
            (product_name, category, description, manufacturer, source_url, scraped_at)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (
            product.get("product_name", "").strip(),
            product.get("category", "General").strip(),
            product.get("description", "").strip(),
            product.get("manufacturer", "").strip(),
            product.get("source_url", ""),
            datetime.utcnow().isoformat(),
        ),
    )
    inserted = cur.rowcount > 0
    conn.commit()
    conn.close()
    return inserted


def get_all_products(limit: int = 200) -> list[dict]:
    conn = get_db_connection()
    rows = conn.execute(
        "SELECT * FROM products ORDER BY category, product_name LIMIT ?", (limit,)
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_products_by_category(category: str) -> list[dict]:
    conn = get_db_connection()
    rows = conn.execute(
        "SELECT * FROM products WHERE category LIKE ? ORDER BY product_name",
        (f"%{category}%",),
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def search_products(query: str, limit: int = 10) -> list[dict]:
    """
    Full-text search over product_name, category, description.
    Strategy:
      1. Try full query as LIKE %query%
      2. If no results, try each significant word individually and union results
    """
    conn = get_db_connection()
    like = f"%{query}%"
    rows = conn.execute(
        """
        SELECT * FROM products
        WHERE product_name LIKE ? OR category LIKE ? OR description LIKE ?
        ORDER BY
            CASE WHEN product_name LIKE ? THEN 0 ELSE 1 END,
            product_name
        LIMIT ?
        """,
        (like, like, like, like, limit),
    ).fetchall()

    if not rows:
        # Word-by-word fallback — search each significant word
        stop = {"the","a","an","and","or","of","in","for","to","with","is","are","was","were"}
        words = [w.strip("(),") for w in query.lower().split()
                 if len(w.strip("(),")) >= 4 and w.strip("(),") not in stop]
        seen_ids = set()
        results  = []
        for word in words[:6]:
            w_like = f"%{word}%"
            word_rows = conn.execute(
                """
                SELECT * FROM products
                WHERE product_name LIKE ? OR description LIKE ?
                ORDER BY product_name LIMIT ?
                """,
                (w_like, w_like, limit),
            ).fetchall()
            for r in word_rows:
                if r["id"] not in seen_ids:
                    seen_ids.add(r["id"])
                    results.append(r)
            if len(results) >= limit:
                break
        conn.close()
        return [dict(r) for r in results[:limit]]

    conn.close()
    return [dict(r) for r in rows]



def get_product_stats() -> dict:
    conn = get_db_connection()
    total = conn.execute("SELECT COUNT(*) as c FROM products").fetchone()["c"]
    cats = conn.execute(
        "SELECT category, COUNT(*) as cnt FROM products GROUP BY category ORDER BY cnt DESC"
    ).fetchall()
    conn.close()
    return {"total": total, "categories": [dict(r) for r in cats]}
