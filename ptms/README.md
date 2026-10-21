# PTMS — Proficiency Testing Management System

Application de gestion de campagnes d'essais d'aptitude (Proficiency Testing), construite avec **Flask + SQLAlchemy + Flask-Login + Bootstrap 5**.

## Évolutions « expert essais d'aptitude » (22 juillet 2026, ISO/CEI 17043 & ISO 13528)

- **Homogénéité et stabilité de l'échantillon (ISO 13528, Annexe B)** — nouveau module (`/homogeneity/trial/<id>`, bouton sur la liste des essais) : saisie de g échantillons x 2 réplicats, calcul ANOVA de s_w (intra-échantillon) et s_s (inter-échantillon), critère de conformité s_s ≤ 0,3×σpt ; comparaison stabilité début/fin de tour avec le même critère. C'était la principale lacune identifiée dans le cahier des charges — un tour d'essai ne peut être exploité sans cette vérification préalable.
- **Nombre En (ISO 13528 §9.3)** — calculé automatiquement à la soumission quand le laboratoire déclare son incertitude U(x) et que l'organisateur a renseigné l'incertitude de la valeur assignée U(X_ref) sur l'essai. Affiché dans le Centre de statistiques en complément du z-score/Grubbs.
- **Traçabilité de σpt** — chaque essai quantitatif indique désormais sa méthode de détermination (valeur fixe, équation de Horwitz, écart-type robuste des résultats), avec calcul automatique de σpt à partir de la concentration si Horwitz est choisi, et un texte de traçabilité conservé sur l'essai (exigence ISO 13528 §6-7).
- **Seuil minimal de participants (ISO 17043 §7.4)** — configurable par essai ; une alerte apparaît dans le Centre de statistiques si le nombre de soumissions est inférieur au seuil, pour ne pas surinterpréter un jeu de données trop restreint.
- **Suivi des soumissions** (`/participants/tracker`) — vue de synthèse par campagne : formulaires attendus / soumis / en retard, par laboratoire et par essai.
- **Solidité des couleurs modélisée en base** — les 9 paramètres (dégradation + dégorgement sur 8 tissus témoins) sont désormais capturés à la soumission (`Submission.solidite_data`), et non plus seulement dans l'Atelier de traitement autonome.
- **Pièces jointes** — chaque soumission peut inclure un rapport d'essai/certificat (PDF, image, Excel, Word, CSV), téléchargeable par le laboratoire et le personnel PTMS.

## Évolutions (22 juillet 2026)

- **Atelier de traitement des résultats** (`/traitement/atelier`, menu « Atelier de traitement ») : nouveau module intégrant Algorithme A (ISO 13528, Annexe C, winsorisation itérative), Médiane/MADe/nIQR, Moyenne/Écart-type et Solidité des couleurs (9 paramètres). Accessible aux rôles admin/chef technique/auditeur, authentifié via le compte PTMS. Depuis la fiche d'une campagne, le bouton « Atelier de traitement » pré-remplit automatiquement la Collecte des données à partir des soumissions réelles (essai quantitatif et essai qualitatif de la campagne). Le module Solidité des couleurs reste en saisie autonome dans l'Atelier (structure à 9 paramètres désormais capturée côté soumission, mais non encore reliée à ce module de calcul).
- **Parcours participant renforcé** : après soumission, un écran de **confirmation** récapitule les données saisies et le participant doit valider explicitement (« Confirmer ma soumission ») pour finaliser son entrée — statut visible dans « Mes formulaires ».
- **Récapitulatif PDF téléchargeable** pour chaque soumission (coordonnées du laboratoire, résultat, dates, pièce jointe, statut de confirmation), accessible au participant et au personnel PTMS.
- **Page « Mes coordonnées »** : le participant peut mettre à jour le contact, l'email, le téléphone et l'adresse de son laboratoire, sans accès aux autres données de gestion.
- **Centre de validation** : affichage de la date de soumission, du résultat, du statut de confirmation et de la **moyenne** des résultats déjà soumis pour l'essai (au lieu du z-score/interprétation, retirés à la demande — le traitement statistique détaillé se fait désormais dans l'Atelier de traitement / vos fichiers Excel internes).
- **Centre de statistiques** : z-score et interprétation retirés de l'affichage ; conservation de la médiane robuste, du MADe et du test de Grubbs (alerte QC) pour le quantitatif, et du consensus/taux d'accord pour le qualitatif.

## Évolutions (juillet 2026)

Mise à jour du code alignée sur le *Cahier des charges PTMS* (§8 "Évolutions possibles") et sur les modèles internes FTE_004 / FTE_008 :

- **Distinction quantitatif / qualitatif / solidité** dans le modèle de données (`Trial.trial_type`), avec formulaires de création (Wizard étape 2 et fiche essai) et de soumission adaptés à chaque type.
- **Champs supplémentaires capturés à la soumission** : réplicat 2 (répétabilité), instrument, accréditation ISO 17025, dates de réception/essai, température, humidité relative, incertitude élargie — ce qui permet aux exports FTE_004/FTE_008 de restituer ces colonnes automatiquement au lieu de les laisser vides.
- **Test de Grubbs** (ISO 5725-2 / ISO 13528 Annexe C, table de valeurs critiques usuelles α = 5 %) pour la détection de valeurs aberrantes sur les essais quantitatifs, affiché dans le Centre de statistiques.
- **Consensus qualitatif** (mode + taux d'accord) pour les essais qualitatifs et de solidité, avec statut "Conforme / Non conforme" par laboratoire.
- **Exports FTE_004 / FTE_008** désormais filtrés par type d'essai (FTE_004 = qualitatif/solidité, FTE_008 = quantitatif) et remplis avec les nouvelles métadonnées.
- **Rapport Word complet** : la section statistiques par essai bascule automatiquement entre l'affichage z-score (quantitatif) et consensus (qualitatif).

⚠️ Ces évolutions ajoutent des colonnes au schéma de base de données. Comme le projet n'utilise pas de migration (Alembic), il faut **réinitialiser la base** après mise à jour : supprimez le fichier `.db` (`ptms.db` à la racine, ou tout fichier dans `instance/`) puis relancez `python seed.py`.

Limites restantes : le module Solidité des couleurs de l'Atelier de traitement n'est pas encore relié à la base de données (saisie autonome) ; pas d'envoi d'e-mails automatique ; le critère d'homogénéité utilisé est le critère simple et suffisant (0,3×σpt) de l'ISO 13528 §B.2, plus rapide à mettre en œuvre que le critère complet à deux conditions (F-test) — à faire évoluer si une revue plus rigoureuse est nécessaire.

## Fonctionnalités

- **Authentification & rôles** : Admin, Chef Technique, Participant, Auditeur
- **Campaign Wizard** : assistant en 5 étapes pour créer une campagne complète (infos générales → essais → laboratoires → aperçu → génération auto des formulaires et comptes participants)
- **Gestion des campagnes** : CRUD, activation, clôture, archivage, duplication
- **Gestion des laboratoires** : CRUD complet
- **Gestion des essais** : CRUD rattaché aux campagnes
- **Portail participant** : soumission des résultats, suivi de ses propres formulaires
- **Submission Tracker** : vue d'ensemble de qui a soumis / qui manque / qui est en retard
- **Validation Center** : validation ou rejet des soumissions, avec calcul automatique du z-score (ISO 13528)
- **Statistics Center** : statistiques robustes (médiane, MAD normalisé) et interprétation des z-scores
- **Report Generator** : export Excel (.xlsx), PDF, et Word (.docx)
- **Dashboard KPI** : indicateurs clés par rôle (staff / participant)
- **Administration** : gestion des utilisateurs, audit trail complet de toutes les actions
- **Notifications** internes entre rôles (nouvelle campagne, nouvelle soumission, etc.)

## Installation

```bash
# 1. Créer un environnement virtuel (recommandé)
python3 -m venv venv
source venv/bin/activate      # Windows : venv\Scripts\activate

# 2. Installer les dépendances
pip install -r requirements.txt

# 3. Initialiser la base de données et créer les comptes de démonstration
python seed.py

# 4. Lancer l'application
python app.py
```

L'application est accessible sur **http://localhost:5000**.

## Comptes de démonstration

| Rôle           | Nom d'utilisateur | Mot de passe |
|----------------|--------------------|---------------|
| Admin          | `admin`            | `admin123`    |
| Chef Technique | `chef.technique`   | `chef123`     |
| Auditeur       | `auditeur`         | `audit123`    |

Un laboratoire de démonstration (`LAB001`) est également créé. Les comptes participants sont générés automatiquement lorsque vous créez une campagne via le **Wizard** (menu "Nouvelle campagne (Wizard)").

## Premiers pas

1. Connectez-vous avec le compte `admin`.
2. Allez dans **Nouvelle campagne (Wizard)** pour créer votre première campagne :
   - Étape 1 : informations générales (référence, nom, dates)
   - Étape 2 : ajout des essais (paramètre, unité, méthode, valeur assignée, sigma)
   - Étape 3 : sélection des laboratoires participants
   - Étape 4 : aperçu des formulaires qui seront générés (essai × laboratoire)
   - Étape 5 : validation → création de la campagne, des essais, des formulaires, et des comptes participants
3. Notez les identifiants générés pour chaque laboratoire (affichés à la fin du Wizard).
4. Activez la campagne pour permettre aux participants de soumettre leurs résultats.
5. Connectez-vous avec un compte participant pour soumettre un résultat.
6. Revenez avec le compte admin/chef technique pour valider les soumissions dans le **Centre de validation**, consulter les **Statistiques**, et générer un **Rapport** (Excel/PDF/Word).

## Structure du projet

```
ptms/
├── app.py                 # Factory Flask, enregistrement des blueprints
├── config.py              # Configuration (clé secrète, base de données)
├── extensions.py          # db, login_manager
├── models.py               # Modèles SQLAlchemy
├── utils.py                 # Audit trail, notifications, calculs ISO 13528
├── seed.py                    # Initialisation base + comptes démo
├── requirements.txt
├── blueprints/
│   ├── auth.py               # Login, profil, mot de passe
│   ├── dashboard.py          # KPI par rôle
│   ├── campaigns.py          # CRUD campagnes
│   ├── wizard.py              # Assistant en 5 étapes
│   ├── laboratories.py       # CRUD laboratoires
│   ├── trials.py               # CRUD essais
│   ├── participants.py       # Portail, tracker, validation
│   ├── statistics.py          # Statistiques ISO 13528
│   ├── reports.py              # Export Excel/PDF/Word
│   └── admin.py                # Utilisateurs, audit trail
├── templates/                  # Templates Bootstrap 5
└── ptms.db                     # Base SQLite (créée par seed.py)
```

## Notes techniques

- Base de données **SQLite** par défaut (fichier `ptms.db`), configurable via la variable d'environnement `DATABASE_URL`.
- Le z-score est calculé selon **ISO 13528** : `z = (x - X) / sigma`, avec seuils `|z| ≤ 2` (satisfaisant), `2 < |z| < 3` (douteux), `|z| ≥ 3` (non satisfaisant).
- Si la valeur assignée / sigma ne sont pas fixés a priori sur un essai, le module Statistiques calcule des estimateurs robustes (médiane, MAD normalisé) à partir des soumissions reçues.
- Pensez à changer la valeur de `SECRET_KEY` dans `config.py` (ou via la variable d'environnement `SECRET_KEY`) avant tout déploiement en production.
