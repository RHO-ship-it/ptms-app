import json
from datetime import datetime

from flask import Blueprint, render_template, redirect, url_for, request, flash
from flask_login import login_required, current_user

from extensions import db
from models import Trial, HomogeneityStabilityAssessment
from utils import compute_homogeneity, compute_stability, log_action
from blueprints.auth import roles_required

homogeneity_bp = Blueprint("homogeneity", __name__, template_folder="../templates/homogeneity")


@homogeneity_bp.route("/trial/<int:trial_id>", methods=["GET", "POST"])
@login_required
@roles_required("admin", "chef_technique", "auditeur")
def assess(trial_id):
    """Évaluation d'homogénéité et de stabilité de l'échantillon d'essai
    (ISO 13528 Annexe B), à réaliser par l'organisateur AVANT diffusion aux
    participants (homogénéité) et comparée en fin de tour (stabilité)."""
    trial = Trial.query.get_or_404(trial_id)
    assessment = trial.homogeneity_assessment

    if request.method == "POST":
        action = request.form.get("action")

        # Récupère les g paires de réplicats saisies dynamiquement (hom_x1_0, hom_x2_0, ...)
        sample_pairs = []
        i = 0
        while f"hom_x1_{i}" in request.form:
            x1_raw = request.form.get(f"hom_x1_{i}", "").strip()
            x2_raw = request.form.get(f"hom_x2_{i}", "").strip()
            x1 = float(x1_raw) if x1_raw else None
            x2 = float(x2_raw) if x2_raw else None
            if x1 is not None or x2 is not None:
                sample_pairs.append([x1, x2])
            i += 1

        stab_first = request.form.get("stability_first_value", "").strip()
        stab_last = request.form.get("stability_last_value", "").strip()
        notes = request.form.get("notes", "").strip()

        sigma_pt = trial.target_sd  # sigma_pt de l'essai, tracé via Trial.sigma_pt_method

        hom_result = compute_homogeneity(sample_pairs, sigma_pt)
        stab_result = compute_stability(
            float(stab_first) if stab_first else None,
            float(stab_last) if stab_last else None,
            sigma_pt,
        )

        if not assessment:
            assessment = HomogeneityStabilityAssessment(trial_id=trial.id)
            db.session.add(assessment)

        assessment.sample_data = json.dumps(sample_pairs)
        assessment.s_within = hom_result["s_within"]
        assessment.s_between = hom_result["s_between"]
        assessment.homogeneity_criterion = hom_result["criterion"]
        assessment.homogeneity_conform = hom_result["conform"]

        assessment.stability_first_value = float(stab_first) if stab_first else None
        assessment.stability_last_value = float(stab_last) if stab_last else None
        assessment.stability_criterion = stab_result["criterion"]
        assessment.stability_conform = stab_result["conform"]

        assessment.sigma_pt_used = sigma_pt
        assessment.notes = notes
        assessment.assessed_by = current_user.id
        assessment.assessed_at = datetime.utcnow()

        db.session.commit()
        log_action("assess_homogeneity", "Trial", trial.id,
                   f"Évaluation homogénéité/stabilité pour {trial.name} par {current_user.username}")
        flash("Évaluation d'homogénéité/stabilité enregistrée.", "success")
        return redirect(url_for("homogeneity.assess", trial_id=trial.id))

    sample_pairs = []
    if assessment and assessment.sample_data:
        try:
            sample_pairs = json.loads(assessment.sample_data)
        except (ValueError, TypeError):
            sample_pairs = []
    if not sample_pairs:
        sample_pairs = [[None, None] for _ in range(10)]  # 10 lignes vides par défaut (g=10 usuel)

    return render_template("homogeneity/assess.html", trial=trial, assessment=assessment,
                            sample_pairs=sample_pairs)
