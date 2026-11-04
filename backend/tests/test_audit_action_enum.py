import ast
import re
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
APP_ROOT = REPO_ROOT / "backend" / "app"
MIGRATIONS_ROOT = REPO_ROOT / "infra" / "supabase" / "migrations"

# These actions predate the repository's action_type migration history. Keep
# the list closed so a new undeclared action always fails this check.
LEGACY_UNDECLARED_ACTIONS = {
    "alcada_config_updated",
    "component_toggle",
    "eligibility_parameter_updated",
    "escalation_approved",
    "escalation_rejected",
    "operation_approved",
    "operation_escalated",
    "operation_rejected",
    "override_requested",
    "override_reviewed",
    "pricing_matrix_updated",
    "pricing_parameter_updated",
    "relatorio_solicitado",
}


def _declared_action_types() -> set[str]:
    initial = (MIGRATIONS_ROOT / "001_initial_schema.sql").read_text(
        encoding="utf-8"
    )
    match = re.search(
        r"CREATE\s+TYPE\s+action_type\s+AS\s+ENUM\s*\((.*?)\);",
        initial,
        flags=re.IGNORECASE | re.DOTALL,
    )
    assert match, "action_type enum not found in 001_initial_schema.sql"
    declared = set(re.findall(r"'([^']+)'", match.group(1)))

    alter_pattern = re.compile(
        r"ALTER\s+TYPE\s+action_type\s+ADD\s+VALUE\s+IF\s+NOT\s+EXISTS\s+'([^']+)'",
        flags=re.IGNORECASE,
    )
    for migration in MIGRATIONS_ROOT.glob("*.sql"):
        declared.update(alter_pattern.findall(migration.read_text(encoding="utf-8")))
    return declared


def _audit_actions_in_code() -> dict[str, list[str]]:
    usages: dict[str, list[str]] = {}
    for source_path in APP_ROOT.rglob("*.py"):
        source = source_path.read_text(encoding="utf-8")
        tree = ast.parse(source, filename=str(source_path))
        literal_fields: dict[tuple[str, str], set[str]] = {}
        for class_node in (node for node in ast.walk(tree) if isinstance(node, ast.ClassDef)):
            for field in class_node.body:
                if not (
                    isinstance(field, ast.AnnAssign)
                    and isinstance(field.target, ast.Name)
                    and isinstance(field.annotation, ast.Subscript)
                    and isinstance(field.annotation.value, ast.Name)
                    and field.annotation.value.id == "Literal"
                ):
                    continue
                values = (
                    field.annotation.slice.elts
                    if isinstance(field.annotation.slice, ast.Tuple)
                    else [field.annotation.slice]
                )
                literal_fields[(class_node.name, field.target.id)] = {
                    value.value
                    for value in values
                    if isinstance(value, ast.Constant) and isinstance(value.value, str)
                }
        audit_names = {
            target.id
            for node in ast.walk(tree)
            if isinstance(node, (ast.Assign, ast.AnnAssign))
            for target in (
                node.targets if isinstance(node, ast.Assign) else [node.target]
            )
            if isinstance(target, ast.Name)
            and isinstance(node.value, ast.Call)
            and isinstance(node.value.func, ast.Name)
            and node.value.func.id == "AuditService"
        }
        for node in ast.walk(tree):
            if not (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr == "log"
                and isinstance(node.func.value, ast.Name)
                and node.func.value.id in audit_names
            ):
                continue
            action_node = next(
                (keyword.value for keyword in node.keywords if keyword.arg == "action"),
                node.args[1] if len(node.args) > 1 else None,
            )
            action_values: set[str] = set()
            if isinstance(action_node, ast.Constant) and isinstance(action_node.value, str):
                action_values.add(action_node.value)
            elif (
                isinstance(action_node, ast.Attribute)
                and isinstance(action_node.value, ast.Name)
            ):
                enclosing = min(
                    (
                        function
                        for function in ast.walk(tree)
                        if isinstance(function, (ast.FunctionDef, ast.AsyncFunctionDef))
                        and function.lineno <= node.lineno <= function.end_lineno
                    ),
                    key=lambda function: function.end_lineno - function.lineno,
                    default=None,
                )
                if enclosing:
                    parameter = next(
                        (
                            arg
                            for arg in enclosing.args.args
                            if arg.arg == action_node.value.id
                        ),
                        None,
                    )
                    if parameter and isinstance(parameter.annotation, ast.Name):
                        action_values = literal_fields.get(
                            (parameter.annotation.id, action_node.attr), set()
                        )
            assert action_values, (
                f"dynamic audit action at "
                f"{source_path.relative_to(REPO_ROOT)}:{node.lineno}"
            )
            location = f"{source_path.relative_to(REPO_ROOT)}:{node.lineno}"
            for action in action_values:
                usages.setdefault(action, []).append(location)
    return usages


def test_audit_log_actions_are_declared_in_action_type_migrations():
    declared = _declared_action_types()
    usages = _audit_actions_in_code()
    undeclared = set(usages) - declared - LEGACY_UNDECLARED_ACTIONS

    details = ", ".join(
        f"{action} ({', '.join(usages[action])})" for action in sorted(undeclared)
    )
    assert not undeclared, f"audit.log actions missing from action_type: {details}"
