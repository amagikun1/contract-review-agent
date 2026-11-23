"""测试收集前隔离整个应用的数据目录，绝不接触演示数据库。

Windows 受限环境（含 DSH 沙箱）里，``tempfile.TemporaryDirectory`` /
``pytest`` 的 ``tmp_path`` 会以 0o700 创建临时目录，当前进程随后可能无权
在其中再建子目录。这里改为显式 ``os.mkdir(..., 0o777)``，与
``test_repository.py`` / ``test_parser.py`` 的做法一致，避免测试因文件权限
而无法收集。目录在解释器退出时清理，只用临时目录，不读写 ``data/``。
"""
import atexit
import os
import shutil
import tempfile
import uuid
from pathlib import Path

import pytest


def _make_temp_dir(prefix: str) -> Path:
    path = Path(tempfile.gettempdir()) / f"{prefix}-{uuid.uuid4().hex}"
    os.mkdir(path, 0o777)
    return path


_TEST_DATA = _make_temp_dir("contract-tests")
os.environ["DATA_DIR"] = str(_TEST_DATA)
atexit.register(shutil.rmtree, _TEST_DATA, ignore_errors=True)


@pytest.fixture()
def tmp_dir() -> Path:
    """供 test_api / test_workflow 使用的独立临时目录（避开 pytest tmp_path）。"""
    path = _make_temp_dir("contract-case")
    try:
        yield path
    finally:
        shutil.rmtree(path, ignore_errors=True)
