"""为初次使用者建立独立的本地示例清单。"""

import json
import os
from contextlib import closing
from pathlib import Path
from tempfile import TemporaryDirectory
from uuid import uuid4

from table_store import LocalTable


_READINGS = Path(__file__).resolve().parent / "assets" / "demo_readings.json"


def prepare_demo(directory):
    """首次在指定目录发布完整的演示库，后续启动沿用同一文件。"""
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    target = directory / "records.sqlite3"
    if target.is_file():
        return directory

    readings = json.loads(_READINGS.read_text(encoding="utf-8"))
    with TemporaryDirectory(dir=directory, prefix=".demo-") as staging:
        staged = Path(staging) / target.name
        with closing(LocalTable(staged)) as table:
            for fields in readings:
                table.upsert(fields["论文链接"], fields, uuid4().hex, expected_record_id=None)
        try:
            # 两种操作在目标已存在时都会失败，多个进程可以同时首次启动。
            if os.name == "nt":
                staged.rename(target)
            else:
                os.link(staged, target)
        except FileExistsError:
            if not target.is_file():
                raise
    return directory
