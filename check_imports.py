"""Verify syntax and key imports across CRUD poller files."""
import ast
import sys

errors = []
files = [
    "src/connectors/graph_client.py",
    "src/connectors/delta_sync.py",
    "src/connectors/change_handlers.py",
    "src/connectors/permissions.py",
    "src/connectors/webhooks.py",
    "src/api/routes/webhooks.py",
    "src/celery_app.py",
    "src/cli.py",
    "tests/unit/test_webhooks.py",
    "tests/unit/test_crud_poller.py",
    "tests/unit/test_graph_client.py",
]

for path in files:
    try:
        with open(path, encoding="utf-8") as f:
            src = f.read()
        ast.parse(src)
        print(f"OK   {path}")
    except SyntaxError as e:
        errors.append(f"SYNTAX {path}:{e.lineno}: {e.msg}")
        print(f"FAIL {path}: SyntaxError line {e.lineno}: {e.msg}")
    except FileNotFoundError:
        errors.append(f"MISSING {path}")
        print(f"MISS {path}: file not found")
    except Exception as e:
        errors.append(f"ERR {path}: {e}")
        print(f"ERR  {path}: {e}")

print()

# Deep import check for graph_client (no network deps needed)
try:
    # Add project root to path
    sys.path.insert(0, ".")
    # Mock heavy deps before importing
    import unittest.mock as mock
    import types

    # Stub out modules that would need real creds/infra
    for mod in ["msal", "httpx", "structlog"]:
        if mod not in sys.modules:
            stub = types.ModuleType(mod)
            if mod == "structlog":
                stub.get_logger = lambda: mock.MagicMock()
            if mod == "msal":
                stub.ConfidentialClientApplication = mock.MagicMock
            if mod == "httpx":
                stub.AsyncClient = mock.MagicMock
                stub.Headers = dict
                stub.ConnectError = ConnectionError
                stub.ReadTimeout = TimeoutError
                stub.WriteTimeout = TimeoutError
                stub.Response = mock.MagicMock
            sys.modules[mod] = stub

    # Stub config
    config_mod = types.ModuleType("config")
    config_settings = types.ModuleType("config.settings")
    config_settings.get_settings = mock.MagicMock()
    sys.modules["config"] = config_mod
    sys.modules["config.settings"] = config_settings

    from src.connectors.graph_client import (
        GraphClient,
        GraphClientFactory,
        GraphAPIError,
        GraphTokenExpiredError,
        GraphNotFoundError,
        GraphAuthError,
        _TokenRefresher,
        _extract_error,
    )
    print("OK   graph_client: all expected symbols importable")
except Exception as e:
    errors.append(f"IMPORT graph_client: {e}")
    print(f"FAIL graph_client import: {e}")

# Check that delta_sync references exist in graph_client
print()
print("Checking cross-module symbol references...")

with open("src/connectors/delta_sync.py", encoding="utf-8") as f:
    delta_src = f.read()

for sym in ["GraphNotFoundError", "GraphTokenExpiredError", "GraphClient"]:
    if sym in delta_src:
        print(f"  delta_sync uses {sym}: OK")
    else:
        errors.append(f"delta_sync missing reference to {sym}")
        print(f"  delta_sync uses {sym}: MISSING")

with open("src/connectors/permissions.py", encoding="utf-8") as f:
    perms_src = f.read()

for sym in ["GraphNotFoundError", "get_item_permissions", "get_transitive_members"]:
    if sym in perms_src:
        print(f"  permissions uses {sym}: OK")
    else:
        errors.append(f"permissions missing reference to {sym}")
        print(f"  permissions uses {sym}: MISSING")

with open("src/api/routes/webhooks.py", encoding="utf-8") as f:
    webhooks_src = f.read()

for sym in ["run_delta_sync", "get_settings"]:
    if sym in webhooks_src:
        print(f"  webhooks route uses {sym}: OK")
    else:
        errors.append(f"webhooks route missing {sym}")
        print(f"  webhooks route uses {sym}: MISSING")

# Check patch targets in tests match actual module paths
with open("tests/unit/test_webhooks.py", encoding="utf-8") as f:
    test_wh_src = f.read()

for target in [
    "src.api.routes.webhooks.run_delta_sync",
    "src.api.routes.webhooks.get_settings",
]:
    if target in test_wh_src:
        print(f"  test_webhooks patches {target}: OK")
    else:
        errors.append(f"test_webhooks missing patch target {target}")
        print(f"  test_webhooks patches {target}: MISSING")

with open("tests/unit/test_crud_poller.py", encoding="utf-8") as f:
    test_crud_src = f.read()

for target in [
    "src.connectors.delta_sync",
    "src.connectors.change_handlers",
]:
    if target in test_crud_src:
        print(f"  test_crud_poller references {target}: OK")
    else:
        errors.append(f"test_crud_poller missing reference to {target}")
        print(f"  test_crud_poller references {target}: MISSING")

print()
if errors:
    print(f"{len(errors)} issue(s) found:")
    for e in errors:
        print(f"  - {e}")
    sys.exit(1)
else:
    print("All checks passed.")
