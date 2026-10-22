from flask import Blueprint, render_template, request
from flask_login import login_required

from models import Campaign, Trial, Submission, ParticipantForm
from utils import (
    z_score_interpretation,
    algorithm_a,
    grubbs_test,
    qualitative_consensus,
    compute_z_score,
    compute_en,
)
from blueprints.auth import roles_required
from scoring import assigned_values, trial_submissions, estimation_values, score_result

statistics_bp = Blueprint("statistics", __name__, template_folder="../templates/statistics")


@statistics_bp.route("/")
@statistics_bp.route("/campaign/<int:campaign_id>")
@login_required
@roles_required("admin", "chef_technique", "auditeur")
def index(campaign_id=None):
    campaigns = Campaign.query.order_by(Campaign.created_at.desc()).all()
    selected_campaign = Campaign.query.get_or_404(campaign_id) if campaign_id else (campaigns[0] if campaigns else None)

    trial_stats = []
    if selected_campaign:
        for trial in selected_campaign.trials:
            submissions = [f.submission for f in trial.forms if f.submission]

            if trial.trial_type == "quantitative":
                # Estimation de x_pt / sigma_pt sur les résultats NON exclus par l'administrateur ;
                # tous les résultats (exclus compris) restent évalués.
                values = estimation_values(submissions)
                grubbs = grubbs_test(values) if len(values) >= 3 else None
                av = assigned_values(trial, submissions)

                target_val, target_sd = av["x_pt"], av["sigma_pt"]
                ref_unc, U_ref = av["u_xpt"], av["U_xpt"]      # u (z') et U (En), convention ISO 13528
                z_prime_recommended = av["z_prime"]

                rows = []
                for s in submissions:
                    is_outlier = grubbs and s.value in (grubbs.get("outliers_list") or [])
                    z, _, en_calc, _ = score_result(s.value, s.uncertainty, av)
                    interp, interp_badge = z_score_interpretation(z)
                    rows.append({
                        "lab": s.form.laboratory.code,
                        "value": s.value,
                        "replicate_2": s.replicate_2,
                        "z_score": z,
                        "z_interp": interp,
                        "z_badge": interp_badge,
                        "validated": s.validated,
                        "grubbs_outlier": is_outlier,
                        "excluded": bool(s.excluded_from_stats),
                        "exclusion_reason": s.exclusion_reason,
                        "en_score": en_calc,
                    })

                n_min = trial.min_participants or 8
                below_min = len(values) < n_min

                homog = trial.homogeneity_assessment
                homog_status = None
                if homog and homog.assessed_at:
                    homog_status = (homog.homogeneity_conform and
                                     (homog.stability_conform is None or homog.stability_conform))

                trial_stats.append({
                    "trial": trial,
                    "n": len(values),
                    "target_val": target_val,
                    "target_sd": target_sd,
                    "u_xpt": ref_unc,
                    "U_xpt": round(U_ref, 5) if U_ref is not None else None,
                    "z_prime_recommended": z_prime_recommended,
                    "grubbs": grubbs,
                    "rows": rows,
                    "below_min": below_min,
                    "n_min": n_min,
                    "homog_status": homog_status,
                })
            else:
                results = [s.qualitative_result for s in submissions if s.qualitative_result]
                consensus, agreement_rate, distribution = qualitative_consensus(results)

                rows = []
                for s in submissions:
                    conform = s.qualitative_conform
                    if conform is None and consensus is not None and s.qualitative_result:
                        conform = s.qualitative_result.strip().lower() == consensus.strip().lower()
                    rows.append({
                        "lab": s.form.laboratory.code,
                        "result": s.qualitative_result,
                        "conform": conform,
                        "validated": s.validated,
                    })

                trial_stats.append({
                    "trial": trial,
                    "n": len(results),
                    "consensus": consensus,
                    "agreement_rate": agreement_rate,
                    "distribution": distribution,
                    "rows": rows,
                })

    return render_template("statistics/index.html", campaigns=campaigns,
                            selected_campaign=selected_campaign, trial_stats=trial_stats)

