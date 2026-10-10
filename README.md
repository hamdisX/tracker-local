# Tracker candidatures Yosra — version locale

- **Front** : `backend/static/index.html` (même page que sur claude.ai)
- **API** : Python / FastAPI (`backend/main.py`)
- **Base** : PostgreSQL 16 dans Docker (volume `pgdata`, les données survivent aux redémarrages)

## Connexion

Le site est protégé par une page de connexion (`/login`) et un cookie de session signé (HttpOnly, SameSite=Lax, 7 jours).

Créer un fichier `.env` à côté de `docker-compose.yml` :

```bash
APP_USERNAME=yosra
APP_PASSWORD=un-mot-de-passe-solide
SECRET_KEY=$(openssl rand -hex 32)   # coller la valeur générée
COOKIE_SECURE=false                  # true dès que le site est en HTTPS
```

En option A (venv), exporter les mêmes variables avant `uvicorn` :
`set -a; source ../.env; set +a`

5 mauvais mots de passe depuis la même IP bloquent les essais pendant 5 minutes.

## Option A — Python en local (venv) + Postgres dans Docker

```bash
cd tracker-local
docker compose up -d db                 # lance uniquement PostgreSQL

cd backend
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
uvicorn main:app --reload --port 8000
```

Ouvrir http://localhost:8000

## Option B — tout dans Docker

```bash
cd tracker-local
docker compose --profile full up -d --build
```

Ouvrir http://localhost:8000

## Option C — en ligne gratuitement (Render + Neon)

1. **Neon** (neon.com) : créer un projet, région *AWS Frankfurt*, copier la chaîne de connexion (`postgresql://…?sslmode=require`).
2. **GitHub** : pousser ce dossier (le `.env` est ignoré).
3. **Render** (render.com) : *New → Blueprint* → choisir le repo. `render.yaml` crée le service ; saisir `DATABASE_URL` et `APP_PASSWORD`.
4. Ouvrir `https://tracker-yosra.onrender.com` (premier accès après 15 min d'inactivité : ~1 min de réveil).

Reprendre les données locales dans Neon :

```bash
docker compose exec -T db pg_dump -U tracker --no-owner --no-acl tracker > backup.sql
psql "postgresql://…neon…?sslmode=require" < backup.sql
```

## API

| Méthode | Route | Rôle |
|---|---|---|
| GET | `/api/health` | Vérifie l'accès à la base (public) |
| POST | `/api/login` | Connexion (`{username, password, remember}`) |
| POST | `/api/logout` | Déconnexion |
| GET | `/api/candidatures` | Liste toutes les offres |
| PUT | `/api/candidatures/{id}` | Crée ou remplace une offre |
| DELETE | `/api/candidatures/{id}` | Supprime une offre |

Documentation interactive : http://localhost:8000/docs

## Commandes utiles

```bash
# Voir les données
docker compose exec db psql -U tracker -c "SELECT id, data->>'poste', data->>'statut' FROM candidatures;"

# Sauvegarde / restauration
docker compose exec -T db pg_dump -U tracker tracker > backup.sql
docker compose exec -T db psql -U tracker tracker < backup.sql

# Tout arrêter (garde les données) / tout effacer
docker compose --profile full down
docker compose --profile full down -v
```

## Notes

- La page se rafraîchit toutes les 5 s : deux navigateurs ouverts voient les modifications de l'autre.
- Avant toute mise en ligne : HTTPS obligatoire (reverse proxy) + `COOKIE_SECURE=true`.
- Identifiants Postgres (`tracker/tracker`) à changer hors local.
