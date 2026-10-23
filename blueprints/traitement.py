from flask import Blueprint, render_template, request, flash, redirect, url_for
from datetime import datetime

from flask_login import login_required, current_user

from extensions import db
from models import Campaign, Trial, Submission, ParticipantForm, REPORT_ESTIMATORS
from utils import (
    algorithm_a,
    median_made_niqr,
    m_estimator_logistic,
    classical_stats,
    algorithm_s,
    grubbs_test,
    grubbs_detailed,
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
    compute_fleiss_kappa,
    qualitative_weighted_agreement,
    solidite_matrix_stats,
    mandel_h_statistic,
    compute_reliability_index,
    detect_systematic_trend,
    build_tools_summary,
    SOLIDITE_PARAMS,
)
from blueprints.auth import roles_required
from scoring import (assigned_values, estimation_values, score_result, recompute_scores,
                     split_uncertainty, K_EXPANSION)

traitement_bp = Blueprint("traitement", __name__, url_prefix="/traitement")


def _process_quantitative(trial, submissions):
    """Calcule tous les indicateurs statistiques pour un essai quantitatif."""
    # Résultats exclus du traitement par l'administrateur : écartés des estimateurs, mais toujours évalués.
    included = [s for s in submissions if not s.excluded_from_stats]
    values = estimation_values(submissions)
    replicates_2 = [s.replicate_2 for s in included if s.replicate_2 is not None]
    w_values = []
    if len(replicates_2) == len(values) and len(values) > 0:
        w_values = [abs(s.value - s.replicate_2) for s in included
                    if s.value is not None and s.replicate_2 is not None]

    alg_a = algorithm_a(values)
    med_stats = median_made_niqr(values)
    m_est = m_estimator_logistic(values)
    class_st = classical_stats(values)
    alg_s = algorithm_s(w_values, nu=1) if w_values else None

    grubbs = grubbs_test(values) if len(values) >= 3 else None
    grubbs_detail = grubbs_detailed(values) if len(values) >= 3 else None
    henry = droite_henry(values) if len(values) >= 3 else None
    box = boxplot_stats(values) if values else None
    ks = kolmogorov_smirnov_test(values) if len(values) >= 3 else None
    kurt_skew = kurtosis_and_skewness(values) if len(values) >= 3 else None
    kde = kernel_density_estimation(values) if len(values) >= 2 else []

    # Valeur assignée et incertitudes (convention ISO 13528 : U(x_pt) élargie k=2 stockée ; u = U/2 pour z' ;
    # U pour En). Voir scoring.py.
    av = assigned_values(trial, submissions, alg_a=alg_a)
    target_val, target_sd = av["x_pt"], av["sigma_pt"]
    u_xpt, U_xpt = av["u_xpt"], av["U_xpt"]

    check_u_criterion = None
    z_prime_recommended = av["z_prime"]
    if u_xpt is not None and target_sd and target_sd > 0:
        check_u_criterion = u_xpt <= (0.3 * target_sd)      # ISO 13528 §9.2 : u négligeable si u <= 0,3 sigma_pt

    # Statistique h de Mandel (résultats non exclus)
    mandel = mandel_h_statistic(included, target_val, target_sd)

    participant_results = []
    for s in submissions:
        z, anomaly, en, en_conform = score_result(s.value, s.uncertainty, av)
        interp, interp_badge = z_score_interpretation(z)
        is_grubbs_out = grubbs and s.value in (grubbs.get("outliers_list") or [])
        ifr = compute_reliability_index(z, en, is_grubbs_outlier=bool(is_grubbs_out))

        participant_results.append({
            "submission_id": s.id,
            "excluded": bool(s.excluded_from_stats),
            "exclusion_reason": s.exclusion_reason,
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
            "grubbs_outlier": is_grubbs_out,
            "ifr": ifr,
            "anomaly": anomaly,
        })

    stats = {
        "count": len(values),
        "below_min": len(values) < (trial.min_participants or 8),
        "min_participants": trial.min_participants or 8,
        "alg_a": alg_a,
        "med_stats": med_stats,
        "m_est": m_est,
        "class_st": class_st,
        "alg_s": alg_s,
        "grubbs": grubbs,
        "grubbs_detail": grubbs_detail,
        "henry": henry,
        "boxplot": box,
        "ks": ks,
        "kurt_skew": kurt_skew,
        "kde": kde,
        "target_val": target_val,
        "target_sd": target_sd,
        "u_xpt": u_xpt,
        "ref_unc": U_xpt,          # U(x_pt) élargie (k=2) : valeur pré-remplie dans le formulaire « Enregistrer »
        "excluded_count": len(submissions) - len(included),
        "check_u_criterion": check_u_criterion,
        "z_prime_recommended": z_prime_recommended,
        "mandel": mandel,
    }
    return stats, participant_results


def _process_qualitative(trial, submissions):
    """Calcule les indicateurs pour un essai qualitatif."""
    results = [s.qualitative_result for s in submissions if s.qualitative_result]
    consensus, agreement_rate, distribution = qualitative_consensus(results)
    # Kappa de Fleiss : accord corrigé du hasard sur les essais du même type de la campagne
    sibling_results = [
        [f.submission.qualitative_result for f in t.forms if f.submission and f.submission.qualitative_result]
        for t in trial.campaign.trials if t.trial_type == trial.trial_type
    ]
    kappa = compute_fleiss_kappa(sibling_results)
    weighted = qualitative_weighted_agreement(results)

    participant_results = []
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
        "kappa": kappa,
        "weighted": weighted,
    }
    return stats, participant_results


def _process_solidite(trial, submissions):
    """Calcule les indicateurs pour un essai de solidité des couleurs."""
    matrix_data = solidite_matrix_stats(submissions, SOLIDITE_PARAMS)
    participant_results = []
    for s in submissions:
        row = {
            "lab_code": s.form.laboratory.code,
            "lab_name": s.form.laboratory.name,
            "is_valid": s.validated,
        }
        # Chercher les données dans la matrix
        for m in matrix_data["matrix"]:
            if m["lab_code"] == s.form.laboratory.code:
                row["overall_score"] = m.get("overall_score")
                row["is_deviant"] = m.get("is_deviant", False)
                row["params"] = m.get("params", {})
                break
        participant_results.append(row)

    stats = {
        "count": len(submissions),
        "matrix": matrix_data["matrix"],
        "param_stats": matrix_data["param_stats"],
        "radar_data": matrix_data["radar_data"],
        "params": matrix_data["params"],
    }
    return stats, participant_results


@traitement_bp.route("/atelier", methods=["GET"])
@login_required
@roles_required("admin", "chef_technique", "auditeur")
def atelier():
    campaigns = Campaign.query.order_by(Campaign.created_at.desc()).all()
    trial_id = request.args.get("trial_id", type=int)

    selected_trial = Trial.query.get(trial_id) if trial_id else None

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
    tools_summary = []

    if selected_trial:
        submissions = [f.submission for f in selected_trial.forms if f.submission]
        trial_type = selected_trial.trial_type

        if trial_type == "quantitative":
            stats, participant_results = _process_quantitative(selected_trial, submissions)
            tools_summary = build_tools_summary("quantitative", stats, participant_results)

        elif trial_type == "qualitative":
            stats, participant_results = _process_qualitative(selected_trial, submissions)
            tools_summary = build_tools_summary("qualitative", stats, participant_results)

        else:  # solidite
            stats, participant_results = _process_solidite(selected_trial, submissions)
            tools_summary = build_tools_summary("solidite", stats, participant_results)

    return render_template(
        "traitement/atelier.html",
        campaigns=campaigns,
        trials=trials,
        selected_trial=selected_trial,
        selected_trial_id=selected_trial.id if selected_trial else None,
        test_category=selected_trial.trial_type if selected_trial else "quantitative",
        participant_results=participant_results,
        stats=stats,
        tools_summary=tools_summary,
        solidite_params=SOLIDITE_PARAMS,
        report_estimator_options=REPORT_ESTIMATORS,
    )


@traitement_bp.route("/dashboard", methods=["GET"])
@login_required
@roles_required("admin", "chef_technique", "auditeur")
def dashboard():
    """Dashboard récapitulatif de traitement par campagne."""
    campaigns = Campaign.query.order_by(Campaign.created_at.desc()).all()
    campaign_id = request.args.get("campaign_id", type=int)

    selected_campaign = None
    if campaign_id:
        selected_campaign = Campaign.query.get(campaign_id)
    elif campaigns:
        selected_campaign = campaigns[0]

    trial_cards = []
    global_kpis = {
        "total_trials": 0,
        "total_participants": 0,
        "total_satisfying": 0,
        "total_doubtful": 0,
        "total_unsatisfying": 0,
        "total_outliers": 0,
        "conformity_rate": 0,
        "iso_alerts": [],
    }

    if selected_campaign:
        for trial in selected_campaign.trials:
            submissions = [f.submission for f in trial.forms if f.submission]
            n = len(submissions)
            global_kpis["total_trials"] += 1
            global_kpis["total_participants"] += n

            card = {
                "trial": trial,
                "n": n,
                "type": trial.trial_type,
                "type_label": trial.type_label(),
                "tools_summary": [],
                "alerts": [],
                "kpis": {},
            }

            if trial.trial_type == "quantitative":
                stats, p_results = _process_quantitative(trial, submissions)
                card["stats"] = stats
                card["tools_summary"] = build_tools_summary("quantitative", stats)

                # KPIs
                satisfying = sum(1 for p in p_results if p["z_badge"] == "success")
                doubtful = sum(1 for p in p_results if p["z_badge"] == "warning")
                unsatisfying = sum(1 for p in p_results if p["z_badge"] == "danger")
                outliers = sum(1 for p in p_results if p.get("grubbs_outlier"))
                conform_rate = round(satisfying / n * 100, 1) if n > 0 else 0

                card["kpis"] = {
                    "x_pt": stats.get("target_val"),
                    "sigma_pt": stats.get("target_sd"),
                    "satisfying": satisfying,
                    "doubtful": doubtful,
                    "unsatisfying": unsatisfying,
                    "outliers": outliers,
                    "conform_rate": conform_rate,
                }

                global_kpis["total_satisfying"] += satisfying
                global_kpis["total_doubtful"] += doubtful
                global_kpis["total_unsatisfying"] += unsatisfying
                global_kpis["total_outliers"] += outliers

                # Alertes ISO
                if stats.get("below_min"):
                    alert = f"⚠️ Essai '{trial.name}' : Moins de {stats['min_participants']} participants (n={n})"
                    card["alerts"].append({"type": "warning", "msg": alert})
                    global_kpis["iso_alerts"].append(alert)

                if stats.get("z_prime_recommended"):
                    alert = f"ℹ️ Essai '{trial.name}' : z'-score recommandé (u(x_pt) ≥ 0.3×σ_pt)"
                    card["alerts"].append({"type": "info", "msg": alert})

                if stats.get("alg_a", {}).get("high_outliers_warning"):
                    alert = f"⚠️ Essai '{trial.name}' : Taux d'aberrants > 20% — Médiane recommandée"
                    card["alerts"].append({"type": "warning", "msg": alert})
                    global_kpis["iso_alerts"].append(alert)

                # Vérification homogénéité
                homog = trial.homogeneity_assessment
                if homog and homog.assessed_at and not homog.homogeneity_conform:
                    alert = f"🔴 Essai '{trial.name}' : Homogénéité non conforme (s_between > 0.3×σ_pt)"
                    card["alerts"].append({"type": "danger", "msg": alert})
                    global_kpis["iso_alerts"].append(alert)

            elif trial.trial_type == "qualitative":
                stats, p_results = _process_qualitative(trial, submissions)
                card["stats"] = stats
                card["tools_summary"] = build_tools_summary("qualitative", stats)

                conformes = sum(1 for p in p_results if p.get("conform"))
                card["kpis"] = {
                    "consensus": stats.get("consensus"),
                    "agreement_rate": stats.get("agreement_rate"),
                    "kappa": stats.get("kappa", {}).get("kappa"),
                    "conformes": conformes,
                    "non_conformes": n - conformes,
                    "conform_rate": round(conformes / n * 100, 1) if n > 0 else 0,
                }
                global_kpis["total_satisfying"] += conformes
                global_kpis["total_unsatisfying"] += (n - conformes)

            else:  # solidite
                stats, p_results = _process_solidite(trial, submissions)
                card["stats"] = stats
                card["tools_summary"] = build_tools_summary("solidite", stats)

                non_deviants = sum(1 for p in p_results if not p.get("is_deviant"))
                card["kpis"] = {
                    "n_params": len(SOLIDITE_PARAMS),
                    "non_deviants": non_deviants,
                    "deviants": n - non_deviants,
                    "conform_rate": round(non_deviants / n * 100, 1) if n > 0 else 0,
                }
                global_kpis["total_satisfying"] += non_deviants
                global_kpis["total_unsatisfying"] += (n - non_deviants)

            card["participant_results"] = p_results
            trial_cards.append(card)

        # Taux de conformité global
        total_eval = global_kpis["total_satisfying"] + global_kpis["total_doubtful"] + global_kpis["total_unsatisfying"]
        if total_eval > 0:
            global_kpis["conformity_rate"] = round(global_kpis["total_satisfying"] / total_eval * 100, 1)

    return render_template(
        "traitement/dashboard.html",
        campaigns=campaigns,
        selected_campaign=selected_campaign,
        trial_cards=trial_cards,
        global_kpis=global_kpis,
        solidite_params=SOLIDITE_PARAMS,
    )


@traitement_bp.route("/apply_stats/<int:trial_id>", methods=["POST"])
@login_required
@roles_required("admin", "chef_technique")
def apply_stats(trial_id):
    trial = Trial.query.get_or_404(trial_id)
    method_choice = request.form.get("method_choice", "algorithm_a")

    target_val = request.form.get("target_value", type=float)
    target_sd = request.form.get("target_sd", type=float)
    ref_unc = request.form.get("reference_uncertainty", type=float)   # U(x_pt), incertitude élargie k=2

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

    # Recalculer les z-scores et En-scores des participants pour cet essai (logique commune, scoring.py)
    recompute_scores(trial)
    db.session.commit()

    log_action("update_stats", "Trial", trial.id, f"Mise à jour des estimateurs de l'essai {trial.name} ({method_choice})")
    flash(f"Les estimateurs statistiques de l'essai '{trial.name}' ont été mis à jour avec succès.", "success")
    return redirect(url_for("traitement.atelier", trial_id=trial.id))


@traitement_bp.route("/exclude/<int:submission_id>", methods=["POST"])
@login_required
@roles_required("admin", "chef_technique")
def toggle_exclusion(submission_id):
    """L'administrateur décide d'exclure (ou de réintégrer) un résultat du traitement statistique.
    Le résultat exclu n'entre plus dans x_pt / sigma_pt mais reste évalué (z, En). Motif obligatoire."""
    sub = Submission.query.get_or_404(submission_id)
    trial = sub.form.trial
    back = redirect(url_for("traitement.atelier", trial_id=trial.id))

    if trial.campaign.report_published:
        flash("Le rapport de cette campagne est publié : les exclusions ne peuvent plus être modifiées.", "warning")
        return back

    action = request.form.get("action", "exclude")
    if action == "exclude":
        reason = request.form.get("reason", "").strip()
        if not reason:
            flash("Un motif est obligatoire pour exclure un résultat du traitement (traçabilité ISO/IEC 17043).", "warning")
            return back
        sub.excluded_from_stats = True
        sub.exclusion_reason = reason[:300]
        sub.excluded_by = current_user.id
        sub.excluded_at = datetime.utcnow()
        log_action("exclude_result", "Submission", sub.id,
                   f"Exclusion du traitement : {trial.name} / {sub.form.laboratory.code} — motif : {reason}")
        flash(f"Résultat de {sub.form.laboratory.code} exclu du calcul de x_pt et σ_pt (il reste évalué).", "info")
    else:
        sub.excluded_from_stats = False
        sub.exclusion_reason = None
        sub.excluded_by = None
        sub.excluded_at = None
        log_action("include_result", "Submission", sub.id,
                   f"Réintégration au traitement : {trial.name} / {sub.form.laboratory.code}")
        flash(f"Résultat de {sub.form.laboratory.code} réintégré au traitement.", "success")

    recompute_scores(trial)
    db.session.commit()
    return back


@traitement_bp.route("/report-estimators/<int:trial_id>", methods=["POST"])
@login_required
@roles_required("admin", "chef_technique")
def set_report_estimators(trial_id):
    """L'administrateur choisit (liste déroulante) les estimateurs affichés dans les rapports de l'essai.
    N'affecte ni x_pt, ni sigma_pt, ni les scores : c'est un choix de présentation."""
    trial = Trial.query.get_or_404(trial_id)
    if trial.campaign.report_published:
        flash("Le rapport de cette campagne est publié : le choix des estimateurs ne peut plus être modifié.", "warning")
        return redirect(url_for("traitement.atelier", trial_id=trial.id))

    allowed = [k for k, _ in REPORT_ESTIMATORS]
    chosen = [k for k in request.form.getlist("report_estimators") if k in allowed]
    trial.report_estimators = ",".join(chosen) if chosen else None
    log_action("set_report_estimators", "Trial", trial.id,
               f"Estimateurs affichés dans le rapport de {trial.name} : {', '.join(chosen) or 'aucun'}")
    db.session.commit()
    flash("Estimateurs du rapport enregistrés.", "success")
    return redirect(url_for("traitement.atelier", trial_id=trial.id))
