from flask import Blueprint, render_template, request, flash, redirect, url_for
from flask_login import login_required

from extensions import db
from models import Campaign, Trial, Submission, ParticipantForm
from utils import (
    algorithm_a,
    median_made_niqr,
    m_estimator_logistic,
    classical_stats,
    algorithm_s,
    grubbs_test,
    droite_henry,
    boxplot_stats,
    kolmogorov_smirnov_test,
    kurtosis_and_skewness,
    kernel_density_estimation,
    compute_z_score,
    compute_en,
    qualitative_consensus,
    log_action,
    z_score_interpretation,
)
from blueprints.auth import roles_required

traitement_bp = Blueprint("traitement", __name__, url_prefix="/traitement")


@traitement_bp.route("/atelier", methods=["GET"])
@login_required
@roles_required("admin", "chef_technique", "auditeur")
def atelier():
    campaigns = Campaign.query.order_by(Campaign.created_at.desc()).all()
    trial_id = request.args.get("trial_id", type=int)

    selected_trial = Trial.query.get(trial_id) if trial_id else None

    # Si aucun essai sélectionné explicitement, prendre le 1er de la 1ère campagne si dispo
    if not selected_trial and campaigns:
        for c in campaigns:
            if c.trials:
                selected_trial = c.trials[0]
                break

    trials = []
    if campaigns:
        for c in campaigns:
            for t in c.trials:
                trials.append(t)

    participant_results = []
    stats = {}

    if selected_trial:
        submissions = [f.submission for f in selected_trial.forms if f.submission]

        if selected_trial.trial_type == "quantitative":
            values = [s.value for s in submissions if s.value is not None]
            replicates_2 = [s.replicate_2 for s in submissions if s.replicate_2 is not None]
            w_values = []
            if len(replicates_2) == len(values) and len(values) > 0:
                w_values = [abs(s.value - s.replicate_2) for s in submissions if s.value is not None and s.replicate_2 is not None]

            # 1. Estimateurs statistiques principaux
            alg_a = algorithm_a(values)
            med_stats = median_made_niqr(values)
            m_est = m_estimator_logistic(values)
            class_st = classical_stats(values)
            alg_s = algorithm_s(w_values, nu=1) if w_values else None

            # 2. Tests de normalité & valeurs aberrantes
            grubbs = grubbs_test(values) if len(values) >= 3 else None
            henry = droite_henry(values) if len(values) >= 3 else None
            box = boxplot_stats(values) if values else None
            ks = kolmogorov_smirnov_test(values) if len(values) >= 3 else None
            kurt_skew = kurtosis_and_skewness(values) if len(values) >= 3 else None
            kde = kernel_density_estimation(values) if len(values) >= 2 else []

            # 3. Méthode actuellement retenue pour la valeur assignée et l'écart-type
            target_val = selected_trial.target_value if selected_trial.target_value is not None else alg_a["x_pt"]
            target_sd = selected_trial.target_sd if selected_trial.target_sd is not None else alg_a["sigma_pt"]
            ref_unc = selected_trial.reference_uncertainty if selected_trial.reference_uncertainty is not None else alg_a["u_xpt"]

            # Contrôle u(x_pt) < 0.3 * sigma_pt (ISO 13528 / TTS)
            check_u_criterion = None
            z_prime_recommended = False
            if ref_unc is not None and target_sd and target_sd > 0:
                check_u_criterion = ref_unc < (0.3 * target_sd)
                z_prime_recommended = not check_u_criterion

            # Tableau des participants
            for s in submissions:
                z, anomaly = compute_z_score(s.value, target_val, target_sd, u_xpt=ref_unc)
                en, en_conform = compute_en(s.value, s.uncertainty, target_val, ref_unc * 2.0 if ref_unc else None)
                interp, interp_badge = z_score_interpretation(z)
                participant_results.append({
                    "lab_code": s.form.laboratory.code,
                    "lab_name": s.form.laboratory.name,
                    "value": s.value,
                    "replicate_2": s.replicate_2,
                    "uncertainty": s.uncertainty,
                    "z_score": z,
                    "z_interp": interp,
                    "z_badge": interp_badge,
                    "en_score": en,
                    "en_conform": en_conform,
                    "is_valid": s.validated,
                    "grubbs_outlier": grubbs and s.value in (grubbs.get("outliers_list") or []),
                })

            stats = {
                "count": len(values),
                "below_min": len(values) < (selected_trial.min_participants or 8),
                "min_participants": selected_trial.min_participants or 8,
                "alg_a": alg_a,
                "med_stats": med_stats,
                "m_est": m_est,
                "class_st": class_st,
                "alg_s": alg_s,
                "grubbs": grubbs,
                "henry": henry,
                "boxplot": box,
                "ks": ks,
                "kurt_skew": kurt_skew,
                "kde": kde,
                "target_val": target_val,
                "target_sd": target_sd,
                "ref_unc": ref_unc,
                "check_u_criterion": check_u_criterion,
                "z_prime_recommended": z_prime_recommended,
            }

        else:
            # Essais qualitatifs / solidité
            results = [s.qualitative_result for s in submissions if s.qualitative_result]
            consensus, agreement_rate, distribution = qualitative_consensus(results)

            for s in submissions:
                conform = s.qualitative_conform
                if conform is None and consensus and s.qualitative_result:
                    conform = s.qualitative_result.strip().lower() == consensus.strip().lower()
                participant_results.append({
                    "lab_code": s.form.laboratory.code,
                    "lab_name": s.form.laboratory.name,
                    "result": s.qualitative_result,
                    "conform": conform,
                    "is_valid": s.validated,
                })

            stats = {
                "count": len(results),
                "consensus": consensus,
                "agreement_rate": agreement_rate,
                "distribution": distribution,
            }

    return render_template(
        "traitement/atelier.html",
        campaigns=campaigns,
        trials=trials,
        selected_trial=selected_trial,
        selected_trial_id=selected_trial.id if selected_trial else None,
        test_category=selected_trial.trial_type if selected_trial else "quantitative",
        participant_results=participant_results,
        stats=stats,
    )


@traitement_bp.route("/apply_stats/<int:trial_id>", methods=["POST"])
@login_required
@roles_required("admin", "chef_technique")
def apply_stats(trial_id):
    trial = Trial.query.get_or_404(trial_id)
    method_choice = request.form.get("method_choice", "algorithm_a")

    target_val = request.form.get("target_value", type=float)
    target_sd = request.form.get("target_sd", type=float)
    ref_unc = request.form.get("reference_uncertainty", type=float)

    if method_choice == "algorithm_a":
        trial.sigma_pt_method = "robuste"
        trial.sigma_pt_traceability = "Valeur assignée (x_pt) et écart-type (sigma_pt) calculés selon l'Algorithme A de l'ISO 13528."
    elif method_choice == "median_made":
        trial.sigma_pt_method = "robuste"
        trial.sigma_pt_traceability = "Valeur assignée déterminée par la Médiane et écart-type par le MADe / nIQR (ISO 13528)."
    elif method_choice == "m_estimator":
        trial.sigma_pt_method = "robuste"
        trial.sigma_pt_traceability = "Valeur assignée et écart-type calculés par le M-estimateur logistique."
    elif method_choice == "classical":
        trial.sigma_pt_method = "fixe"
        trial.sigma_pt_traceability = "Moyenne et écart-type classiques des participants."
    else:
        trial.sigma_pt_method = "fixe"
        trial.sigma_pt_traceability = request.form.get("traceability_note", "Valeurs fixées manuellement.")

    if target_val is not None:
        trial.target_value = target_val
    if target_sd is not None:
        trial.target_sd = target_sd
    if ref_unc is not None:
        trial.reference_uncertainty = ref_unc

    db.session.commit()

    # Recalculer les z-scores et En-scores des participants pour cet essai
    submissions = [f.submission for f in trial.forms if f.submission]
    for s in submissions:
        if trial.trial_type == "quantitative" and s.value is not None:
            z, anomaly = compute_z_score(s.value, trial.target_value, trial.target_sd, u_xpt=trial.reference_uncertainty)
            en, _ = compute_en(s.value, s.uncertainty, trial.target_value, trial.reference_uncertainty * 2.0 if trial.reference_uncertainty else None)
            s.z_score = z
            s.anomaly_flag = anomaly
            s.en_score = en

    db.session.commit()

    log_action("update_stats", "Trial", trial.id, f"Mise à jour des estimateurs de l'essai {trial.name} ({method_choice})")
    flash(f"Les estimateurs statistiques de l'essai '{trial.name}' ont été mis à jour avec succès.", "success")
    return redirect(url_for("traitement.atelier", trial_id=trial.id))

