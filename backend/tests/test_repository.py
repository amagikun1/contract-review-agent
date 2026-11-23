"""Repository 单元测试。

测试只使用临时目录，不接触真实数据目录，也不依赖第三方服务。
"""

from __future__ import annotations

import json
import os
import shutil
import sqlite3
import sys
import tempfile
import time
import uuid
from pathlib import Path

import pytest
from pydantic import ValidationError

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backend.app.models import ApprovalInput, ReviewInput  # noqa: E402
from backend.app.repository import (  # noqa: E402
    CONTRACT_FIELDS,
    STATUSES,
    Repository,
)


@pytest.mark.parametrize("decision", ["confirm", "reject"])
def test_review_input_accepts_documented_decisions(decision: str) -> None:
    payload = ReviewInput(decision=decision, note="请补充验收条款")

    assert payload.decision == decision
    assert payload.note == "请补充验收条款"


def test_review_input_rejects_unknown_decision() -> None:
    with pytest.raises(ValidationError):
        ReviewInput(decision="approve", note="")  # type: ignore[arg-type]


def test_approval_input_allows_empty_comment() -> None:
    assert ApprovalInput().comment == ""
    assert ApprovalInput(comment="同意").comment == "同意"


@pytest.fixture()
def workdir() -> Path:
    """独立临时目录。

    不使用 pytest 的 ``tmp_path``：部分 Windows 沙箱会把按 0o700 创建的目录
    对当前进程也设为不可读，导致测试收集阶段失败。这里显式使用 0o777（普通
    系统上由 umask 收敛）并在用例结束后清理。
    """

    path = Path(tempfile.gettempdir()) / f"contract-repo-{uuid.uuid4().hex}"
    os.mkdir(path, 0o777)
    try:
        yield path
    finally:
        shutil.rmtree(path, ignore_errors=True)


@pytest.fixture()
def repo(workdir: Path) -> Repository:
    return Repository(workdir / "contracts.db")


def _columns(db_path: Path) -> set[str]:
    with sqlite3.connect(db_path) as conn:
        rows = conn.execute("PRAGMA table_info(contracts)").fetchall()
    return {row[1] for row in rows}


def test_constructor_creates_parent_directory_and_table(workdir: Path) -> None:
    db_path = workdir / "nested" / "data" / "contracts.db"
    Repository(db_path)

    assert db_path.is_file()
    assert set(CONTRACT_FIELDS) <= _columns(db_path)


def test_create_contract_returns_defaults(repo: Repository) -> None:
    contract = repo.create_contract("采购合同.pdf", "data/uploads/a.pdf")

    assert set(contract) == set(CONTRACT_FIELDS)
    assert isinstance(contract["id"], str) and len(contract["id"]) == 32
    assert contract["filename"] == "采购合同.pdf"
    assert contract["stored_path"] == "data/uploads/a.pdf"
    assert contract["status"] == "uploaded"
    assert contract["text"] == ""
    assert contract["pages"] == []
    assert contract["analysis"] == {}
    assert contract["review_note"] == ""
    assert contract["approval"] == {}
    assert contract["error"] == ""
    assert contract["created_at"] == contract["updated_at"]
    assert contract["created_at"].endswith("+00:00")


def test_get_contract_returns_none_for_missing(repo: Repository) -> None:
    assert repo.get_contract("not-exists") is None


def test_create_and_get_are_persisted_across_instances(workdir: Path) -> None:
    db_path = workdir / "contracts.db"
    first = Repository(db_path)
    created = first.create_contract("a.txt", str(workdir / "a.txt"))

    second = Repository(db_path)
    loaded = second.get_contract(created["id"])

    assert loaded is not None
    assert loaded["id"] == created["id"]
    assert loaded["filename"] == "a.txt"


def test_list_contracts_newest_first(repo: Repository) -> None:
    older = repo.create_contract("older.txt", "/tmp/older.txt")
    time.sleep(0.005)
    newer = repo.create_contract("newer.txt", "/tmp/newer.txt")

    items = repo.list_contracts()

    assert [item["id"] for item in items] == [newer["id"], older["id"]]


def test_json_fields_round_trip(repo: Repository) -> None:
    created = repo.create_contract("合同.docx", "/tmp/合同.docx")
    pages = [{"page": 1, "text": "第一页正文"}, {"page": 2, "text": "第二页正文"}]
    analysis = {
        "mode": "offline",
        "risks": [
            {
                "id": "r1",
                "level": "high",
                "quote": "预付款100%",
                "rule_ids": ["R-001"],
            }
        ],
    }
    approval = {"comment": "同意", "operator": "审批人A"}

    updated = repo.update_contract(
        created["id"],
        pages=pages,
        analysis=analysis,
        approval=approval,
        text="第一页正文\n\n第二页正文",
        status="pending_review",
    )

    assert updated["pages"] == pages
    assert updated["analysis"] == analysis
    assert updated["approval"] == approval
    assert updated["status"] == "pending_review"

    loaded = repo.get_contract(created["id"])
    assert loaded is not None
    assert loaded["pages"] == pages
    assert loaded["analysis"] == analysis
    assert loaded["approval"] == approval


def test_json_columns_are_stored_as_utf8_text(repo: Repository, workdir: Path) -> None:
    contract = repo.create_contract("x.txt", "x.txt")
    repo.update_contract(contract["id"], pages=[{"page": 1, "text": "中文正文"}])

    with sqlite3.connect(workdir / "contracts.db") as conn:
        raw_pages, raw_analysis = conn.execute(
            "SELECT pages, analysis FROM contracts WHERE id = ?", (contract["id"],)
        ).fetchone()

    assert "中文正文" in raw_pages
    assert json.loads(raw_pages) == [{"page": 1, "text": "中文正文"}]
    assert json.loads(raw_analysis) == {}


def test_update_returns_new_values_and_refreshes_updated_at(repo: Repository) -> None:
    created = repo.create_contract("a.txt", "a.txt")
    time.sleep(0.005)

    updated = repo.update_contract(
        created["id"], text="正文", review_note="请补充验收条款", status="confirmed"
    )

    assert updated["text"] == "正文"
    assert updated["review_note"] == "请补充验收条款"
    assert updated["status"] == "confirmed"
    assert updated["created_at"] == created["created_at"]
    assert updated["updated_at"] != created["updated_at"]


def test_update_missing_contract_raises_key_error(repo: Repository) -> None:
    with pytest.raises(KeyError):
        repo.update_contract("missing", status="reviewing")


@pytest.mark.parametrize("field", ["id", "created_at"])
def test_update_forbids_identity_fields(repo: Repository, field: str) -> None:
    created = repo.create_contract("a.txt", "a.txt")

    with pytest.raises(ValueError, match="不允许更新"):
        repo.update_contract(created["id"], **{field: "x"})


def test_update_rejects_unknown_field(repo: Repository) -> None:
    created = repo.create_contract("a.txt", "a.txt")

    with pytest.raises(ValueError, match="不支持"):
        repo.update_contract(created["id"], unknown="x")


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("status", "analyzing"),
        ("pages", "not-a-list"),
        ("analysis", []),
        ("approval", "not-a-dict"),
        ("text", 123),
        ("review_note", None),
    ],
)
def test_update_rejects_invalid_values(
    repo: Repository, field: str, value: object
) -> None:
    created = repo.create_contract("a.txt", "a.txt")

    with pytest.raises(ValueError):
        repo.update_contract(created["id"], **{field: value})


def test_update_accepts_all_documented_statuses(repo: Repository) -> None:
    created = repo.create_contract("a.txt", "a.txt")

    for status in sorted(STATUSES):
        updated = repo.update_contract(created["id"], status=status)
        assert updated["status"] == status


def test_failed_update_keeps_previous_values(repo: Repository) -> None:
    created = repo.create_contract("a.txt", "a.txt")
    repo.update_contract(created["id"], text="原文", status="reviewing")

    with pytest.raises(ValueError):
        repo.update_contract(created["id"], text=123, status="confirmed")

    loaded = repo.get_contract(created["id"])
    assert loaded is not None
    assert loaded["text"] == "原文"
    assert loaded["status"] == "reviewing"


def test_memory_repository_keeps_data_between_calls() -> None:
    repo = Repository(":memory:")
    created = repo.create_contract("m.txt", "m.txt")

    loaded = repo.get_contract(created["id"])
    assert loaded is not None
    assert [item["id"] for item in repo.list_contracts()] == [created["id"]]


def test_empty_arguments_are_rejected(repo: Repository) -> None:
    with pytest.raises(ValueError):
        repo.create_contract("", "a.txt")
    with pytest.raises(ValueError):
        repo.create_contract("a.txt", "")
    with pytest.raises(ValueError):
        Repository("")
