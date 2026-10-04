"""Static contract between literal PostgREST queries and production schema."""
from __future__ import annotations

import ast
import json
from pathlib import Path


ROOT = Path(__file__).parents[1] / "app"
SCHEMA = {
    table: set(columns)
    for table, columns in json.loads(
        (Path(__file__).parent / "fixtures" / "schema_producao.json").read_text(encoding="utf-8")
    ).items()
}
FILTERS = {"eq", "neq", "in_", "order", "gt", "lt"}
WRITES = {"insert", "update", "upsert"}
DYNAMIC_COLUMNS_IGNORED = 0


def _table_name(node: ast.AST) -> str | None:
    if isinstance(node, ast.Call):
        if isinstance(node.func, ast.Attribute) and node.func.attr == "table":
            if node.args and isinstance(node.args[0], ast.Constant) and isinstance(node.args[0].value, str):
                return node.args[0].value
            return None
        return _table_name(node.func.value) if isinstance(node.func, ast.Attribute) else None
    if isinstance(node, ast.Attribute):
        return _table_name(node.value)
    return None


def _select_columns(value: str) -> list[str]:
    columns = []
    for column in value.split(","):
        column = column.strip()
        if not column or column == "*" or "(" in column:
            continue
        columns.append(column.split(":", 1)[0].split("::", 1)[0].strip())
    return columns


def _literal_string(node: ast.AST) -> str | None:
    return node.value if isinstance(node, ast.Constant) and isinstance(node.value, str) else None


def _issues() -> tuple[list[str], int]:
    issues: list[str] = []
    dynamic = 0
    for path in ROOT.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
                continue
            method = node.func.attr
            if method not in FILTERS | WRITES | {"select"}:
                continue
            table = _table_name(node.func.value)
            if table not in SCHEMA:
                continue
            columns: list[str] = []
            if method == "select":
                for arg in node.args:
                    value = _literal_string(arg)
                    if value is None:
                        dynamic += 1
                    else:
                        columns.extend(_select_columns(value))
            elif method in FILTERS:
                if node.args:
                    value = _literal_string(node.args[0])
                    if value is None:
                        dynamic += 1
                    else:
                        columns.append(value.split("::", 1)[0])
            elif method in WRITES and node.args:
                payload = node.args[0]
                if isinstance(payload, ast.Dict):
                    for key in payload.keys:
                        value = _literal_string(key) if key is not None else None
                        if value is None:
                            dynamic += 1
                        else:
                            columns.append(value)
                else:
                    dynamic += 1
            for column in columns:
                if column and column not in SCHEMA[table]:
                    issues.append(f"{path}:{node.lineno} usa coluna inexistente {table}.{column}")
    return sorted(set(issues)), dynamic


def test_literal_postgrest_columns_match_production_schema():
    global DYNAMIC_COLUMNS_IGNORED
    issues, DYNAMIC_COLUMNS_IGNORED = _issues()
    assert not issues, "\n".join(issues)
