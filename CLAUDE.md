# Tracker candidatures — consignes pour Claude

Projet : suivi de candidatures d'une personne (FastAPI + PostgreSQL/Neon + front HTML), en ligne sur https://tracker-yosra.onrender.com (déploiement automatique Render à chaque push sur `main`). Voir README.md pour le lancement et l'API.

## Code
- `backend/main.py` : API (auth cookie signé, table `candidatures` en JSONB, table `cvs`, `auto_trash` : Backlog → Corbeille après 35 jours depuis `datePublication`). Pas de migration destructive : ne jamais supprimer de table ou de colonne.
- `backend/static/index.html` : tout le front (JS vanilla). Échapper toute donnée affichée avec `esc()`.
- `add-offre.py` : ajoute une offre via l'API (identifiants dans `~/.tracker`, que Claude ne lit pas).
- `check-doublon.py` : vérifie un doublon dans le dernier export CSV de `cvs/`.
- Ne jamais commiter ni pousser sans demande explicite. Le push déclenche un déploiement en production.
- `cvs/` (CVs PDF + exports CSV) et `backup*.sql` sont dans `.gitignore` : données personnelles, ne jamais les ajouter à git, ni en copier le contenu dans un fichier suivi.

## Statuts d'une offre
Repérée (Backlog) → À postuler → Envoyée → Relancée → Entretien ; Corbeille (offres périmées). Relance prévue 10 jours après l'envoi.

## Recherche d'offres — critères
- **Contrats** : CDI et CDD. Pas d'alternance pour le moment, ni stage ni freelance.
- **Domaines** : ESN ou client final, tous secteurs.
- **Zone** : Île-de-France et régions proches accessibles en transport (détail dans `.claude/profil.local.md`).
- **Versions de CV** (dans `cvs/`, toutes en français) : `Agile(Scrum Master)`, `Gestion de projet(CPF/S Master)`, `Électronique`, `Biomédical`, `Master / alternance` (alternance seulement). Les CVs ne se modifient pas.
- **Profil, éligibilité et zone exacte** : voir `.claude/profil.local.md` (local, ignoré par git). Le lire avant toute recherche ou validation d'offre.

## Procédure obligatoire avant de proposer ou d'ajouter une offre
1. **Doublon** : `python3 -I check-doublon.py '{"poste":"…","entreprise":"…","ville":"…","lien":"…"}'`. `DOUBLON` ou `PROBABLE` → ne pas proposer, indiquer l'offre existante et son statut. Si `age_jours` > 1, demander de ré-exporter le CSV (bouton Export du site, dans `cvs/`) avant de conclure « NOUVELLE ».
2. **Validation** : appeler l'agent `offre-validator` pour chaque offre restante (lien + version de CV visée). Ne présenter à l'utilisateur que les `MATCH` et `PARTIEL` (avec leurs réserves). Les `REJET` sont listés en une ligne avec la raison, jamais cachés.
3. **Ajout au tracker** : uniquement après confirmation explicite de l'utilisateur (l'écriture va en production). Utiliser `add-offre.py` avec le CV conseillé par le validateur et `datePublication` seulement si elle est connue (sinon l'omettre : pas de mise en corbeille automatique). Un 409 du serveur = doublon, ne pas insister.
4. Ne jamais présenter une offre sans lien vérifiable ni sans avoir ouvert la page de l'annonce. Les pages d'agrégateurs (Indeed, Jobijoba, Hellowork) sont souvent périmées : toujours confirmer sur l'annonce d'origine.

## Style
Répondre en français, de façon courte et directe. Donner les liens sources.
