"""
Évaluation de la performance d'un essai quantitatif — logique unique partagée par le Centre de
statistiques, l'Atelier de traitement, les soumissions et les rapports.

Références normatives :
  - ISO 13528:2022 §7 (valeur assignée x_pt, u(x_pt)), §9.2 (z), §9.3 (z'), §9.5 (En)
  - ISO/IEC 17043:2023 §7.4 (traitement des données, valeurs aberrantes, traçabilité)

Convention d'incertitude (unique dans toute l'application)
----------------------------------------------------------
  Trial.reference_uncertainty = U(x_pt), incertitude ÉLARGIE, facteur d'élargissement k = 2
                                (c'est la grandeur d'un certificat de référence / de l'étiquette du formulaire).
  u(x_pt) = U(x_pt) / k          -> incertitude-type, utilisée UNIQUEMENT pour z'.
  U(x_pt)                        -> utilisée UNIQUEMENT pour En (avec U(x_i) déclarée par le laboratoire).

  Critère ISO 13528 §9.2 : si u(x_pt) <= 0,3 sigma_pt, u est négligeable -> z ; sinon -> z'.

Traitement des résultats exclus
-------------------------------
  Un résultat exclu par l'administrateur (Submission.excluded_from_stats) n'entre pas dans le calcul
  de x_pt, sigma_pt et u(x_pt) par l'Algorithme A, mais il est toujours ÉVALUÉ (z, En) par rapport à
  ces valeurs (ISO 13528 §6.6 : les valeurs aberrantes sont écartées de l'estimation robuste,
  pas de l'évaluation).
"""
from utils import algorithm_a, compute_z_score, compute_en

K_EXPANSION = 2.0


def split_uncertainty(U_xpt):
    """Retourne (u_xpt, U_xpt) à partir de l'incertitude élargie U(x_pt) (k = 2)."""
    if U_xpt is None:
        return None, None
    return U_xpt / K_EXPANSION, U_xpt


def trial_submissions(trial):
    """Toutes les soumissions de l'essai (exclues comprises : elles restent évaluées)."""
    return [f.submission for f in trial.forms if f.submission]


def estimation_values(submissions):
    """Valeurs entrant dans le calcul de x_pt / sigma_pt : résultats non exclus."""
    return [s.value for s in submissions
            if s.value is not None and not getattr(s, "excluded_from_stats", False)]


def assigned_values(trial, submissions, alg_a=None):
    """
    Valeur assignée, écart-type d'aptitude et incertitudes de l'essai.

    Retourne un dict : x_pt, sigma_pt, u_xpt, U_xpt, source_x ('fixe'|'algorithme_a'|None), alg_a, n_used.
    - x_pt / sigma_pt : valeurs de l'essai si renseignées, sinon Algorithme A sur les résultats non exclus.
    - U(x_pt) : celle de l'essai si renseignée. Sinon, repli sur u issue de l'Algorithme A UNIQUEMENT si
      x_pt provient lui-même de l'Algorithme A (ISO 13528 §7 : u(x_pt) doit correspondre à la manière
      dont x_pt a été obtenue). Avec x_pt fixé et sans U(x_pt) : u inconnue -> z simple, En non calculable.
    """
    values = estimation_values(submissions)
    if alg_a is None:
        alg_a = algorithm_a(values) if values else {"x_pt": None, "sigma_pt": None, "u_xpt": None, "U_xpt": None}

    x_pt = trial.target_value if trial.target_value is not None else alg_a["x_pt"]
    sigma_pt = trial.target_sd if trial.target_sd is not None else alg_a["sigma_pt"]

    if trial.reference_uncertainty is not None:
        u_xpt, U_xpt = split_uncertainty(trial.reference_uncertainty)
    elif trial.target_value is None and alg_a.get("u_xpt") is not None:
        u_xpt, U_xpt = alg_a["u_xpt"], K_EXPANSION * alg_a["u_xpt"]
    else:
        u_xpt, U_xpt = None, None

    z_prime = bool(u_xpt is not None and sigma_pt and sigma_pt > 0 and u_xpt > 0.3 * sigma_pt)
    return {
        "x_pt": x_pt, "sigma_pt": sigma_pt, "u_xpt": u_xpt, "U_xpt": U_xpt,
        "z_prime": z_prime, "alg_a": alg_a, "n_used": len(values),
        "source_x": "fixe" if trial.target_value is not None else ("algorithme_a" if x_pt is not None else None),
    }


def score_result(value, lab_U, av):
    """(z, anomalie, En, En conforme) d'un résultat pour les valeurs assignées `av`."""
    z, anomaly = compute_z_score(value, av["x_pt"], av["sigma_pt"], u_xpt=av["u_xpt"])
    en, en_ok = compute_en(value, lab_U, av["x_pt"], av["U_xpt"])
    return z, anomaly, en, en_ok


def fixed_scores(trial, value, lab_U):
    """Scores à la soumission : uniquement si x_pt et sigma_pt sont fixés pour l'essai (sinon None)."""
    if trial.target_value is None or trial.target_sd is None:
        return None, False, None
    z, anomaly = compute_z_score(value, trial.target_value, trial.target_sd,
                                 u_xpt=split_uncertainty(trial.reference_uncertainty)[0])
    en, _ = compute_en(value, lab_U, trial.target_value, trial.reference_uncertainty)
    return z, anomaly, en


def recompute_scores(trial):
    """Recalcule et enregistre z, En et le drapeau d'anomalie de toutes les soumissions de l'essai.
    À appeler (puis commit) avant tout export / publication, et après toute modification des paramètres
    ou des exclusions."""
    if trial.trial_type != "quantitative":
        return 0
    subs = trial_submissions(trial)
    av = assigned_values(trial, subs)
    n = 0
    for s in subs:
        if s.value is None:
            continue
        s.z_score, s.anomaly_flag, s.en_score, _ = score_result(s.value, s.uncertainty, av)
        n += 1
    return n


def recompute_campaign_scores(campaign):
    return sum(recompute_scores(t) for t in campaign.trials)
