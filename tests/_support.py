"""测试共用的小工具：把 src 挂上路径、造临时库、把打印收起来。

这里用标准库 unittest，不引 pytest——工具本身零依赖，测试也零依赖，
换台机器 clone 下来就能跑，不用先 pip 装任何东西。
"""

from __future__ import annotations

import contextlib
import io
import shutil
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from guicang.demo import build  # noqa: E402


class TempDir:
    """临时目录：用完就删，测试之间不串味。"""

    def __init__(self) -> None:
        self.path = Path(tempfile.mkdtemp(prefix="guicang-test-"))

    def __enter__(self) -> Path:
        return self.path

    def __exit__(self, *exc) -> None:
        shutil.rmtree(self.path, ignore_errors=True)


def demo_library(base: Path, *, clean: bool = True) -> Path:
    """在 base 里造一套示例素材库，返回库根目录。"""
    root = base / "library"
    build(root, clean=clean)
    return root


@contextlib.contextmanager
def quiet():
    """吞掉被测代码的打印，测试输出只剩点和 F。"""
    buffer = io.StringIO()
    with contextlib.redirect_stdout(buffer):
        yield buffer
