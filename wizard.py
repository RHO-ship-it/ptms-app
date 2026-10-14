import secrets
from datetime import datetime

from flask import Blueprint, render_template, redirect, url_for, request, flash, session
from flask_login import login_required, current_user

from extensions import db
from models import Campaign, Trial, Laboratory, CampaignLaboratory, ParticipantForm, User
from utils import log_action, notify_role
from blueprints.auth import roles_required

wizard_bp = Blueprint("wizard", __name__, template_folder="../templates/wizard")

SESSION_KEY = "wizard_data"


def _get_data():
    return session.get(SESSION_KEY, {"trials": [], "laboratory_ids": []})


def _save_data(data):
    session[SESSION_KEY] = data


def _reset():
    session.pop(SESSION_KEY, None)


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
    data = _get_data()
    if "reference" not in data:
        flash("Veuillez d'abord renseigner les informations générales.", "warning")
        return redirect(url_for("wizard.step1"))

    if request.method == "POST":
        if request.form.get("action") == "add_trial":
            trial = {
                "name": request.form.get("trial_name", "").strip(),
                "trial_type": request.form.get("trial_type", "quantitative"),
                "parameter": request.form.get("parameter", "").strip(),
                "unit": request.form.get("unit", "").strip(),
                "method": request.form.get("method", "").strip(),
                "sample_reference": request.form.get("sample_reference", "").strip(),
                "target_value": request.form.get("target_value", ""),
                "target_sd": request.form.get("target_sd", ""),
                "expected_qualitative_result": request.form.get("expected_qualitative_result", "").strip(),
            }
            if trial["name"]:
                data.setdefault("trials", []).append(trial)
                _save_data(data)
            return redirect(url_for("wizard.step2"))
        elif request.form.get("action") == "remove_trial":
            idx = int(request.form.get("index"))
            if 0 <= idx < len(data.get("trials", [])):
                data["trials"].pop(idx)
                _save_data(data)
            return redirect(url_for("wizard.step2"))
        elif request.form.get("action") == "next":
            if not data.get("trials"):
                flash("Veuillez ajouter au moins un essai.", "warning")
                return redirect(url_for("wizard.step2"))
            return redirect(url_for("wizard.step3"))

    return render_template("wizard/step2.html", data=data)


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
    forms_preview = []
    for trial in data["trials"]:
        for lab in labs:
            forms_preview.append({"trial": trial["name"], "lab": lab.code})

    if request.method == "POST":
        return redirect(url_for("wizard.step5"))

    return render_template("wizard/step4.html", data=data, labs=labs, forms_preview=forms_preview)


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
