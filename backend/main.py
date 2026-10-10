"""API locale du tracker de candidatures.

Chaque candidature est stockée comme un document JSON (colonne JSONB),
exactement comme dans la version claude.ai : le front n'a pas besoin de
connaître la structure des champs.
"""
import logging
import os
import re
from contextlib import asynccontextmanager
from pathlib import Path
from urllib.parse import parse_qsl, urlencode, urlsplit

import psycopg
from psycopg.types.json import Jsonb
from psycopg_pool import ConnectionPool
from fastapi import FastAPI, HTTPException, Response
from fastapi.staticfiles import StaticFiles

DATABASE_URL = os.getenv(
    "DATABASE_URL", "postgresql://tracker:tracker@localhost:5433/tracker"
)
ID_RE = re.compile(r"^[A-Za-z0-9_\-.~:@+]{1,200}$")

log = logging.getLogger("uvicorn.error")

# Paramètres de suivi ignorés pour comparer deux liens (mêmes règles que le front)
TRACKING_RE = re.compile(
    r"^(utm_.*|trk|trackingid|refid|ref|src|from|source|fbclid|gclid|origin|position|pagenum|currentjobid)$",
    re.I,
)


def norm_link(url: str | None) -> str:
    """Forme canonique d'un lien d'offre : sans www, sans traceurs, sans / final."""
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
    keep = sorted((k, v) for k, v in parse_qsl(parts.query) if not TRACKING_RE.match(k))
    query = "?" + urlencode(keep) if keep else ""
    return host + parts.path.rstrip("/") + query


pool = ConnectionPool(DATABASE_URL, min_size=1, max_size=5, open=False)


@asynccontextmanager
async def lifespan(_: FastAPI):
    pool.open(wait=True, timeout=30)
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
        # Numéro lisible (#1, #2…) attribué automatiquement, y compris aux offres existantes
        conn.execute("ALTER TABLE candidatures ADD COLUMN IF NOT EXISTS ref BIGSERIAL")
        conn.execute("ALTER TABLE candidatures ADD COLUMN IF NOT EXISTS lien_norm TEXT NOT NULL DEFAULT ''")
        for doc_id, data in conn.execute("SELECT id, data FROM candidatures WHERE lien_norm = ''").fetchall():
            conn.execute("UPDATE candidatures SET lien_norm = %s WHERE id = %s",
                         (norm_link(data.get("lien")), doc_id))
    # Un même lien ne peut exister qu'une fois (index créé à part : il échoue s'il reste des doublons)
    try:
        with pool.connection() as conn:
            conn.execute(
                "CREATE UNIQUE INDEX IF NOT EXISTS candidatures_lien_uniq "
                "ON candidatures (lien_norm) WHERE lien_norm <> ''"
            )
    except psycopg.errors.UniqueViolation:
        log.warning("Des offres en double (même lien) existent déjà : supprimez-les pour activer le contrôle en base.")
    yield
    pool.close()


app = FastAPI(title="Tracker candidatures (local)", lifespan=lifespan)


def check_id(doc_id: str) -> None:
    if not ID_RE.match(doc_id):
        raise HTTPException(status_code=422, detail="Identifiant invalide")


@app.get("/api/health")
def health():
    with pool.connection() as conn:
        conn.execute("SELECT 1")
    return {"status": "ok"}


@app.get("/api/candidatures")
def list_candidatures():
    with pool.connection() as conn:
        rows = conn.execute(
            "SELECT id, ref, data FROM candidatures ORDER BY ref DESC"
        ).fetchall()
    return [{"id": r[0], "ref": r[1], "data": r[2]} for r in rows]


@app.put("/api/candidatures/{doc_id}")
def upsert_candidature(doc_id: str, body: dict):
    check_id(doc_id)
    if not body.get("poste"):
        raise HTTPException(status_code=422, detail="Le champ 'poste' est obligatoire")
    body.pop("ref", None)  # attribué par la base, jamais par le client
    body.pop("id", None)
    lien = norm_link(body.get("lien"))
    with pool.connection() as conn:
        if lien:
            other = conn.execute(
                "SELECT ref, data->>'poste' FROM candidatures WHERE lien_norm = %s AND id <> %s",
                (lien, doc_id),
            ).fetchone()
            if other:
                raise HTTPException(
                    status_code=409,
                    detail=f"Cette offre existe déjà (même lien) : #{other[0]} « {other[1]} ».",
                )
        try:
            row = conn.execute(
                """
                INSERT INTO candidatures (id, data, lien_norm, updated_at)
                VALUES (%s, %s, %s, now())
                ON CONFLICT (id) DO UPDATE
                  SET data = EXCLUDED.data, lien_norm = EXCLUDED.lien_norm, updated_at = now()
                RETURNING ref
                """,
                (doc_id, Jsonb(body), lien),
            ).fetchone()
        except psycopg.errors.UniqueViolation:
            raise HTTPException(status_code=409, detail="Cette offre existe déjà (même lien).")
    return {"id": doc_id, "ref": row[0]}


@app.delete("/api/candidatures/{doc_id}", status_code=204)
def delete_candidature(doc_id: str):
    check_id(doc_id)
    with pool.connection() as conn:
        conn.execute("DELETE FROM candidatures WHERE id = %s", (doc_id,))
    return Response(status_code=204)


# Le front (index.html) est servi à la racine : http://localhost:8000
app.mount(
    "/",
    StaticFiles(directory=Path(__file__).parent / "static", html=True),
    name="static",
)
