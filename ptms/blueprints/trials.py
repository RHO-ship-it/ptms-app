from datetime import datetime

from flask import Blueprint, render_template, redirect, url_for, request, flash
from flask_login import login_required

from extensions import db
from models import Trial, Campaign, ParticipantForm, CampaignLaboratory
from utils import log_action, horwitz_rsd
from blueprints.auth import roles_required

trials_bp = Blueprint("trials", __name__, template_folder="../templates/trials")


@trials_bp.route("/campaign/<int:campaign_id>")
@login_required
@roles_required("admin", "chef_technique", "auditeur")
def list_trials(campaign_id):
    campaign = Campaign.query.get_or_404(campaign_id)
    return render_template("trials/list.html", campaign=campaign, trials=campaign.trials)


@trials_bp.route("/campaign/<int:campaign_id>/new", methods=["GET", "POST"])
@login_required
@roles_required("admin", "chef_technique")
def new_trial(campaign_id):
    campaign = Campaign.query.get_or_404(campaign_id)
    if request.method == "POST":
        due_date_str = request.form.get("due_date", "")
        due_date = datetime.strptime(due_date_str, "%Y-%m-%d").date() if due_date_str else None

        sigma_pt_method = request.form.get("sigma_pt_method", "fixe")
        min_participants_str = request.form.get("min_participants", "")
        reference_uncertainty_str = request.form.get("reference_uncertainty", "")
        horwitz_concentration_str = request.form.get("horwitz_concentration", "")
        target_value_str = request.form.get("target_value", "")

        sigma_pt_traceability = None
        target_sd_final = float(request.form["target_sd"]) if request.form.get("target_sd") else None
        if sigma_pt_method == "horwitz" and horwitz_concentration_str and target_value_str:
            concentration = float(horwitz_concentration_str)
            rsd_pct = horwitz_rsd(concentration)
            if rsd_pct is not None:
                target_sd_final = round(float(target_value_str) * rsd_pct / 100, 6)
                sigma_pt_traceability = (
                    f"σpt déterminé par l'équation de Horwitz pour une concentration de {concentration} "
                    f"(fraction massique) : RSD_H = {rsd_pct}% -> σpt = {target_sd_final}."
                )
        elif sigma_pt_method == "robuste":
            sigma_pt_traceability = ("σpt destiné à être établi à partir de l'écart-type robuste (Algorithme A / "
                                      "MADe) calculé dans l'Atelier de traitement, à reporter manuellement ici.")

        trial = Trial(
            campaign_id=campaign.id,
            name=request.form["name"].strip(),
            trial_type=request.form.get("trial_type", "quantitative"),
            parameter=request.form.get("parameter", "").strip(),
            unit=request.form.get("unit", "").strip(),
            method=request.form.get("method", "").strip(),
            sample_reference=request.form.get("sample_reference", "").strip(),
            target_value=float(request.form["target_value"]) if request.form.get("target_value") else None,
            target_sd=target_sd_final,
            expected_qualitative_result=request.form.get("expected_qualitative_result", "").strip() or None,
            due_date=due_date,
            sigma_pt_method=sigma_pt_method,
            sigma_pt_traceability=sigma_pt_traceability,
            min_participants=int(min_participants_str) if min_participants_str else None,
            reference_uncertainty=float(reference_uncertainty_str) if reference_uncertainty_str else None,
        )
        db.session.add(trial)
        db.session.commit()

        # Générer automatiquement les formulaires pour les labos déjà rattachés à la campagne
        for cl in campaign.laboratories:
            form = ParticipantForm(trial_id=trial.id, laboratory_id=cl.laboratory_id, status="pending")
            db.session.add(form)
        db.session.commit()

        log_action("create", "Trial", trial.id, f"Création essai {trial.name}")
        flash("Essai créé avec succès.", "success")
        return redirect(url_for("trials.list_trials", campaign_id=campaign.id))
    return render_template("trials/form.html", campaign=campaign, trial=None)


@trials_bp.route("/<int:trial_id>/edit", methods=["GET", "POST"])
@login_required
@roles_required("admin", "chef_technique")
def edit_trial(trial_id):
    trial = Trial.query.get_or_404(trial_id)
    if request.method == "POST":
        due_date_str = request.form.get("due_date", "")
        trial.due_date = datetime.strptime(due_date_str, "%Y-%m-%d").date() if due_date_str else None
        trial.name = request.form["name"].strip()
        trial.trial_type = request.form.get("trial_type", trial.trial_type)
        trial.parameter = request.form.get("parameter", "").strip()
        trial.unit = request.form.get("unit", "").strip()
        trial.method = request.form.get("method", "").strip()
        trial.sample_reference = request.form.get("sample_reference", "").strip()
        trial.target_value = float(request.form["target_value"]) if request.form.get("target_value") else None
        trial.expected_qualitative_result = request.form.get("expected_qualitative_result", "").strip() or None

        sigma_pt_method = request.form.get("sigma_pt_method", "fixe")
        min_participants_str = request.form.get("min_participants", "")
        reference_uncertainty_str = request.form.get("reference_uncertainty", "")
        horwitz_concentration_str = request.form.get("horwitz_concentration", "")
        target_value_str = request.form.get("target_value", "")

        sigma_pt_traceability = None
        target_sd_final = float(request.form["target_sd"]) if request.form.get("target_sd") else None
        if sigma_pt_method == "horwitz" and horwitz_concentration_str and target_value_str:
            concentration = float(horwitz_concentration_str)
            rsd_pct = horwitz_rsd(concentration)
            if rsd_pct is not None:
                target_sd_final = round(float(target_value_str) * rsd_pct / 100, 6)
                sigma_pt_traceability = (
                    f"σpt déterminé par l'équation de Horwitz pour une concentration de {concentration} "
                    f"(fraction massique) : RSD_H = {rsd_pct}% -> σpt = {target_sd_final}."
                )
        elif sigma_pt_method == "robuste":
            sigma_pt_traceability = ("σpt destiné à être établi à partir de l'écart-type robuste (Algorithme A / "
                                      "MADe) calculé dans l'Atelier de traitement, à reporter manuellement ici.")

        trial.target_sd = target_sd_final
        trial.sigma_pt_method = sigma_pt_method
        if sigma_pt_traceability:
            trial.sigma_pt_traceability = sigma_pt_traceability
        trial.min_participants = int(min_participants_str) if min_participants_str else None
        trial.reference_uncertainty = float(reference_uncertainty_str) if reference_uncertainty_str else None

        db.session.commit()
        log_action("update", "Trial", trial.id, f"Modification essai {trial.name}")
        flash("Essai mis à jour.", "success")
        return redirect(url_for("trials.list_trials", campaign_id=trial.campaign_id))
    return render_template("trials/form.html", campaign=trial.campaign, trial=trial)


@trials_bp.route("/<int:trial_id>/delete", methods=["POST"])
@login_required
@roles_required("admin")
def delete_trial(trial_id):
    trial = Trial.query.get_or_404(trial_id)
    campaign_id = trial.campaign_id
    name = trial.name
    db.session.delete(trial)
    db.session.commit()
    log_action("delete", "Trial", trial_id, f"Suppression essai {name}")
    flash("Essai supprimé.", "info")
    return redirect(url_for("trials.list_trials", campaign_id=campaign_id))
