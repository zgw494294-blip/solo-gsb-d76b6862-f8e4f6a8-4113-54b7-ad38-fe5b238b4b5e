"""迁移页前端回归的 pytest 包装：调用 tests/page 下的 Node + jsdom 脚本。

与纯 Python 用例不同，这里需要 Node.js（见 tests/page/package.json）：
环境缺少 node 时跳过（容器 / CI 安装 Node 后自动运行），不影响其余套件。
"""

import shutil
import subprocess
from pathlib import Path

import pytest

PAGE_DIR = Path(__file__).parent / "page"
SCRIPT = PAGE_DIR / "migration_page.test.js"


@pytest.mark.skipif(shutil.which("node") is None, reason="未安装 Node.js，跳过页面回归")
def test_migration_page_type_distinct_ids():
    node_modules = PAGE_DIR / "node_modules"
    if not node_modules.exists():
        pytest.skip("未安装页面回归依赖（cd tests/page && npm install），跳过")
    proc = subprocess.run(
        ["node", str(SCRIPT)], cwd=PAGE_DIR,
        capture_output=True, text=True, timeout=60,
    )
    assert proc.returncode == 0, (
        f"页面回归失败（exit={proc.returncode}）\nstdout:\n{proc.stdout}\nstderr:\n{proc.stderr}")
