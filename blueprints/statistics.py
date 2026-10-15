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
                values = [s.value for s in submissions if s.value is not None]
                alg_a_res = algorithm_a(values) if values else {"x_pt": None, "sigma_pt": None, "u_xpt": None, "U_xpt": None}
                grubbs = grubbs_test(values) if len(values) >= 3 else None

                target_val = trial.target_value if trial.target_value is not None else alg_a_res["x_pt"]
                target_sd = trial.target_sd if trial.target_sd is not None else alg_a_res["sigma_pt"]
                ref_unc = trial.reference_uncertainty if trial.reference_uncertainty is not None else alg_a_res["u_xpt"]

                z_prime_recommended = False
                if ref_unc is not None and target_sd and target_sd > 0:
                    z_prime_recommended = ref_unc >= (0.3 * target_sd)

                rows = []
                for s in submissions:
                    is_outlier = grubbs and s.value in (grubbs.get("outliers_list") or [])
                    z, _ = compute_z_score(s.value, target_val, target_sd, u_xpt=ref_unc)
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
                        "en_score": s.en_score,
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
                    "U_xpt": round(ref_unc * 2.0, 5) if ref_unc else None,
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

