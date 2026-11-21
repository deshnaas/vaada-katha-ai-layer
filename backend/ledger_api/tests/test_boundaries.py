"""Architecture and safety boundaries for the ledger integration (static checks over the source + log hygiene)."""
import ast
import logging
import re
from pathlib import Path

import pytest

from fake_backend import FakeBackend
from rig import Rig

BACKEND = Path(__file__).resolve().parents[2]
PACKAGES = ["intent", "commands", "dialogue", "ai", "ledger_api"]
NETWORK_MODULES = {"urllib", "urllib3", "http", "socket", "requests", "httpx", "aiohttp", "ssl", "ftplib", "smtplib"}
DB_MODULES = {"sqlite3", "sqlalchemy", "psycopg2", "psycopg", "pymysql", "mysql", "peewee", "pymongo", "databases",
              "aiosqlite", "tortoise", "prisma"}


def sources(*packages, tests=False):
    for pkg in packages:
        for path in (BACKEND / pkg).rglob("*.py"):
            if "tests" in path.parts and not tests:
                continue
            if "__pycache__" in path.parts:
                continue
            yield path


def imports(path):
    tree = ast.parse(path.read_text(encoding="utf-8"))
    found = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            found |= {a.name.split(".")[0] for a in node.names}
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            found.add(node.module.split(".")[0])
    return found


def test_no_package_touches_a_database():
    offenders = {p.name: imports(p) & DB_MODULES for p in sources(*PACKAGES, tests=True) if imports(p) & DB_MODULES}
    assert not offenders


def test_voice_core_never_touches_the_network():
    for p in sources("intent", "commands", "dialogue", "ai"):
        if p.parts[-2] == "ai":
            continue                                                       # the optional local-LLM client has its own tests
        assert not (imports(p) & NETWORK_MODULES), p


def test_dialogue_does_not_import_the_api_layer():
    for p in sources("dialogue"):
        assert "ledger_api" not in imports(p), p
        text = p.read_text(encoding="utf-8")
        assert "urllib" not in text and "http://" not in text and "https://" not in text, p


def test_only_the_client_module_opens_connections():
    for p in sources("ledger_api"):
        hit = imports(p) & NETWORK_MODULES
        if p.name == "client.py":
            assert hit <= {"urllib", "socket"}, hit            # socket only for its timeout exception type
        else:                                              # (urllib.parse for URL validation is fine; opening is not)
            assert not (hit - {"urllib"}), (p.name, hit)
            assert "urllib.request" not in p.read_text(encoding="utf-8"), p.name


def test_the_deprecated_commit_endpoint_is_never_used():
    for p in sources(*PACKAGES):
        assert "contract/commit" not in p.read_text(encoding="utf-8"), p


def test_the_client_only_calls_the_documented_endpoints():
    text = (BACKEND / "ledger_api" / "client.py").read_text(encoding="utf-8")
    used = {re.sub(r"\{(cid|tid)\}", "{id}", u) for u in re.findall(r'"((?:/ai)?/[a-z_/{}]+)"', text)}
    allowed = {"/ai/contract/propose", "/ai/proposals/{id}/confirm", "/ai/proposals/{id}/cancel", "/health",
               "/customers/lookup", "/customers", "/customers/{id}", "/customers/{id}/balance",
               "/customers/{id}/transactions", "/transactions/{id}/reverse"}
    assert used and used <= allowed, used - allowed
    assert not any("commit" in x for x in used)


def test_no_secret_shaped_strings_in_the_new_code():
    patterns = [r"sk-[A-Za-z0-9]{16,}", r"AKIA[0-9A-Z]{12,}", r"(?i)api[_-]?key\s*=\s*['\"][A-Za-z0-9_\-]{16,}['\"]",
                r"(?i)bearer\s+[A-Za-z0-9._\-]{20,}", r"(?i)password\s*=\s*['\"][^'\"]+['\"]"]
    for p in list(sources("ledger_api", "dialogue", tests=True)):
        text = p.read_text(encoding="utf-8")
        for pat in patterns:
            assert not re.search(pat, text), (p.name, pat)


def test_the_config_has_no_baked_in_credentials():
    from ledger_api import LedgerAPIConfig
    cfg = LedgerAPIConfig()
    for field in vars(cfg):
        assert "key" not in field.lower() and "token" not in field.lower() and "password" not in field.lower() \
            or getattr(cfg, field) in (None, "", False)
    assert cfg.base_url.startswith(("http://127.0.0.1", "http://localhost"))     # safe local default, overridable by env


def test_env_example_documents_the_variables_without_values_that_look_real():
    path = BACKEND / ".env.example"
    text = path.read_text(encoding="utf-8")
    for line in text.splitlines():
        if line.startswith("VAADA_LEDGER_API_"):
            value = line.split("=", 1)[1].strip()
            assert not re.search(r"[A-Za-z0-9_\-]{24,}", value), line


# ====================================================================== log hygiene
@pytest.fixture
def log_text(caplog):
    caplog.set_level(logging.DEBUG)
    return caplog


def test_logs_never_contain_names_amounts_or_proposal_ids(log_text):
    rig = Rig()
    pid = None
    r = rig.say("Suresh ka 345 udhaar likho")
    pid = r.pending.proposal_id
    rig.backend.inject("confirm", "unavailable", times=3)
    rig.say("haan")
    rig.say("haan")
    rig.say("Suresh ka balance kitna hai")
    rig.backend.inject("propose", "timeout")
    rig.say("Lakshmi ne 777 diye")
    blob = "\n".join(rec.getMessage() for rec in log_text.records)
    for needle in ("Suresh", "Lakshmi", "345", "777", "34500", "77700", pid or "prop_"):
        assert needle not in blob, needle


def test_error_objects_do_not_carry_request_bodies():
    rig = Rig()
    rig.backend.inject("propose", "status:422")
    from ledger_api import LedgerAPIError
    try:
        rig.client.propose("add_credit", "Suresh", 34500)
    except LedgerAPIError as exc:
        assert "Suresh" not in repr(exc) and "34500" not in repr(exc)
