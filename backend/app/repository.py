"""SQLite 持久层。

契约（见 ARCHITECTURE.md）：

* ``Repository(db_path)`` 构造时自动建表；
* 每次调用使用独立的 SQLite 连接（``:memory:`` 例外，见下）；
* ``create_contract`` / ``list_contracts`` / ``get_contract`` / ``update_contract``；
* 固定字段：id, filename, stored_path, created_at, updated_at, status, text,
  pages, analysis, review_note, approval, error；
* ``pages`` / ``analysis`` / ``approval`` 以 JSON 文本存储，读取时还原为
  list / dict；
* ``update_contract`` 只允许更新固定字段中除 ``id``、``created_at`` 之外的字段，
  未知字段或非法类型抛出 ``ValueError``，不存在的 id 抛出 ``KeyError``。

关于 ``:memory:``：SQLite 的内存库在最后一个连接关闭时销毁，而本模块默认
“每次调用一个连接”，因此内存库会丢失数据。为避免这种静默错误，本模块对
``:memory:`` 保留一个进程内连接（并用锁串行化），文件数据库仍严格做到每次
调用独立连接。生产环境请使用文件路径。
"""

from __future__ import annotations

import json
import sqlite3
import threading
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator, Mapping

#: 合同状态枚举，与 ARCHITECTURE.md 保持一致。
STATUSES: frozenset[str] = frozenset(
    {
        "uploaded",
        "reviewing",
        "pending_review",
        "confirmed",
        "rejected",
        "approved",
        "failed",
    }
)

#: 对外返回的固定字段及其顺序。
CONTRACT_FIELDS: tuple[str, ...] = (
    "id",
    "filename",
    "stored_path",
    "created_at",
    "updated_at",
    "status",
    "text",
    "pages",
    "analysis",
    "review_note",
    "approval",
    "error",
)

#: 允许通过 update_contract 修改的字段（除 id、created_at 外的全部固定字段）。
UPDATABLE_FIELDS: frozenset[str] = frozenset(CONTRACT_FIELDS) - {"id", "created_at"}

_JSON_FIELDS = frozenset({"pages", "analysis", "approval"})
_TEXT_FIELDS = frozenset(
    {"filename", "stored_path", "text", "review_note", "error", "status"}
)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS contracts (
    id           TEXT PRIMARY KEY,
    filename     TEXT NOT NULL,
    stored_path  TEXT NOT NULL,
    created_at   TEXT NOT NULL,
    updated_at   TEXT NOT NULL,
    status       TEXT NOT NULL DEFAULT 'uploaded',
    text         TEXT NOT NULL DEFAULT '',
    pages        TEXT NOT NULL DEFAULT '[]',
    analysis     TEXT NOT NULL DEFAULT '{}',
    review_note  TEXT NOT NULL DEFAULT '',
    approval     TEXT NOT NULL DEFAULT '{}',
    error        TEXT NOT NULL DEFAULT ''
)
"""

_INDEXES = (
    "CREATE INDEX IF NOT EXISTS idx_contracts_created_at "
    "ON contracts (created_at DESC)",
    "CREATE INDEX IF NOT EXISTS idx_contracts_status ON contracts (status)",
)


def _now() -> str:
    """UTC ISO-8601 时间戳（带微秒，便于排序与测试）。"""

    return datetime.now(timezone.utc).isoformat()


def _encode_json(field: str, value: Any) -> str:
    expected = list if field == "pages" else dict
    if not isinstance(value, expected):
        type_name = "list" if expected is list else "dict"
        raise ValueError(
            f"字段 {field!r} 必须是 {type_name}，实际是 {type(value).__name__}"
        )
    try:
        return json.dumps(value, ensure_ascii=False, separators=(",", ":"))
    except (TypeError, ValueError) as exc:
        raise ValueError(f"字段 {field!r} 不能序列化为 JSON：{exc}") from exc


def _decode_json(field: str, raw: Any) -> Any:
    if not isinstance(raw, str):
        return [] if field == "pages" else {}
    try:
        value = json.loads(raw)
    except (TypeError, ValueError):
        return [] if field == "pages" else {}
    expected = list if field == "pages" else dict
    if not isinstance(value, expected):
        return [] if field == "pages" else {}
    return value


def _validate_update(field: str, value: Any) -> Any:
    if field in _JSON_FIELDS:
        return _encode_json(field, value)
    if field not in _TEXT_FIELDS:
        raise ValueError(f"不支持的字段：{field!r}")
    if not isinstance(value, str):
        raise ValueError(
            f"字段 {field!r} 必须是 str，实际是 {type(value).__name__}"
        )
    if field == "status" and value not in STATUSES:
        allowed = ", ".join(sorted(STATUSES))
        raise ValueError(f"非法 status：{value!r}，允许值：{allowed}")
    return value


class Repository:
    """基于 SQLite 的合同仓库。"""

    def __init__(self, db_path: str | Path) -> None:
        raw_path = str(db_path)
        if not raw_path.strip():
            raise ValueError("db_path 不能为空")

        self.db_path = raw_path
        self._is_memory = raw_path == ":memory:"
        self._memory_conn: sqlite3.Connection | None = None
        self._memory_lock = threading.RLock()

        if self._is_memory:
            self._memory_conn = sqlite3.connect(
                ":memory:", timeout=30.0, check_same_thread=False
            )
            self._memory_conn.row_factory = sqlite3.Row
            self._configure(self._memory_conn)
            self._init_schema(self._memory_conn)
        else:
            path = Path(raw_path).expanduser()
            self.db_path = str(path)
            if path.parent and not path.parent.exists():
                path.parent.mkdir(parents=True, exist_ok=True)
            # 文件数据库：建表用一个独立连接，之后每次调用各自新建连接。
            with self._connect_file() as conn:
                self._init_schema(conn)

    # ------------------------------------------------------------------ 连接
    def _configure(self, conn: sqlite3.Connection) -> None:
        conn.execute("PRAGMA busy_timeout = 5000")
        conn.execute("PRAGMA foreign_keys = ON")

    def _init_schema(self, conn: sqlite3.Connection) -> None:
        if not self._is_memory:
            try:
                conn.execute("PRAGMA journal_mode = WAL")
            except sqlite3.DatabaseError:
                # 某些网络盘/受限文件系统不支持 WAL，退回默认日志模式即可。
                pass
        conn.execute(_SCHEMA)
        for statement in _INDEXES:
            conn.execute(statement)
        conn.commit()

    @contextmanager
    def _connect_file(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(self.db_path, timeout=30.0)
        conn.row_factory = sqlite3.Row
        self._configure(conn)
        try:
            yield conn
            conn.commit()
        except BaseException:
            conn.rollback()
            raise
        finally:
            conn.close()

    @contextmanager
    def _connection(self) -> Iterator[sqlite3.Connection]:
        """每个调用一个连接；内存库复用唯一连接并串行化。"""

        if self._memory_conn is not None:
            with self._memory_lock:
                conn = self._memory_conn
                try:
                    yield conn
                    conn.commit()
                except BaseException:
                    conn.rollback()
                    raise
            return

        with self._connect_file() as conn:
            yield conn

    # ------------------------------------------------------------------ 序列化
    @staticmethod
    def _row_to_contract(row: sqlite3.Row | Mapping[str, Any]) -> dict[str, Any]:
        data = {field: row[field] for field in CONTRACT_FIELDS}
        for field in _JSON_FIELDS:
            data[field] = _decode_json(field, data.get(field))
        return data

    # ------------------------------------------------------------------ 公开 API
    def create_contract(self, filename: str, stored_path: str) -> dict[str, Any]:
        """新建合同，返回完整合同字典。"""

        if not isinstance(filename, str) or not filename.strip():
            raise ValueError("filename 必须是非空字符串")
        if not isinstance(stored_path, str) or not stored_path.strip():
            raise ValueError("stored_path 必须是非空字符串")

        contract_id = uuid.uuid4().hex
        now = _now()
        with self._connection() as conn:
            conn.execute(
                """
                INSERT INTO contracts (
                    id, filename, stored_path, created_at, updated_at, status,
                    text, pages, analysis, review_note, approval, error
                ) VALUES (?, ?, ?, ?, ?, 'uploaded', '', '[]', '{}', '', '{}', '')
                """,
                (contract_id, filename, stored_path, now, now),
            )
            row = conn.execute(
                "SELECT * FROM contracts WHERE id = ?", (contract_id,)
            ).fetchone()
        if row is None:  # pragma: no cover - 理论上不可能
            raise RuntimeError("合同创建后无法读取")
        return self._row_to_contract(row)

    def list_contracts(self) -> list[dict[str, Any]]:
        """按创建时间倒序返回全部合同。"""

        with self._connection() as conn:
            rows = conn.execute(
                "SELECT * FROM contracts ORDER BY created_at DESC, rowid DESC"
            ).fetchall()
        return [self._row_to_contract(row) for row in rows]

    def get_contract(self, contract_id: str) -> dict[str, Any] | None:
        """按 id 读取合同，不存在返回 None。"""

        if not isinstance(contract_id, str):
            raise ValueError("contract_id 必须是 str")
        with self._connection() as conn:
            row = conn.execute(
                "SELECT * FROM contracts WHERE id = ?", (contract_id,)
            ).fetchone()
        return self._row_to_contract(row) if row is not None else None

    def update_contract(self, contract_id: str, **fields: Any) -> dict[str, Any]:
        """更新合同的可变字段并返回最新合同。

        * 不存在的 id 抛 ``KeyError``；
        * ``id``、``created_at`` 以及未知字段抛 ``ValueError``；
        * ``updated_at`` 由服务端统一刷新（传入值会被忽略）。
        """

        if not isinstance(contract_id, str):
            raise ValueError("contract_id 必须是 str")

        changes: dict[str, Any] = {}
        for field, value in fields.items():
            if field in {"id", "created_at"}:
                raise ValueError(f"字段 {field!r} 不允许更新")
            if field == "updated_at":
                # 由服务端统一维护，忽略调用方传入的值。
                continue
            if field not in UPDATABLE_FIELDS:
                raise ValueError(f"不支持的字段：{field!r}")
            changes[field] = _validate_update(field, value)

        now = _now()
        assignments = [f"{field} = ?" for field in changes]
        assignments.append("updated_at = ?")
        params: list[Any] = [changes[field] for field in changes]
        params.append(now)
        params.append(contract_id)

        with self._connection() as conn:
            cursor = conn.execute(
                f"UPDATE contracts SET {', '.join(assignments)} WHERE id = ?",
                params,
            )
            if cursor.rowcount == 0:
                raise KeyError(f"合同不存在：{contract_id}")
            row = conn.execute(
                "SELECT * FROM contracts WHERE id = ?", (contract_id,)
            ).fetchone()
        if row is None:  # pragma: no cover - 理论上不可能
            raise KeyError(f"合同不存在：{contract_id}")
        return self._row_to_contract(row)
