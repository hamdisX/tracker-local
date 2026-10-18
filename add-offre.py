#!/usr/bin/env python3
"""Ajoute une offre au tracker (Backlog par défaut).

Usage :  python3 add-offre.py '{"poste":"…","entreprise":"…","lien":"…","datePublication":"AAAA-MM-JJ"}'
datePublication est optionnelle : absente, elle n'est pas enregistrée.
Identifiants : fichier ~/.tracker (TRACKER_USER=…, TRACKER_PASS=…, TRACKER_URL=… optionnel)
ou variables d'environnement du même nom.
"""
import datetime as dt
import http.cookiejar
import json
import os
import sys
import urllib.error
import urllib.request
import uuid
from pathlib import Path

cfg = {}
f = Path.home() / ".tracker"
if f.exists():
    for line in f.read_text().splitlines():
        if "=" in line and not line.lstrip().startswith("#"):
            k, v = line.split("=", 1)
            cfg[k.strip()] = v.strip().strip("'\"")
get = lambda k, d=None: os.environ.get(k) or cfg.get(k) or d

URL = get("TRACKER_URL", "https://tracker-yosra.onrender.com").rstrip("/")
USER, PASS = get("TRACKER_USER"), get("TRACKER_PASS")
if not USER or not PASS:
    sys.exit("Définir TRACKER_USER et TRACKER_PASS dans ~/.tracker")
if len(sys.argv) < 2:
    sys.exit(__doc__)

today, now = dt.date.today().isoformat(), dt.datetime.now(dt.timezone.utc).isoformat()
offre = {"type": "Emploi", "statut": "Repérée", "priorite": "Moyenne", "cv": "Agile", "lang": "FR",
         "lettre": "Non", "source": "Autre", "entreprise": "", "ville": "", "lien": "",
         "dateLimite": "", "dateEnvoi": "", "contact": "", "notes": ""}
offre.update(json.loads(sys.argv[1]))
# Date de publication (AAAA-MM-JJ) : conservée seulement si elle est fournie et valide
pub = str(offre.pop("datePublication", "") or "").strip()
if pub:
    try:
        offre["datePublication"] = dt.date.fromisoformat(pub).isoformat()
    except ValueError:
        sys.exit(f"✘ datePublication invalide « {pub} » (format AAAA-MM-JJ)")
offre.update(createdAt=now, updatedAt=now, history=[{"d": today, "s": offre["statut"]}])

opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))

def call(method, path, body, timeout=60):
    req = urllib.request.Request(URL + path, method=method, data=json.dumps(body).encode(),
                                 headers={"Content-Type": "application/json"})
    try:
        with opener.open(req, timeout=timeout) as r:
            return r.status, json.loads(r.read() or b"null")
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.loads(e.read()).get("detail")
        except Exception:
            return e.code, e.reason
    except (urllib.error.URLError, TimeoutError) as e:
        sys.exit(f"✘ Site injoignable ({URL}) : {getattr(e, 'reason', e)}")

print(f"Connexion à {URL} (réveil possible ~1 min)…")
code, res = call("POST", "/api/login", {"username": USER, "password": PASS}, timeout=120)
if code != 200:
    sys.exit(f"✘ Connexion refusée ({code}) : {res}")

code, res = call("PUT", f"/api/candidatures/{uuid.uuid4()}", offre)
if code == 200:
    print(f"✔ Offre #{res['ref']} ajoutée au Backlog : {offre['poste']} — {offre['entreprise']}")
elif code == 409:
    sys.exit(f"⚠ Doublon : {res}")
else:
    sys.exit(f"✘ Erreur {code} : {res}")