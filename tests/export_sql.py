"""Expose literal production SQL to the PostgreSQL/WASM regression tests."""
import ast
import json
from pathlib import Path

root = Path(__file__).resolve().parents[1]
queries = {}
for filename in ("db/db.py", "db/history.py", "tasks.py", "llm/context.py"):
    tree = ast.parse((root / "src" / filename).read_text(encoding="utf-8-sig"))
    for function in tree.body:
        if not isinstance(function, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        statements = []
        for node in ast.walk(function):
            if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                    and node.func.attr == "execute" and node.args
                    and isinstance(node.args[0], ast.Constant)):
                statements.append(node.args[0].value)
        if statements:
            queries[function.name] = statements
print(json.dumps(queries))
