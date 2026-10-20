import json
import re
import secrets
from datetime import datetime

from flask import Blueprint, render_template, redirect, url_for, request, flash, session
from flask_login import login_required, current_user

from extensions import db
from models import Campaign, Trial, Laboratory, CampaignLaboratory, ParticipantForm, User, WizardDraft
from blueprints.sheets import (make_sheet, PRESETS, SENSORY_NAME, SENSORY_ROWS, DEFAULT_IDENT, KINDS,
                               _slug, _pesticide_list)
from utils import log_action, notify_role
from blueprints.auth import roles_required

wizard_bp = Blueprint("wizard", __name__, template_folder="../templates/wizard")

SESSION_KEY = "wizard_data"


def _get_data():
    row = WizardDraft.query.filter_by(user_id=current_user.id).first()
    if row and row.data:
        try:
            data = json.loads(row.data)
            data.setdefault("trials", [])
            data.setdefault("laboratory_ids", [])
            return data
        except ValueError:
            pass
    return {"trials": [], "laboratory_ids": []}


def _save_data(data):
    row = WizardDraft.query.filter_by(user_id=current_user.id).first()
    if row is None:
        row = WizardDraft(user_id=current_user.id)
        db.session.add(row)
    row.data = json.dumps(data, ensure_ascii=False)
    db.session.commit()


def _reset():
    WizardDraft.query.filter_by(user_id=current_user.id).delete()
    db.session.commit()
    session.pop(SESSION_KEY, None)


def _rebuild_trials(data):
    """Liste à plat des essais (utilisée par les étapes 3 à 5) déduite des feuilles."""
    flat = []
    for s in data.get("sheets", []):
        for r in s["rows"]:
            flat.append({**r, "trial_type": r.get("trial_type") or s["kind"], "sheet_key": s["key"], "sheet_title": s["title"]})
    data["trials"] = flat


def _float_or_none(text):
    text = (text or "").strip().replace(",", ".")
    if not text:
        return ""
    float(text)          # lève ValueError si invalide
    return text


@wizard_bp.route("/start")
@login_required
@roles_required("admin", "chef_technique")
def start():
    _reset()
    return redirect(url_for("wizard.step1"))


@wizard_bp.route("/step1", methods=["GET", "POST"])
@login_required
@roles_required("admin", "chef_technique")
def step1():
    data = _get_data()
    if request.method == "POST":
        reference = request.form["reference"].strip()

        # --- Vérification unicité de la référence ---
        existing = Campaign.query.filter_by(reference=reference).first()
        if existing:
            flash(
                f"La référence « {reference} » est déjà utilisée par la campagne "
                f"« {existing.name} ». Veuillez choisir une référence différente.",
                "danger",
            )
            data["reference"] = reference  # re-remplir le champ
            data["name"] = request.form.get("name", "").strip()
            data["description"] = request.form.get("description", "").strip()
            data["start_date"] = request.form.get("start_date", "")
            data["end_date"] = request.form.get("end_date", "")
            return render_template("wizard/step1.html", data=data)

        data["reference"] = reference
        data["name"] = request.form["name"].strip()
        data["description"] = request.form.get("description", "").strip()
        data["start_date"] = request.form.get("start_date", "")
        data["end_date"] = request.form.get("end_date", "")
        _save_data(data)
        return redirect(url_for("wizard.step2"))
    return render_template("wizard/step1.html", data=data)


@wizard_bp.route("/step2", methods=["GET", "POST"])
@login_required
@roles_required("admin", "chef_technique")
def step2():
    """Essais de la campagne = feuilles de saisie (onglets vus par les laboratoires) et leurs lignes (paramètres)."""
    data = _get_data()
    if "reference" not in data:
        flash("Veuillez d'abord renseigner les informations générales.", "warning")
        return redirect(url_for("wizard.step1"))
    data.setdefault("sheets", [])

    if request.method == "POST":
        f = request.form
        action = f.get("action")
        try:
            if action == "add_sheet":
                _add_sheet(data, f)
            elif action == "remove_sheet":
                idx = f.get("sheet", type=int)
                if idx is not None and 0 <= idx < len(data["sheets"]):
                    data["sheets"].pop(idx)
            elif action in ("add_row", "bulk_rows", "remove_row"):
                _edit_rows(data, f, action)
            elif action == "next":
                if not any(s["rows"] for s in data["sheets"]):
                    flash("Ajoutez au moins une feuille avec une ligne.", "warning")
                    return redirect(url_for("wizard.step2"))
                _rebuild_trials(data)
                _save_data(data)
                return redirect(url_for("wizard.step3"))
        except ValueError as exc:
            flash(str(exc), "danger")
            return redirect(url_for("wizard.step2"))
        _rebuild_trials(data)
        _save_data(data)
        return redirect(url_for("wizard.step2", _anchor=("sheet-%s" % f.get("sheet")) if f.get("sheet") not in (None, "") else None))

    previews = []
    for s in data["sheets"]:
        sheet = make_sheet(s["preset"], s["title"], s["key"])
        previews.append([c["label"] for c in sheet["cols"]] if sheet["kind"] != "sensory" else
                        ["Panel + grille des dégustateurs", "Médianes (auto)", "Classification", "Type de fruité", "Défaut dominant"])
    return render_template("wizard/step2.html", data=data, presets=PRESETS, kinds=KINDS, previews=previews,
                           pesticides=_pesticide_list())


def _add_sheet(data, f):
    preset = f.get("preset")
    if preset not in PRESETS:
        raise ValueError("Choisissez le modèle de feuille.")
    label, kind = PRESETS[preset][0], PRESETS[preset][1]
    title = (f.get("title") or "").strip() or label.split(":")[0]
    name = (f.get("trial_name") or "").strip() or title
    sample = (f.get("sample_reference") or "").strip()
    key, n = _slug(title, "feuille"), 2
    while any(s["key"] == key for s in data["sheets"]):
        key, n = f"{_slug(title, 'feuille')}_{n}", n + 1
    sheet = {"key": key, "title": title, "preset": preset, "kind": kind, "name": name, "sample": sample, "rows": []}
    if kind == "sensory":      # les 3 résultats transmis pour évaluation sont créés d'office
        for pname, ptype in SENSORY_ROWS:
            sheet["rows"].append({"name": name or SENSORY_NAME, "trial_type": ptype, "parameter": pname, "unit": "",
                                  "method": "COI", "sample_reference": sample, "target_value": "", "target_sd": "",
                                  "expected_qualitative_result": (f.get("expected") or "").strip() if ptype == "qualitative" else ""})
    data["sheets"].append(sheet)


def _edit_rows(data, f, action):
    idx = f.get("sheet", type=int)
    if idx is None or not 0 <= idx < len(data["sheets"]):
        raise ValueError("Feuille inconnue.")
    sheet = data["sheets"][idx]
    if action == "remove_row":
        ridx = f.get("row", type=int)
        if ridx is not None and 0 <= ridx < len(sheet["rows"]):
            sheet["rows"].pop(ridx)
        return
    if sheet["kind"] == "sensory":
        raise ValueError("Les lignes d'une feuille sensorielle sont fixes (fruité, défaut, catégorie).")
    unit, method = (f.get("unit") or "").strip(), (f.get("method") or "").strip()
    sample = (f.get("sample_reference") or "").strip() or sheet.get("sample", "")
    name = (f.get("trial_name") or "").strip() or sheet.get("name") or sheet["title"]
    is_quant = sheet["kind"] == "quantitative"

    def row(parameter, target="", sd="", expected=""):
        return {"name": name, "trial_type": sheet["kind"], "parameter": parameter, "unit": unit, "method": method,
                "sample_reference": sample, "target_value": _float_or_none(target) if is_quant else "",
                "target_sd": _float_or_none(sd) if is_quant else "",
                "expected_qualitative_result": "" if is_quant else (expected or "").strip()}

    if action == "add_row":
        parameter = (f.get("parameter") or "").strip()
        if not parameter:
            raise ValueError("Indiquez le paramètre.")
        sheet["rows"].append(row(parameter, f.get("target_value"), f.get("target_sd"), f.get("expected")))
    else:   # bulk_rows : « paramètre [; valeur assignée / attendu [; sigma]] », une ligne par paramètre (collage Excel possible)
        existing = {r["parameter"].lower() for r in sheet["rows"]}
        added = 0
        for line in (f.get("lines") or "").splitlines():
            parts = [p.strip() for p in re.split(r"[;\t]", line)]
            if not parts or not parts[0] or parts[0].lower() in existing:
                continue
            existing.add(parts[0].lower())
            sheet["rows"].append(row(parts[0], parts[1] if len(parts) > 1 else "", parts[2] if len(parts) > 2 else "",
                                     parts[1] if len(parts) > 1 else ""))
            added += 1
        flash(f"{added} ligne(s) ajoutée(s) à « {sheet['title']} ».", "success" if added else "info")


@wizard_bp.route("/step3", methods=["GET", "POST"])
@login_required
@roles_required("admin", "chef_technique")
def step3():
    data = _get_data()
    if not data.get("trials"):
        flash("Veuillez d'abord ajouter des essais.", "warning")
        return redirect(url_for("wizard.step2"))

    labs = Laboratory.query.filter_by(active=True).order_by(Laboratory.code).all()

    if request.method == "POST":
        selected = request.form.getlist("laboratory_ids")
        if not selected:
            flash("Veuillez sélectionner au moins un laboratoire.", "warning")
            return redirect(url_for("wizard.step3"))
        data["laboratory_ids"] = selected
        _save_data(data)
        return redirect(url_for("wizard.step4"))

    return render_template("wizard/step3.html", data=data, labs=labs)


@wizard_bp.route("/step4", methods=["GET", "POST"])
@login_required
@roles_required("admin", "chef_technique")
def step4():
    """Aperçu et génération automatique des formulaires (essai x laboratoire)."""
    data = _get_data()
    if not data.get("laboratory_ids"):
        flash("Veuillez d'abord sélectionner les laboratoires.", "warning")
        return redirect(url_for("wizard.step3"))

    labs = Laboratory.query.filter(Laboratory.id.in_(data["laboratory_ids"])).all()
    sheets_preview = [{"title": s["title"], "kind": KINDS[s["kind"]], "name": s.get("name", ""), "sample": s.get("sample", ""),
                       "rows": len(s["rows"]), "forms": len(s["rows"]) * len(labs)} for s in data.get("sheets", [])]
    total_forms = len(data["trials"]) * len(labs)

    if request.method == "POST":
        return redirect(url_for("wizard.step5"))

    return render_template("wizard/step4.html", data=data, labs=labs, sheets_preview=sheets_preview, total_forms=total_forms)


@wizard_bp.route("/step5", methods=["GET", "POST"])
@login_required
@roles_required("admin", "chef_technique")
def step5():
    """Génération des comptes participants et finalisation."""
    data = _get_data()
    if not data.get("laboratory_ids"):
        flash("Session du wizard expirée, veuillez recommencer.", "warning")
        return redirect(url_for("wizard.start"))

    labs = Laboratory.query.filter(Laboratory.id.in_(data["laboratory_ids"])).all()

    if request.method == "POST":
        # --- Vérification de sécurité finale : la référence est-elle encore disponible ? ---
        if Campaign.query.filter_by(reference=data["reference"]).first():
            flash(
                f"La référence « {data['reference']} » est déjà utilisée. "
                f"Veuillez retourner à l'étape 1 et choisir une nouvelle référence.",
                "danger",
            )
            return redirect(url_for("wizard.step1"))

        try:
            # 1. Créer la campagne
            start_date = datetime.strptime(data["start_date"], "%Y-%m-%d").date() if data.get("start_date") else None
            end_date = datetime.strptime(data["end_date"], "%Y-%m-%d").date() if data.get("end_date") else None

            campaign = Campaign(
                reference=data["reference"],
                name=data["name"],
                description=data.get("description", ""),
                status="draft",
                start_date=start_date,
                end_date=end_date,
                created_by=current_user.id,
            )
            # Feuille de saisie vue par les laboratoires : une feuille par modèle choisi à l'étape 2
            campaign.sheet_config = json.dumps({"ident": list(DEFAULT_IDENT), "sheets": [
                make_sheet(s["preset"], s["title"], s["key"]) for s in data.get("sheets", [])]}, ensure_ascii=False)
            db.session.add(campaign)
            db.session.flush()

            # 2. Rattacher les laboratoires
            for lab in labs:
                db.session.add(CampaignLaboratory(campaign_id=campaign.id, laboratory_id=lab.id))

            # 3. Créer les essais
            created_trials = []
            for t in data["trials"]:
                due_date = datetime.strptime(data["end_date"], "%Y-%m-%d").date() if data.get("end_date") else None
                trial = Trial(
                    campaign_id=campaign.id,
                    name=t["name"],
                    trial_type=t.get("trial_type", "quantitative"),
                    parameter=t.get("parameter", ""),
                    unit=t.get("unit", ""),
                    method=t.get("method", ""),
                    sample_reference=t.get("sample_reference", ""),
                    target_value=float(t["target_value"]) if t.get("target_value") else None,
                    target_sd=float(t["target_sd"]) if t.get("target_sd") else None,
                    expected_qualitative_result=t.get("expected_qualitative_result", "") or None,
                    sheet_key=t.get("sheet_key"),
                    due_date=due_date,
                )
                db.session.add(trial)
                created_trials.append(trial)
            db.session.flush()

            # 4. Générer les formulaires (essai x labo)
            for trial in created_trials:
                for lab in labs:
                    db.session.add(ParticipantForm(trial_id=trial.id, laboratory_id=lab.id, status="pending"))

            # 5. Générer les comptes participants (un par laboratoire, s'il n'existe pas déjà)
            created_accounts = []
            for lab in labs:
                existing_user = User.query.filter_by(laboratory_id=lab.id, role="participant").first()
                if existing_user:
                    continue
                username = f"{lab.code.lower()}.{campaign.reference.lower().replace(' ', '-')}"
                # S'assurer que le username est unique
                if User.query.filter_by(username=username).first():
                    username = f"{username}.{secrets.token_hex(3)}"
                temp_password = secrets.token_urlsafe(8)
                user = User(
                    username=username,
                    email=lab.email or f"{username}@ptms.local",
                    full_name=f"Participant {lab.name}",
                    role="participant",
                    laboratory_id=lab.id,
                    active=True,
                )
                user.set_password(temp_password)
                db.session.add(user)
                created_accounts.append({"username": username, "password": temp_password, "lab": lab.code})

            db.session.commit()

            log_action("create", "Campaign", campaign.id,
                        f"Campagne {campaign.reference} créée via le Wizard "
                        f"({len(created_trials)} essais, {len(labs)} laboratoires)")

            notify_role("chef_technique", f"Nouvelle campagne créée via le Wizard : {campaign.reference}")

            session["wizard_result"] = {
                "campaign_id": campaign.id,
                "campaign_reference": campaign.reference,
                "accounts": created_accounts,
                "nb_trials": len(created_trials),
                "nb_labs": len(labs),
                "nb_forms": len(created_trials) * len(labs),
            }
            _reset()
            return redirect(url_for("wizard.summary"))

        except Exception as e:
            db.session.rollback()
            flash(
                f"Erreur lors de la création de la campagne : {str(e)}. "
                f"Si le problème persiste, vérifiez que la référence est unique.",
                "danger",
            )
            return redirect(url_for("wizard.step1"))

    return render_template("wizard/step5.html", data=data, labs=labs)


@wizard_bp.route("/summary")
@login_required
@roles_required("admin", "chef_technique")
def summary():
    result = session.get("wizard_result")
    if not result:
        return redirect(url_for("campaigns.list_campaigns"))
    return render_template("wizard/summary.html", result=result)
