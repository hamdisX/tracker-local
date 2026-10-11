
"""API du tracker de candidatures avec authentification."""

import asyncio
import base64
import binascii
import hashlib
import hmac
import logging
import os
import re
import secrets
import time
from contextlib import asynccontextmanager
from pathlib import Path
from urllib.parse import parse_qsl, quote, urlencode, urlsplit

import psycopg
from psycopg.types.json import Jsonb
from psycopg_pool import ConnectionPool
from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.responses import (
    FileResponse,
    JSONResponse,
    PlainTextResponse,
    RedirectResponse,
)
from fastapi.staticfiles import StaticFiles


DATABASE_URL = os.getenv(
    "DATABASE_URL",
    "postgresql://tracker:tracker@localhost:5433/tracker",
)

APP_USERNAME = os.getenv("APP_USERNAME")
APP_PASSWORD = os.getenv("APP_PASSWORD")

ID_RE = re.compile(r"^[A-Za-z0-9_\-.~:@+]{1,200}$")

log = logging.getLogger("uvicorn.error")

TRACKING_RE = re.compile(
    r"^(utm_.*|trk|trackingid|refid|ref|src|from|source|fbclid|gclid|origin|position|pagenum|currentjobid)$",
    re.I,
)


def norm_link(url: str | None) -> str:
    """Normalise un lien d'offre pour détecter les doublons."""
    url = (url or "").strip()

    if not url:
        return ""

    if "://" not in url:
        url = "https://" + url

    try:
        parts = urlsplit(url)
    except ValueError:
        return url.lower().rstrip("/")

    host = (parts.hostname or "").lower().removeprefix("www.")

    keep = sorted(
        (k, v)
        for k, v in parse_qsl(parts.query)
        if not TRACKING_RE.match(k)
    )

    query = "?" + urlencode(keep) if keep else ""

    return host + parts.path.rstrip("/") + query


pool = ConnectionPool(
    DATABASE_URL,
    min_size=1,
    max_size=5,
    open=False,
    # Neon met la base en veille : on vérifie la connexion avant de la réutiliser
    check=ConnectionPool.check_connection,
    max_idle=240,
)


@asynccontextmanager
async def lifespan(_: FastAPI):
    pool.open(wait=True, timeout=30)

    try:
        with pool.connection() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS candidatures (
                    id          TEXT PRIMARY KEY,
                    data        JSONB NOT NULL,
                    updated_at  TIMESTAMPTZ NOT NULL DEFAULT now()
                )
                """
            )

            conn.execute(
                "ALTER TABLE candidatures "
                "ADD COLUMN IF NOT EXISTS ref BIGSERIAL"
            )

            conn.execute(
                "ALTER TABLE candidatures "
                "ADD COLUMN IF NOT EXISTS lien_norm TEXT NOT NULL DEFAULT ''"
            )

            # Bibliothèque de CVs : fichiers stockés en base (le disque Render est éphémère)
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS cvs (
                    id          BIGSERIAL PRIMARY KEY,
                    version     TEXT NOT NULL,
                    lang        TEXT NOT NULL,
                    filename    TEXT NOT NULL,
                    mime        TEXT NOT NULL,
                    size        INTEGER NOT NULL,
                    data        BYTEA NOT NULL,
                    uploaded_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                    UNIQUE (version, lang)
                )
                """
            )

            rows = conn.execute(
                "SELECT id, data FROM candidatures WHERE lien_norm = ''"
            ).fetchall()

            for doc_id, data in rows:
                conn.execute(
                    "UPDATE candidatures SET lien_norm = %s WHERE id = %s",
                    (norm_link(data.get("lien")), doc_id),
                )

        try:
            with pool.connection() as conn:
                conn.execute(
                    "CREATE UNIQUE INDEX IF NOT EXISTS candidatures_lien_uniq "
                    "ON candidatures (lien_norm) WHERE lien_norm <> ''"
                )
        except psycopg.errors.UniqueViolation:
            log.warning(
                "Des offres en double existent déjà. "
                "Supprimez les doublons pour activer le contrôle en base."
            )

        yield

    finally:
        pool.close()


app = FastAPI(
    title="Tracker candidatures",
    lifespan=lifespan,
)


# ---------------------------------------------------------------------------
# Authentification par page de connexion + cookie de session signé (HMAC)
# ---------------------------------------------------------------------------

SESSION_COOKIE = "tracker_session"
SESSION_TTL = int(os.getenv("SESSION_TTL_HOURS", "168")) * 3600   # 7 jours
COOKIE_SECURE = os.getenv("COOKIE_SECURE", "false").lower() == "true"
# Sans SECRET_KEY fixe, les sessions sont perdues à chaque redémarrage de l'API.
SECRET_KEY = (os.getenv("SECRET_KEY") or secrets.token_hex(32)).encode()

STATIC_DIR = Path(__file__).parent / "static"
PUBLIC_PATHS = {"/login", "/api/login", "/api/logout", "/api/health", "/favicon.ico"}

# Anti-bruteforce simple : 5 échecs par IP -> blocage 5 min
MAX_FAILS, LOCK_SECONDS = 5, 300
_fails: dict[str, list[float]] = {}


def _sign(payload: str) -> str:
    return hmac.new(SECRET_KEY, payload.encode(), hashlib.sha256).hexdigest()


def make_session(username: str) -> str:
    payload = base64.urlsafe_b64encode(
        f"{username}|{int(time.time()) + SESSION_TTL}".encode()
    ).decode()
    return f"{payload}.{_sign(payload)}"


def read_session(token: str | None) -> str | None:
    """Retourne l'utilisateur si le cookie est valide et non expiré."""
    if not token or "." not in token:
        return None
    payload, sig = token.rsplit(".", 1)
    if not hmac.compare_digest(sig, _sign(payload)):
        return None
    try:
        username, exp = base64.urlsafe_b64decode(payload).decode().rsplit("|", 1)
        if int(exp) < time.time():
            return None
    except (ValueError, binascii.Error, UnicodeDecodeError):
        return None
    return username if username == APP_USERNAME else None


def _client_ip(request: Request) -> str:
    return request.client.host if request.client else "?"


def _is_locked(ip: str) -> bool:
    now = time.time()
    recent = [t for t in _fails.get(ip, []) if now - t < LOCK_SECONDS]
    _fails[ip] = recent
    return len(recent) >= MAX_FAILS


@app.middleware("http")
async def require_login(request: Request, call_next):
    path = request.url.path

    if path in PUBLIC_PATHS:
        return await call_next(request)

    if not APP_USERNAME or not APP_PASSWORD:
        return PlainTextResponse("Authentication is not configured", status_code=503)

    if read_session(request.cookies.get(SESSION_COOKIE)):
        return await call_next(request)

    # Non connecté : 401 pour l'API, redirection vers /login pour les pages
    if path.startswith("/api/") or path in ("/docs", "/openapi.json", "/redoc"):
        return JSONResponse({"detail": "Non authentifié"}, status_code=401)

    return RedirectResponse("/login", status_code=303)


@app.get("/login", include_in_schema=False)
def login_page(request: Request):
    if read_session(request.cookies.get(SESSION_COOKIE)):
        return RedirectResponse("/", status_code=303)
    return FileResponse(STATIC_DIR / "login.html", headers={"Cache-Control": "no-store"})


@app.post("/api/login")
async def login(request: Request):
    ip = _client_ip(request)

    if not APP_USERNAME or not APP_PASSWORD:
        raise HTTPException(503, "Authentification non configurée")

    if _is_locked(ip):
        raise HTTPException(429, "Trop de tentatives. Réessayez dans quelques minutes.")

    try:
        body = await request.json()
        username = str(body.get("username", ""))
        password = str(body.get("password", ""))
    except Exception:
        raise HTTPException(400, "Requête invalide")

    ok = secrets.compare_digest(username.encode(), APP_USERNAME.encode()) & \
        secrets.compare_digest(password.encode(), APP_PASSWORD.encode())

    if not ok:
        _fails.setdefault(ip, []).append(time.time())
        await asyncio.sleep(0.6)   # ralentit les essais en rafale
        raise HTTPException(401, "Identifiant ou mot de passe incorrect")

    _fails.pop(ip, None)
    resp = JSONResponse({"status": "ok"})
    resp.set_cookie(
        SESSION_COOKIE,
        make_session(username),
        max_age=SESSION_TTL if body.get("remember", True) else None,
        httponly=True,
        secure=COOKIE_SECURE,
        samesite="lax",
        path="/",
    )
    return resp


@app.post("/api/logout")
def logout():
    resp = JSONResponse({"status": "ok"})
    resp.delete_cookie(SESSION_COOKIE, path="/")
    return resp


def check_id(doc_id: str) -> None:
    if not ID_RE.match(doc_id):
        raise HTTPException(
            status_code=422,
            detail="Identifiant invalide",
        )


@app.get("/api/health")
def health():
    """Vérifie que l'API peut joindre PostgreSQL."""
    with pool.connection() as conn:
        conn.execute("SELECT 1")

    return {"status": "ok"}


@app.get("/api/candidatures")
def list_candidatures():
    with pool.connection() as conn:
        rows = conn.execute(
            "SELECT id, ref, data FROM candidatures ORDER BY ref DESC"
        ).fetchall()

    return [
        {"id": row[0], "ref": row[1], "data": row[2]}
        for row in rows
    ]


@app.put("/api/candidatures/{doc_id}")
def upsert_candidature(doc_id: str, body: dict):
    check_id(doc_id)

    if not body.get("poste"):
        raise HTTPException(
            status_code=422,
            detail="Le champ 'poste' est obligatoire",
        )

    body.pop("ref", None)
    body.pop("id", None)

    lien = norm_link(body.get("lien"))

    with pool.connection() as conn:
        if lien:
            other = conn.execute(
                """
                SELECT ref, data->>'poste'
                FROM candidatures
                WHERE lien_norm = %s AND id <> %s
                """,
                (lien, doc_id),
            ).fetchone()

            if other:
                raise HTTPException(
                    status_code=409,
                    detail=(
                        f"Cette offre existe déjà (même lien) : "
                        f"#{other[0]} « {other[1]} »."
                    ),
                )

        try:
            row = conn.execute(
                """
                INSERT INTO candidatures (id, data, lien_norm, updated_at)
                VALUES (%s, %s, %s, now())
                ON CONFLICT (id) DO UPDATE
                    SET data = EXCLUDED.data,
                        lien_norm = EXCLUDED.lien_norm,
                        updated_at = now()
                RETURNING ref
                """,
                (doc_id, Jsonb(body), lien),
            ).fetchone()

        except psycopg.errors.UniqueViolation:
            raise HTTPException(
                status_code=409,
                detail="Cette offre existe déjà (même lien).",
            )

    return {"id": doc_id, "ref": row[0]}


@app.delete("/api/candidatures/{doc_id}", status_code=204)
def delete_candidature(doc_id: str):
    check_id(doc_id)

    with pool.connection() as conn:
        conn.execute(
            "DELETE FROM candidatures WHERE id = %s",
            (doc_id,),
        )

    return Response(status_code=204)


# ---------------------------------------------------------------------------
# CVs : un fichier par (version, langue), consultable depuis l'appli
# ---------------------------------------------------------------------------

CV_MAX_BYTES = int(os.getenv("CV_MAX_MB", "5")) * 1024 * 1024
CV_TYPES = {
    b"%PDF": ("application/pdf", ".pdf"),
    b"PK\x03\x04": (
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        ".docx",
    ),
}
LABEL_RE = re.compile(r"^[\w À-ÿ'’/()+.-]{1,60}$")


def _cv_meta(row):
    return {
        "id": row[0], "version": row[1], "lang": row[2], "filename": row[3],
        "mime": row[4], "size": row[5], "uploadedAt": row[6].isoformat(),
    }


@app.get("/api/cvs")
def list_cvs():
    with pool.connection() as conn:
        rows = conn.execute(
            "SELECT id, version, lang, filename, mime, size, uploaded_at "
            "FROM cvs ORDER BY version, lang"
        ).fetchall()
    return [_cv_meta(r) for r in rows]


@app.put("/api/cvs")
async def upload_cv(request: Request, version: str, lang: str, filename: str = "cv"):
    """Envoi brut du fichier (corps = octets du PDF/DOCX). Remplace l'existant."""
    if not LABEL_RE.match(version) or lang not in ("FR", "EN"):
        raise HTTPException(422, "Version ou langue invalide")

    if int(request.headers.get("content-length") or 0) > CV_MAX_BYTES:
        raise HTTPException(413, f"Fichier trop lourd (max {CV_MAX_BYTES // 1048576} Mo)")

    data = await request.body()
    if not data:
        raise HTTPException(422, "Fichier vide")
    if len(data) > CV_MAX_BYTES:
        raise HTTPException(413, f"Fichier trop lourd (max {CV_MAX_BYTES // 1048576} Mo)")

    kind = next((v for magic, v in CV_TYPES.items() if data.startswith(magic)), None)
    if not kind:
        raise HTTPException(415, "Format accepté : PDF ou Word (.docx)")
    mime, ext = kind

    name = Path(filename).name.strip()[:120] or "cv"
    if not name.lower().endswith(ext):
        name = Path(name).stem + ext

    with pool.connection() as conn:
        row = conn.execute(
            """
            INSERT INTO cvs (version, lang, filename, mime, size, data)
            VALUES (%s, %s, %s, %s, %s, %s)
            ON CONFLICT (version, lang) DO UPDATE
                SET filename = EXCLUDED.filename, mime = EXCLUDED.mime,
                    size = EXCLUDED.size, data = EXCLUDED.data, uploaded_at = now()
            RETURNING id, version, lang, filename, mime, size, uploaded_at
            """,
            (version, lang, name, mime, len(data), data),
        ).fetchone()
    return _cv_meta(row)


@app.get("/api/cvs/{cv_id}/file")
def get_cv_file(cv_id: int, download: bool = False):
    with pool.connection() as conn:
        row = conn.execute(
            "SELECT filename, mime, data FROM cvs WHERE id = %s", (cv_id,)
        ).fetchone()
    if not row:
        raise HTTPException(404, "CV introuvable")

    filename, mime, data = row
    disposition = "attachment" if download or mime != "application/pdf" else "inline"
    return Response(
        content=bytes(data),
        media_type=mime,
        headers={
            "Content-Disposition": f"{disposition}; filename*=UTF-8''{quote(filename)}",
            "Cache-Control": "private, no-store",
            "X-Content-Type-Options": "nosniff",
        },
    )


@app.delete("/api/cvs/{cv_id}", status_code=204)
def delete_cv(cv_id: int):
    with pool.connection() as conn:
        conn.execute("DELETE FROM cvs WHERE id = %s", (cv_id,))
    return Response(status_code=204)


# Le frontend est servi à la racine du site.
app.mount(
    "/",
    StaticFiles(
        directory=Path(__file__).parent / "static",
        html=True,
    ),
    name="static",
)