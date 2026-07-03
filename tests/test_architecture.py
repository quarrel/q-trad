import ast
from pathlib import Path

import pytest

FORBIDDEN_CORE_DEPENDENCIES = {
    "fastapi",
    "pydantic",
    "pydantic_settings",
    "sqlalchemy",
    "trading_ig",
}

FORBIDDEN_QTRAD_DEPENDENCIES = {
    "domain": {"adapters", "api", "application", "ports", "runtime"},
    "ports": {"adapters", "api", "application", "runtime"},
    "application": {"adapters", "api", "runtime"},
}


def _imports(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module)
    return imported


@pytest.mark.parametrize("path", sorted(Path("src/qtrad/domain").glob("*.py")))
def test_domain_has_no_framework_imports(path: Path) -> None:
    roots = {name.split(".")[0] for name in _imports(path)}
    assert roots.isdisjoint(FORBIDDEN_CORE_DEPENDENCIES)


@pytest.mark.parametrize(
    ("layer", "path"),
    [
        (layer, path)
        for layer in FORBIDDEN_QTRAD_DEPENDENCIES
        for path in sorted(Path(f"src/qtrad/{layer}").glob("*.py"))
    ],
)
def test_core_dependency_direction(layer: str, path: Path) -> None:
    qtrad_layers = {
        parts[1]
        for name in _imports(path)
        if (parts := name.split("."))[:1] == ["qtrad"] and len(parts) > 1
    }
    assert qtrad_layers.isdisjoint(FORBIDDEN_QTRAD_DEPENDENCIES[layer])
