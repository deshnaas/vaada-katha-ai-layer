"""TEST-ONLY authentication shim around Asmita's REAL backend app. This is NOT her implementation and must never be deployed.

Why it exists: at feature/ledger-backend @ 94f6100 the backend's database layer is multi-merchant (users, sessions, user_id on
every table) but its HTTP layer never sets the merchant, so every merchant-scoped route answers HTTP 500 (see
test_canary_* in test_real_backend_integration.py). To exercise the REAL database isolation through REAL HTTP, this shim
overrides her `get_db` dependency: it resolves `Authorization: Bearer <token>` through HER `sessions` table (token_hash =
SHA-256 hex of the token; the hashing scheme is this shim's assumption, not her contract) and binds `conn.current_user_id`
exactly the way her own tests do. Everything else (routes, validation, ledger, isolation queries) is her code, unmodified.

Run by the test fixture:  uvicorn auth_shim_app:app --app-dir <this dir>   with PYTHONPATH=<her repo>, DATABASE_PATH=<temp db>,
SHIM_SEED_FILE=<json>. The database is always a throw-away file holding synthetic merchants.
"""
import hashlib
import json
import os

from fastapi import HTTPException, Request

from backend.database import get_db, get_db_connection, init_db
from backend.main import app


def _sha(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _seed() -> None:
    init_db()
    seed = json.load(open(os.environ["SHIM_SEED_FILE"], encoding="utf-8"))
    conn = get_db_connection()
    try:
        for m in seed["merchants"]:
            conn.execute("INSERT OR IGNORE INTO users (phone, name, shop_name, status) VALUES (?, ?, ?, ?)",
                         (m["phone"], m["name"], m["shop"], m.get("status", "ACTIVE")))
            uid = conn.execute("SELECT user_id FROM users WHERE phone = ?", (m["phone"],)).fetchone()["user_id"]
            expires = "datetime('now','localtime','-1 day')" if m.get("expired") else "datetime('now','localtime','+1 day')"
            revoked = "datetime('now','localtime')" if m.get("revoked") else "NULL"
            conn.execute(
                f"INSERT OR IGNORE INTO sessions (session_id, user_id, token_hash, auth_method, expires_at, revoked_at) "
                f"VALUES (?, ?, ?, 'PIN', {expires}, {revoked})",
                (f"shim-{m['phone']}", uid, _sha(m["token"])))
    finally:
        conn.close()


def _authenticated_db(request: Request):
    conn = get_db_connection()
    try:
        if request.url.path != "/health":
            parts = request.headers.get("authorization", "").split(" ")
            if len(parts) != 2 or parts[0].lower() != "bearer":
                raise HTTPException(status_code=401, detail="Not authenticated")
            row = conn.execute(
                "SELECT s.user_id FROM sessions s JOIN users u ON u.user_id = s.user_id "
                "WHERE s.token_hash = ? AND s.revoked_at IS NULL AND s.expires_at > datetime('now','localtime')",
                (_sha(parts[1]),)).fetchone()
            if row is None:
                raise HTTPException(status_code=401, detail="Not authenticated")
            status_row = conn.execute("SELECT status FROM users WHERE user_id = ?", (row["user_id"],)).fetchone()
            if status_row["status"] != "ACTIVE":
                raise HTTPException(status_code=403, detail="Merchant account is not active")
            conn.current_user_id = row["user_id"]
        yield conn
    finally:
        conn.close()


app.dependency_overrides[get_db] = _authenticated_db
_seed()
