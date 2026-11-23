"""HTTP 入参模型（Pydantic）。

契约（ARCHITECTURE.md）：

* ``ReviewInput``：``decision`` 为 ``confirm`` / ``reject``，``note`` 为字符串；
* ``ApprovalInput``：``comment`` 为字符串。

这里只做入参建模，不改变字段名与语义；额外字段默认忽略，便于前端渐进式
升级，同时保留 FastAPI 的 422 校验能力。
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

Decision = Literal["confirm", "reject"]
RiskLevel = Literal["high", "medium", "low"]
ContractStatus = Literal[
    "uploaded",
    "reviewing",
    "pending_review",
    "confirmed",
    "rejected",
    "approved",
    "failed",
]


class ReviewInput(BaseModel):
    """人工复核：确认通过或驳回。"""

    decision: Decision = Field(description="人工复核结论：confirm=确认，reject=驳回")
    note: str = Field(default="", max_length=10000, description="复核意见，可为空")


class ApprovalInput(BaseModel):
    """模拟审批：在人工确认之后提交审批意见。"""

    comment: str = Field(default="", max_length=10000, description="审批意见，可为空")
