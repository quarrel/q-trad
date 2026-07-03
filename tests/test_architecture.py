import ast
from pathlib import Path

import pytest

FORBIDDEN_DOMAIN_ROOTS = {
    "fastapi",
    "sqlalchemy",
    "trading_ig",
    "pydantic",
    "pydantic_settings",
}


@pytest.mark.parametrize("path", sorted(Path("src/qtrad/domain").glob("*.py")))
def test_domain_has_no_framework_imports(path: Path) -> None:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module.split(".")[0])
    assert imported.isdisjoint(FORBIDDEN_DOMAIN_ROOTS)
