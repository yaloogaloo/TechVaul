"""Startup entry gates for the project-root launcher."""

from __future__ import annotations

import ast
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MAIN_PY = ROOT / "main.py"
PACKAGE_MAIN = ROOT / "src" / "techvault" / "__main__.py"


def test_main_py_imports_techvault_from_the_project_root() -> None:
    env = os.environ.copy()
    env.pop("PYTHONPATH", None)
    env["QT_QPA_PLATFORM"] = "offscreen"
    script = r"""
import importlib.util
import sys
from pathlib import Path

root = Path(sys.argv[1]).resolve()
src = root / "src"
sys.path = [entry for entry in sys.path if Path(entry).resolve() != src]
spec = importlib.util.spec_from_file_location("project_main", root / "main.py")
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)

import techvault
from techvault.ui.app import main as launch

assert Path(techvault.__file__).resolve() == src / "techvault" / "__init__.py"
assert module.main is launch
assert any(Path(entry).resolve() == src for entry in sys.path)
print("IMPORT_OK")
"""
    completed = subprocess.run(
        [sys.executable, "-c", script, str(ROOT)],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr
    assert "IMPORT_OK" in completed.stdout
    assert "Traceback" not in completed.stderr


def test_main_py_creates_application_and_main_window(tmp_path: Path) -> None:
    env = os.environ.copy()
    env.pop("PYTHONPATH", None)
    env["LOCALAPPDATA"] = str(tmp_path / "appdata")
    env["QT_QPA_PLATFORM"] = "offscreen"
    env["TECHVAULT_SMOKE"] = "1"
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONUTF8"] = "1"
    script = r"""
import importlib.util
import sys
from pathlib import Path

root = Path(sys.argv[1]).resolve()
src = root / "src"
sys.path = [entry for entry in sys.path if Path(entry).resolve() != src]
spec = importlib.util.spec_from_file_location("project_main", root / "main.py")
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)

from PySide6.QtWidgets import QApplication

created = []
real_window = module.main.__globals__["MainWindow"]

class RecordingWindow(real_window):
    def __init__(self, context):
        super().__init__(context)
        created.append(self)

module.main.__globals__["MainWindow"] = RecordingWindow
code = module.main([])
window = created[0]
labels = [window.nav.item(index).text() for index in range(window.nav.count())]
print("LAUNCH_OK")
print(QApplication.instance() is not None)
print(window.windowTitle())
print("|".join(labels))
print(code)
window.context.close()
"""
    completed = subprocess.run(
        [sys.executable, "-c", script, str(ROOT)],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=60,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr
    lines = [line for line in completed.stdout.splitlines() if line]
    assert lines[-5:] == [
        "LAUNCH_OK",
        "True",
        "TechVault",
        "知识|GitHub 项目|设置",
        "0",
    ]
    assert "Traceback" not in completed.stderr


def test_root_main_py_process_exits_cleanly(tmp_path: Path) -> None:
    env = os.environ.copy()
    env.pop("PYTHONPATH", None)
    env["LOCALAPPDATA"] = str(tmp_path / "appdata")
    env["QT_QPA_PLATFORM"] = "offscreen"
    env["TECHVAULT_SMOKE"] = "1"
    completed = subprocess.run(
        [sys.executable, str(MAIN_PY)],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr
    assert "Traceback" not in completed.stdout
    assert "Traceback" not in completed.stderr


def test_startup_entries_call_the_same_launch_function() -> None:
    main_tree = ast.parse(MAIN_PY.read_text(encoding="utf-8"))
    package_tree = ast.parse(PACKAGE_MAIN.read_text(encoding="utf-8"))
    pyproject = (ROOT / "pyproject.toml").read_text(encoding="utf-8")

    assert _imported_launch(main_tree) == ("techvault.ui.app", "main")
    assert _imported_launch(package_tree) == ("techvault.ui.app", "main")
    assert _calls_name(main_tree, "main")
    assert _calls_name(package_tree, "main")
    assert 'techvault = "techvault.ui.app:main"' in pyproject
    assert "QApplication(" not in MAIN_PY.read_text(encoding="utf-8")
    assert "QApplication(" not in PACKAGE_MAIN.read_text(encoding="utf-8")


def _imported_launch(tree: ast.AST) -> tuple[str, str] | None:
    for node in ast.walk(tree):
        if not isinstance(node, ast.ImportFrom) or node.module is None:
            continue
        for alias in node.names:
            if alias.name == "main":
                return node.module, alias.asname or alias.name
    return None


def _calls_name(tree: ast.AST, name: str) -> bool:
    return any(
        isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == name
        for node in ast.walk(tree)
    )
