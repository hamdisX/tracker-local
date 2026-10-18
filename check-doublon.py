#!/usr/bin/env python3
"""Cherche si une offre est déjà dans le tracker (export CSV le plus récent de cvs/).

Usage :  python3 check-doublon.py '{"poste":"…","entreprise":"…","ville":"…","lien":"…"}'
Sortie JSON : {"verdict": "DOUBLON" | "PROBABLE" | "NOUVELLE", "csv": …, "age_jours": …, "match": {…}}
Même logique que le tracker : lien normalisé (sans paramètres de suivi) = doublon certain ;
même poste + entreprise + ville = doublon probable.
L'export est un instantané : le ré-exporter (bouton Export du site) pour qu'il soit à jour.
"""
import csv
import datetime as dt
import json
import re
import sys
import unicodedata
from pathlib import Path
from urllib.parse import parse_qsl, urlencode, urlsplit

TRACKING = re.compile(
    r"^(utm_.*|trk|trackingid|refid|ref|src|from|source|fbclid|gclid|origin|position|pagenum|currentjobid)$", re.I)


def norm_link(url):
    url = (url or "").strip()
    if not url:
        return ""
    if "://" not in url:
        url = "https://" + url
    try:
        p = urlsplit(url)
    except ValueError:
        return url.lower().rstrip("/")
    keep = sorted((k, v) for k, v in parse_qsl(p.query) if not TRACKING.match(k))
    host = (p.hostname or "").lower().removeprefix("www.")
    return host + p.path.rstrip("/") + ("?" + urlencode(keep) if keep else "")


def norm(s):
    s = unicodedata.normalize("NFD", str(s or "")).encode("ascii", "ignore").decode().lower()
    s = re.sub(r"\(?[fh]\s*/\s*[fh]\)?", " ", s)
    return re.sub(r"[^a-z0-9]+", " ", s).strip()


def key3(poste, entreprise, ville):
    return "|".join(norm(x) for x in (poste, entreprise, ville))


if len(sys.argv) < 2:
    sys.exit(__doc__)
offre = json.loads(sys.argv[1])

csvs = sorted((Path(__file__).parent / "cvs").glob("candidatures-*.csv"))
if not csvs:
    sys.exit("✘ Aucun export trouvé dans cvs/ (candidatures-*.csv). Utiliser le bouton Export du site.")
f = csvs[-1]
with f.open(encoding="utf-8-sig", newline="") as fh:
    rows = list(csv.DictReader(fh, delimiter=";"))

lien = norm_link(offre.get("lien"))
k = key3(offre.get("poste"), offre.get("entreprise"), offre.get("ville"))
verdict, match = "NOUVELLE", None
for r in rows:
    if lien and norm_link(r.get("Lien")) == lien:
        verdict, match = "DOUBLON", r
        break
    if verdict != "PROBABLE" and offre.get("poste") and offre.get("entreprise") \
            and key3(r.get("Poste / Formation"), r.get("Entreprise / École"), r.get("Ville")) == k:
        verdict, match = "PROBABLE", r

m = re.search(r"(\d{4}-\d{2}-\d{2})", f.name)
age = (dt.date.today() - dt.date.fromisoformat(m.group(1))).days if m else None
out = {"verdict": verdict, "csv": f.name, "age_jours": age, "nb_offres": len(rows)}
if match:
    out["match"] = {c: match.get(c) for c in ("Poste / Formation", "Entreprise / École", "Ville", "Statut", "Date envoi", "Lien")}
print(json.dumps(out, ensure_ascii=False, indent=2))
