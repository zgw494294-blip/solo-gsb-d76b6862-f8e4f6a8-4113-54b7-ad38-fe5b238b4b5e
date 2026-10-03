"""迁移页面回归的 pytest 包装：调用 Node + jsdom 跑真实 DOM 用例。

页面逻辑（app/static/app.js）是浏览器 JavaScript，Python 测试栈无法直接
驱动；这里以子进程运行 tests/page/test_migration_page.mjs（依赖见
tests/page/package.json，仅测试期需要，不进容器）。环境中没有 Node 时
跳过，不影响其余回归。
"""

import os
import shutil
import subprocess
from pathlib import Path

import pytest

PAGE_DIR = Path(__file__).parent / "page"
SCRIPT = PAGE_DIR / "test_migration_page.mjs"

pytestmark = pytest.mark.skipif(shutil.which("node") is None,
                                reason="环境中没有 Node.js，跳过页面 DOM 回归")


def test_migration_page_regression():
    if not (PAGE_DIR / "node_modules").exists():
        pytest.skip("tests/page/node_modules 不存在（npm --prefix tests/page install）")
    env = dict(os.environ)
    # 确保脚本能从 tests/page/node_modules 解析到 jsdom
    env["NODE_PATH"] = str(PAGE_DIR / "node_modules")
    proc = subprocess.run(
        ["node", str(SCRIPT)], cwd=PAGE_DIR, env=env,
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    assert proc.returncode == 0, (
        "迁移页面回归失败：\n" + proc.stdout)
