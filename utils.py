import math
import json
from datetime import datetime, date, timedelta
from collections import Counter
from flask_login import current_user
from extensions import db
from models import AuditLog, Notification, User, ParticipantForm, Trial


def log_action(action, entity_type=None, entity_id=None, details=None):
    """Enregistre une action dans l'audit trail."""
    try:
        user_id = current_user.id if current_user.is_authenticated else None
    except Exception:
        user_id = None
    entry = AuditLog(
        user_id=user_id,
        action=action,
        entity_type=entity_type,
        entity_id=entity_id,
        details=details,
    )
    db.session.add(entry)
    db.session.commit()


def notify(user_id, message, link=None):
    """Crée une notification pour un utilisateur et envoie un email."""
    n = Notification(user_id=user_id, message=message, link=link)
    db.session.add(n)
    db.session.commit()
    
    # Envoi d'email
    from emails import send_email
    user = User.query.get(user_id)
    if user and user.email:
        subject = "Notification PTMS"
        body = f"Bonjour {user.username},\n\nVous avez une nouvelle notification :\n{message}"
        if link:
            # Note: en prod, on ajouterait le domaine absolu
            body += f"\n\nLien : {link}"
        send_email(user.email, subject, body)
        
    return n


def notify_role(role, message, link=None):
    """Notifie tous les utilisateurs actifs d'un rôle donné."""
    users = User.query.filter_by(role=role, active=True).all()
    for u in users:
        notify(u.id, message, link)


def send_deadline_reminders(days_threshold=5):
    """
    Parcourt les formulaires participants en attente de soumission et envoie
    des notifications/e-mails automatiques de rappel pour les deadlines proches ou dépassées.
    """
    today = date.today()
    pending_forms = ParticipantForm.query.filter(
        ParticipantForm.status.in_(["pending", "draft"])
    ).all()

    reminders_sent = 0
    notified_labs = set()

    for form in pending_forms:
        trial = form.trial
        if not trial or not trial.due_date:
            continue

        days_left = (trial.due_date - today).days

        # Relancer si l'échéance est dans <= days_threshold jours ou dépassée
        if days_left <= days_threshold:
            lab = form.laboratory
            if not lab:
                continue

            due_str = trial.due_date.strftime("%d/%m/%Y")
            if days_left < 0:
                msg = f"⚠️ DEADLINE DÉPASSÉE ({abs(days_left)}j) : L'essai '{trial.name}' ({trial.campaign.reference}) avait pour date limite le {due_str}. Veuillez soumettre vos résultats immédiatement."
            elif days_left == 0:
                msg = f"⏳ RAPPEL DEADLINE AUJOURD'HUI : L'échéance pour l'essai '{trial.name}' ({trial.campaign.reference}) est aujourd'hui ({due_str})."
            else:
                msg = f"🔔 RAPPEL DEADLINE ({days_left}j restants) : Date limite de l'essai '{trial.name}' ({trial.campaign.reference}) fixée au {due_str}."

            link = f"/participants/forms/{form.id}"

            # Notifier les comptes utilisateurs rattachés au laboratoire
            lab_users = User.query.filter_by(laboratory_id=lab.id, active=True).all()
            for u in lab_users:
                notify(u.id, msg, link=link)
                reminders_sent += 1

            notified_labs.add(lab.code)

    # Journaliser l'exécution automatique des relances
    if reminders_sent > 0:
        log_action(
            "deadline_reminders_sent",
            entity_type="Notification",
            details=f"Relances automatiques envoyées à {len(notified_labs)} laboratoires ({reminders_sent} notifications générées).",
        )

    return {
        "reminders_sent": reminders_sent,
        "labs_count": len(notified_labs),
        "labs": list(notified_labs),
    }



# ===========================================================================
# TABLES DE RÉFÉRENCE CONFORMES À LA PROCÉDURE TTS (ANNEXE A1)
# ===========================================================================

# Table de Grubbs (p = 3 à 100) : (1% une valeur, 5% une valeur, 1% deux valeurs, 5% deux valeurs)
GRUBBS_FULL_TABLE = {
    3: (1.155, 1.155, None, None),
    4: (1.496, 1.481, 0.0000, 0.0002),
    5: (1.764, 1.715, 0.0018, 0.0090),
    6: (1.973, 1.887, 0.0116, 0.0349),
    7: (2.139, 2.020, 0.0308, 0.0708),
    8: (2.274, 2.126, 0.0563, 0.1101),
    9: (2.387, 2.215, 0.0851, 0.1492),
    10: (2.482, 2.290, 0.1150, 0.1864),
    11: (2.564, 2.355, 0.1448, 0.2213),
    12: (2.636, 2.412, 0.1738, 0.2537),
    13: (2.699, 2.462, 0.2016, 0.2836),
    14: (2.755, 2.507, 0.2280, 0.3112),
    15: (2.805, 2.549, 0.2530, 0.3367),
    16: (2.852, 2.585, 0.2767, 0.3603),
    17: (2.894, 2.620, 0.2990, 0.3822),
    18: (2.932, 2.651, 0.3200, 0.4025),
    19: (2.968, 2.681, 0.3398, 0.4214),
    20: (3.001, 2.709, 0.3585, 0.4391),
    21: (3.031, 2.733, 0.3761, 0.4556),
    22: (3.060, 2.758, 0.3927, 0.4711),
    23: (3.087, 2.781, 0.4085, 0.4857),
    24: (3.112, 2.802, 0.4234, 0.4994),
    25: (3.135, 2.822, 0.4376, 0.5123),
    26: (3.157, 2.841, 0.4510, 0.5245),
    27: (3.178, 2.859, 0.4638, 0.5360),
    28: (3.199, 2.876, 0.4759, 0.5470),
    29: (3.218, 2.893, 0.4875, 0.5574),
    30: (3.236, 2.908, 0.4985, 0.5672),
    31: (3.253, 2.924, 0.5091, 0.5766),
    32: (3.270, 2.938, 0.5192, 0.5856),
    33: (3.286, 2.952, 0.5288, 0.5941),
    34: (3.301, 2.965, 0.5381, 0.6023),
    35: (3.316, 2.979, 0.5469, 0.6101),
    36: (3.330, 2.991, 0.5554, 0.6175),
    37: (3.343, 3.003, 0.5636, 0.6247),
    38: (3.356, 3.014, 0.5714, 0.6316),
    39: (3.369, 3.025, 0.7890, 0.6382),
    40: (3.381, 3.036, 0.5862, 0.6445),
    41: (3.393, 3.046, 0.5932, 0.6506),
    42: (3.404, 3.057, 0.5999, 0.6565),
    43: (3.415, 3.067, 0.6064, 0.6621),
    44: (3.425, 3.075, 0.6127, 0.6676),
    45: (3.435, 3.085, 0.6188, 0.6728),
    46: (3.445, 3.094, 0.6246, 0.6779),
    47: (3.455, 3.103, 0.6303, 0.6828),
    48: (3.464, 3.111, 0.6358, 0.6876),
    49: (3.474, 3.120, 0.6411, 0.6921),
    50: (3.483, 3.128, 0.6462, 0.6966),
    51: (3.491, 3.136, 0.6512, 0.7009),
    52: (3.500, 3.143, 0.6560, 0.7051),
    53: (3.507, 3.151, 0.6607, 0.7091),
    54: (3.516, 3.158, 0.6653, 0.7130),
    55: (3.524, 3.166, 0.6697, 0.7168),
    56: (3.531, 3.172, 0.6740, 0.7205),
    57: (3.539, 3.180, 0.6782, 0.7241),
    58: (3.546, 3.186, 0.6823, 0.7276),
    59: (3.553, 3.193, 0.6862, 0.7310),
    60: (3.560, 3.199, 0.6901, 0.7343),
    61: (3.566, 3.205, 0.6938, 0.7375),
    62: (3.573, 3.212, 0.6975, 0.7406),
    63: (3.579, 3.218, 0.7010, 0.7437),
    64: (3.586, 3.224, 0.7045, 0.7467),
    65: (3.592, 3.230, 0.7079, 0.7496),
    66: (3.598, 3.235, 0.7112, 0.7524),
    67: (3.605, 3.241, 0.7144, 0.7551),
    68: (3.610, 3.246, 0.7175, 0.7578),
    69: (3.617, 3.252, 0.7206, 0.7604),
    70: (3.622, 3.257, 0.7236, 0.7630),
    71: (3.627, 3.262, 0.7265, 0.7655),
    72: (3.633, 3.267, 0.7294, 0.7679),
    73: (3.638, 3.272, 0.7322, 0.7703),
    74: (3.643, 3.278, 0.7349, 0.7727),
    75: (3.648, 3.282, 0.7376, 0.7749),
    76: (3.654, 3.287, 0.7402, 0.7772),
    77: (3.658, 3.291, 0.7427, 0.7794),
    78: (3.663, 3.297, 0.7453, 0.7815),
    79: (3.669, 3.301, 0.7477, 0.7836),
    80: (3.673, 3.305, 0.7501, 0.7856),
    81: (3.677, 3.309, 0.7525, 0.7876),
    82: (3.682, 3.315, 0.7548, 0.7896),
    83: (3.687, 3.319, 0.7570, 0.7915),
    84: (3.691, 3.323, 0.7592, 0.7934),
    85: (3.695, 3.327, 0.7614, 0.7953),
    86: (3.699, 3.331, 0.7635, 0.7971),
    87: (3.704, 3.335, 0.7656, 0.7989),
    88: (3.708, 3.339, 0.7670, 0.8006),
    89: (3.712, 3.343, 0.7697, 0.8023),
    90: (3.716, 3.347, 0.7717, 0.8040),
    91: (3.720, 3.350, 0.7736, 0.8057),
    92: (3.725, 3.355, 0.7755, 0.8073),
    93: (3.728, 3.358, 0.7774, 0.8089),
    94: (3.732, 3.362, 0.7792, 0.8104),
    95: (3.736, 3.365, 0.7810, 0.8120),
    96: (3.739, 3.369, 0.7828, 0.8135),
    97: (3.744, 3.372, 0.7845, 0.8149),
    98: (3.747, 3.377, 0.7862, 0.8164),
    99: (3.750, 3.380, None, None),
    100: (3.754, 3.383, None, None),
}

# Table Kolmogorov-Smirnov (alpha=0.05) pour n <= 50
KS_CRITICAL_05 = {
    1: 0.975, 2: 0.842, 3: 0.708, 4: 0.624, 5: 0.565, 6: 0.521, 7: 0.486, 8: 0.457,
    9: 0.432, 10: 0.410, 11: 0.391, 12: 0.375, 13: 0.361, 14: 0.349, 15: 0.338,
    16: 0.328, 17: 0.318, 18: 0.309, 19: 0.301, 20: 0.294, 25: 0.270, 30: 0.240,
    35: 0.230, 40: 0.210, 45: 0.200, 50: 0.190,
}

# Facteurs Algorithme S (ISO 13528 §C.3.2 / Procédure TTS §5.2)
ALGORITHM_S_PARAMS = {
    1: (1.645, 1.097),
    2: (1.517, 1.054),
    3: (1.444, 1.039),
    4: (1.395, 1.032),
    5: (1.359, 1.027),
    6: (1.332, 1.024),
    7: (1.310, 1.021),
    8: (1.292, 1.019),
    9: (1.277, 1.018),
    10: (1.264, 1.017),
}


# ===========================================================================
# FONCTIONS STATISTIQUES AUXILIAIRES
# ===========================================================================

def compute_median(vals):
    """Calcule la médiane d'une liste de nombres."""
    if not vals:
        return None
    s = sorted(vals)
    n = len(s)
    mid = n // 2
    if n % 2 == 0:
        return (s[mid - 1] + s[mid]) / 2.0
    return float(s[mid])


def compute_quantile(vals, q):
    """Calcule le quantile d'ordre q (0 à 1) par interpolation linéaire."""
    if not vals:
        return None
    s = sorted(vals)
    n = len(s)
    if n == 1:
        return float(s[0])
    pos = q * (n - 1)
    idx = int(pos)
    frac = pos - idx
    if idx >= n - 1:
        return float(s[-1])
    return s[idx] + frac * (s[idx + 1] - s[idx])


def compute_erf_inv(x):
    """Approximation de la fonction d'erreur inverse (Winitzki)."""
    if x <= -1.0 or x >= 1.0:
        return float('nan')
    a = 0.147
    sgn = 1.0 if x >= 0 else -1.0
    log_term = math.log(1.0 - x * x)
    term1 = 2.0 / (math.pi * a) + log_term / 2.0
    val = math.sqrt(term1 * term1 - log_term / a) - term1
    return sgn * math.sqrt(val)


def normit_quantile(p):
    """Quantile de la loi normale standard Phi^-1(p)."""
    p = max(1e-9, min(1.0 - 1e-9, p))
    return math.sqrt(2.0) * compute_erf_inv(2.0 * p - 1.0)


def normal_cdf(x):
    """Fonction de répartition de la loi normale standard Phi(x)."""
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


# ===========================================================================
# ESTIMATEURS DE VALEUR ASSIGNÉE (x_pt) ET ÉCART-TYPE (sigma_pt)
# ===========================================================================

def algorithm_a(values, tol=1e-6, max_iter=100):
    """
    Algorithme A conforme à ISO 13528 §C.3.1 et Procédure TTS §5.1.3.1.
    Calcul robuste de la moyenne (x*) et de l'écart-type (s*).

    Remarques & règles d'application TTS :
    1. Si MADe == 0, remplacer MADe (1.483 * med|x_i - x*|) par med|x_i - mean(x)|.
    2. Si le pourcentage de valeurs aberrantes > 20%, avertissement + recommandation Médiane.
    3. Critère d'arrêt : pas de changement du 3ème chiffre significatif.
    """
    if not values or len(values) < 2:
        return {
            "x_pt": values[0] if values else None,
            "sigma_pt": None,
            "u_xpt": None,
            "U_xpt": None,
            "iterations": 0,
            "made": None,
            "high_outliers_warning": False,
        }

    p = len(values)
    med = compute_median(values)

    # 1. Calcul du MADe initial
    abs_devs = [abs(v - med) for v in values]
    made = 1.483 * compute_median(abs_devs)

    if made == 0:
        mean_val = sum(values) / p
        abs_devs_mean = [abs(v - mean_val) for v in values]
        made = compute_median(abs_devs_mean)

    # Détection si > 20% aberrants (selon écarts à la médiane)
    q1 = compute_quantile(values, 0.25)
    q3 = compute_quantile(values, 0.75)
    iqr = q3 - q1
    outlier_count = sum(1 for v in values if v < q1 - 1.5 * iqr or v > q3 + 1.5 * iqr)
    high_outliers_warning = (outlier_count / p) > 0.20

    # 2. Boucle itérative Algorithme A
    x_star = med
    s_star = made if made > 0 else (std_dev(values) or 1e-6)

    iterations = 0
    for iter_idx in range(1, max_iter + 1):
        iterations = iter_idx
        delta = 1.5 * s_star
        x_win = [max(x_star - delta, min(x_star + delta, v)) for v in values]
        x_new = sum(x_win) / p
        variance = sum((w - x_new) ** 2 for w in x_win) / (p - 1)
        s_new = 1.134 * math.sqrt(variance)

        # Critère de convergence (3ème chiffre significatif)
        if abs(x_new - x_star) < tol and abs(s_new - s_star) < tol:
            x_star = x_new
            s_star = s_new
            break
        x_star = x_new
        s_star = s_new

    u_xpt = 1.25 * s_star / math.sqrt(p) if p > 0 else None
    U_xpt = 2.0 * u_xpt if u_xpt is not None else None

    return {
        "x_pt": round(x_star, 5),
        "sigma_pt": round(s_star, 5),
        "u_xpt": round(u_xpt, 5) if u_xpt else None,
        "U_xpt": round(U_xpt, 5) if U_xpt else None,
        "iterations": iterations,
        "made": round(made, 5),
        "high_outliers_warning": high_outliers_warning,
    }


def median_made_niqr(values):
    """
    Médiane, MADe et nIQR selon Procédure TTS §5.1.3.2.
    - med(x) = médiane
    - MADe = 1.483 * med(|x_i - med(x)|)
    - Si MADe == 0 (50%+ résultats identiques), passage à nIQR = 0.7413 * (Q3 - Q1).
    """
    if not values:
        return {"median": None, "made": None, "niqr": None, "sigma_pt": None, "u_xpt": None, "U_xpt": None}
    p = len(values)
    med = compute_median(values)
    abs_devs = [abs(v - med) for v in values]
    made = 1.483 * compute_median(abs_devs)

    q1 = compute_quantile(values, 0.25)
    q3 = compute_quantile(values, 0.75)
    niqr = 0.7413 * (q3 - q1)

    sigma_pt = made if made > 0 else niqr
    u_xpt = 1.25 * sigma_pt / math.sqrt(p) if sigma_pt and p > 0 else None
    U_xpt = 2.0 * u_xpt if u_xpt else None

    return {
        "median": round(med, 5),
        "made": round(made, 5),
        "niqr": round(niqr, 5),
        "sigma_pt": round(sigma_pt, 5) if sigma_pt else None,
        "u_xpt": round(u_xpt, 5) if u_xpt else None,
        "U_xpt": round(U_xpt, 5) if U_xpt else None,
    }


def m_estimator_logistic(values, tol=1e-6, max_iter=100):
    """
    M-Estimateur Logistique selon Procédure TTS §5.1.3.2 (M_estimateur_logistique_total).
    Estimation robuste de la moyenne (mu_hat) et de l'échelle robuste (S) avec fonction d'influence logistique.
    sigma_robuste = S / 1.29.
    """
    if not values:
        return {"mu_hat": None, "S": None, "sigma_robuste": None, "iterations": 0, "weights": [], "residuals": []}

    p = len(values)
    mu_hat = compute_median(values)
    abs_devs = [abs(v - mu_hat) for v in values]
    S = 1.4826 * compute_median(abs_devs)
    if S == 0:
        S = 1e-6

    weights = []
    residuals = []
    iterations = 0

    for iter_idx in range(1, max_iter + 1):
        iterations = iter_idx
        residuals = [(v - mu_hat) / S for v in values]
        # Poids logistiques w = 1 / (cosh(r))^2
        weights = [1.0 / (math.cosh(r) ** 2) for r in residuals]

        sum_w = sum(weights)
        if sum_w == 0:
            break
        mu_new = sum(w * v for w, v in zip(weights, values)) / sum_w
        abs_devs_new = [abs(v - mu_new) for v in values]
        S_new = 1.4826 * compute_median(abs_devs_new)
        if S_new == 0:
            S_new = 1e-6

        if abs(mu_new - mu_hat) < tol:
            mu_hat = mu_new
            S = S_new
            break
        mu_hat = mu_new
        S = S_new

    sigma_robuste = S / 1.29
    u_xpt = 1.25 * sigma_robuste / math.sqrt(p) if p > 0 else None
    U_xpt = 2.0 * u_xpt if u_xpt else None

    return {
        "mu_hat": round(mu_hat, 5),
        "S": round(S, 5),
        "sigma_robuste": round(sigma_robuste, 5),
        "u_xpt": round(u_xpt, 5) if u_xpt else None,
        "U_xpt": round(U_xpt, 5) if U_xpt else None,
        "iterations": iterations,
        "weights": [round(w, 4) for w in weights],
        "residuals": [round(r, 4) for r in residuals],
    }


def classical_stats(values):
    """Moyenne et écart-type classique."""
    if not values:
        return {"mean": None, "sd": None, "u_xpt": None, "U_xpt": None}
    p = len(values)
    mean_val = sum(values) / p
    sd_val = std_dev(values)
    u_xpt = (sd_val / math.sqrt(p)) if sd_val and p > 0 else None
    U_xpt = 2.0 * u_xpt if u_xpt else None
    return {
        "mean": round(mean_val, 5),
        "sd": round(sd_val, 5) if sd_val is not None else None,
        "u_xpt": round(u_xpt, 5) if u_xpt else None,
        "U_xpt": round(U_xpt, 5) if U_xpt else None,
    }


def std_dev(values):
    """Écart-type échantillon (n-1)."""
    if not values or len(values) < 2:
        return 0.0
    p = len(values)
    mean_val = sum(values) / p
    var = sum((v - mean_val) ** 2 for v in values) / (p - 1)
    return math.sqrt(var)


# ===========================================================================
# ALGORITHME S : ÉCART-TYPE DE RÉPÉTABILITÉ (PROCÉDURE TTS §5.2)
# ===========================================================================

def algorithm_s(w_values, nu=1, max_iter=100, tol=1e-6):
    """
    Algorithme S conforme à ISO 13528 §C.3.2 et Procédure TTS §5.2.
    Fournit une estimation robuste de l'écart-type de répétabilité s_r = w*.
    w_values : liste des écarts-types ou étendues des réplicats de chaque laboratoire.
    nu : degrés de liberté (nu = m-1 pour un écart-type de m répétitions, nu = 1 pour une étendue).
    """
    clean_w = [w for w in w_values if w is not None]
    if not clean_w:
        return {"s_r": None, "iterations": 0}

    p = len(clean_w)
    eta, xi = ALGORITHM_S_PARAMS.get(min(max(1, nu), 10), (1.645, 1.097))

    # Si la moitié des valeurs de w sont nulles, éliminer les zéro/points aberrants
    zero_count = sum(1 for w in clean_w if w == 0)
    if zero_count >= p / 2.0:
        filtered_w = [w for w in clean_w if w > 0]
        if filtered_w:
            clean_w = filtered_w
            p = len(clean_w)

    w_star = compute_median(clean_w)
    if w_star == 0:
        w_star = 1e-6

    iterations = 0
    for iter_idx in range(1, max_iter + 1):
        iterations = iter_idx
        psi = eta * w_star
        w_star_i = [psi if w > psi else w for w in clean_w]
        w_new = xi * math.sqrt(sum(w ** 2 for w in w_star_i) / p)

        if abs(w_new - w_star) < tol:
            w_star = w_new
            break
        w_star = w_new

    return {
        "s_r": round(w_star, 5),
        "iterations": iterations,
        "nu": nu,
        "eta": eta,
        "xi": xi,
    }


# ===========================================================================
# MODÈLE DE HORWITZ MODIFIÉ PAR THOMPSON (PROCÉDURE TTS TABLEAU 2)
# ===========================================================================

def horwitz_thompson_sd(concentration_mass_fraction, target_value=None):
    """
    Modèle empirique de Horwitz modifié par Thompson (Procédure TTS §5.1.3 et Tableau 2) :
    - C < 1.2e-7           => sigma_R = 0.22 * C
    - 1.2e-7 <= C <= 0.138 => sigma_R = 0.22 * C^0.8495
    - C > 0.138            => sigma_R = 0.02 * sqrt(C)
    """
    if concentration_mass_fraction is None or concentration_mass_fraction <= 0:
        return None
    C = float(concentration_mass_fraction)
    if C < 1.2e-7:
        rsd = 0.22
    elif C <= 0.138:
        rsd = 0.22 * (C ** (0.8495 - 1.0))
    else:
        rsd = 0.02 * (C ** (-0.5))

    sigma_pt = (target_value * rsd) if target_value else (C * rsd)
    return round(sigma_pt, 6)


def horwitz_rsd(concentration_mass_fraction):
    """Équation classique de Horwitz RSD_H (%) = 2^(1 - 0.5*log10(C))."""
    if not concentration_mass_fraction or concentration_mass_fraction <= 0:
        return None
    rsd_pct = 2 ** (1 - 0.5 * math.log10(concentration_mass_fraction))
    return round(rsd_pct, 3)


# ===========================================================================
# ÉVALUATION DE LA PERFORMANCE (Z-SCORE, Z'-SCORE, EN)
# ===========================================================================

def compute_z_score(value, target_value, target_sd, u_xpt=None):
    """
    Calcule le z-score ou z'-score selon ISO 13528 §9.2-9.4 / Procédure TTS §5.1.4.
    Si u(x_pt) >= 0.3 * sigma_pt ou si u_xpt est fourni et significatif, z'-score est utilisé :
        z' = (x_i - X) / sqrt(sigma_pt^2 + u(x_pt)^2)
    """
    if value is None or target_value is None or not target_sd or target_sd <= 0:
        return None, False

    denom = target_sd
    use_z_prime = False
    if u_xpt is not None and u_xpt >= 0.3 * target_sd:
        denom = math.sqrt(target_sd ** 2 + u_xpt ** 2)
        use_z_prime = True

    score = (value - target_value) / denom
    anomaly = abs(score) >= 3.0
    return round(score, 3), anomaly


def z_score_interpretation(z):
    """Interprétation conventionnelle des z-scores / z'-scores ISO 13528 / TTS."""
    if z is None:
        return "Non calculable", "secondary"
    az = abs(z)
    if az <= 2.0:
        return "Satisfaisant", "success"
    elif az < 3.0:
        return "Douteux", "warning"
    else:
        return "Non satisfaisant", "danger"


def compute_en(value, lab_uncertainty, target_value, reference_uncertainty):
    """
    Calcule le nombre En (normalized error) ISO 13528 §9.3 / Procédure TTS §5.1.4 / CIL MRC :
        En = (x_i - x_pt) / sqrt(U(x_i)^2 + U(x_pt)^2)
    Satisfaisant si |En| < 1.0, non satisfaisant si |En| >= 1.0.
    """
    if None in (value, target_value) or reference_uncertainty is None:
        return None, None
    u_lab = lab_uncertainty if lab_uncertainty is not None else 0.0
    denom = math.sqrt(u_lab ** 2 + reference_uncertainty ** 2)
    if denom == 0:
        return None, None
    en = (value - target_value) / denom
    return round(en, 3), abs(en) < 1.0


def robust_stats(values):
    """Compatibilité ascendante : médiane et MAD normalisé (1.4826)."""
    res = median_made_niqr(values)
    return res["median"], res["made"]


# ===========================================================================
# ANALYSE DES DONNÉES, NORMALITÉ ET VALEURS ABERRANTES (ANNEXE A1)
# ===========================================================================

def grubbs_test(values):
    """
    Test de Grubbs simple et double conforme à la Procédure TTS §A1.4 et table complète.
    Règles de décision :
    - G <= C_crit(5%) : Valeur correcte
    - C_crit(5%) < G <= C_crit(1%) : Valeur isolée (*)
    - G > C_crit(1%) : Valeur aberrante (**)
    """
    p = len(values)
    if p < 3:
        return {
            "outlier_value": None,
            "G_min": None,
            "G_max": None,
            "G": None,
            "G_critical_5": None,
            "G_critical_1": None,
            "status": "Insuffisant",
            "outliers_list": [],
            "double_grubbs": None,
        }

    mean_val = sum(values) / p
    sd_val = std_dev(values)
    if sd_val == 0:
        return {
            "outlier_value": None,
            "G": 0.0,
            "G_critical_5": None,
            "G_critical_1": None,
            "status": "Homogène",
            "outliers_list": [],
            "double_grubbs": None,
        }

    min_val = min(values)
    max_val = max(values)

    g_1 = (mean_val - min_val) / sd_val
    g_p = (max_val - mean_val) / sd_val

    g_max = max(g_1, g_p)
    candidate = min_val if g_1 > g_p else max_val

    c1, c5, dc1, dc5 = GRUBBS_FULL_TABLE.get(p, (GRUBBS_FULL_TABLE[100]))

    outlier = None
    outliers_list = []
    status = "Correct"

    if g_max > c1:
        outlier = candidate
        outliers_list.append(candidate)
        status = "Valeur aberrante (**)"
    elif g_max > c5:
        outlier = candidate
        outliers_list.append(candidate)
        status = "Valeur isolée (*)"

    # Test de Grubbs double (deux plus grandes ou deux plus petites)
    s0_sq = sum((v - mean_val) ** 2 for v in values)
    sorted_vals = sorted(values)

    # Deux plus grandes
    x_bar_top2 = sum(sorted_vals[:-2]) / (p - 2)
    s_top2_sq = sum((v - x_bar_top2) ** 2 for v in sorted_vals[:-2])
    g_double_top = s_top2_sq / s0_sq if s0_sq > 0 else 1.0

    # Deux plus petites
    x_bar_bot2 = sum(sorted_vals[2:]) / (p - 2)
    s_bot2_sq = sum((v - x_bar_bot2) ** 2 for v in sorted_vals[2:])
    g_double_bot = s_bot2_sq / s0_sq if s0_sq > 0 else 1.0

    double_outlier = None
    if dc5 is not None:
        if g_double_top < dc1 or g_double_bot < dc1:
            double_outlier = "Paire d'aberrantes (**)"
        elif g_double_top < dc5 or g_double_bot < dc5:
            double_outlier = "Paire d'isolées (*)"

    return {
        "outlier_value": outlier,
        "G": round(g_max, 3),
        "G_critical_5": c5,
        "G_critical_1": c1,
        "status": status,
        "outliers_list": outliers_list,
        "double_grubbs": {
            "G_top": round(g_double_top, 4),
            "G_bot": round(g_double_bot, 4),
            "crit_5": dc5,
            "crit_1": dc1,
            "status": double_outlier,
        },
    }


def droite_henry(values):
    """
    Droite d'Henry conforme à la Procédure TTS §A1.1.
    Calcul des effectifs cumulés (RANG), fréquences cumulées, normits (LOI.NORMALE.STANDARD.INVERSE) et R^2.
    Règle de décision : Si R^2 > 0.7, corrélation acceptable et distribution normale.
    """
    if not values or len(values) < 3:
        return {"r_squared": None, "slope": None, "intercept": None, "normal_conform": False, "points": []}

    sorted_vals = sorted(values)
    p = len(sorted_vals)

    points = []
    normits = []

    for i, x in enumerate(sorted_vals, 1):
        freq_cumul = i / (p + 1.0)
        normit = normit_quantile(freq_cumul)
        normits.append(normit)
        points.append({"x": round(x, 4), "freq": round(freq_cumul, 4), "normit": round(normit, 4)})

    mean_x = sum(sorted_vals) / p
    mean_y = sum(normits) / p

    num = sum((x - mean_x) * (y - mean_y) for x, y in zip(sorted_vals, normits))
    den_x = sum((x - mean_x) ** 2 for x in sorted_vals)
    den_y = sum((y - mean_y) ** 2 for y in normits)

    if den_x == 0 or den_y == 0:
        return {"r_squared": 0.0, "slope": 0.0, "intercept": 0.0, "normal_conform": False, "points": points}

    slope = num / den_x
    intercept = mean_y - slope * mean_x
    r_squared = (num ** 2) / (den_x * den_y)

    return {
        "r_squared": round(r_squared, 4),
        "slope": round(slope, 5),
        "intercept": round(intercept, 5),
        "normal_conform": r_squared > 0.7,
        "points": points,
    }


def boxplot_stats(values):
    """
    Boîte à moustache conforme à la Procédure TTS §A1.2.
    Q1, Médiane, Q3, IQR, Moustaches, et détection des valeurs aberrantes hors moustaches.
    """
    if not values:
        return {"q1": None, "median": None, "q3": None, "iqr": None, "lower_fence": None, "upper_fence": None, "outliers": []}

    q1 = compute_quantile(values, 0.25)
    med = compute_median(values)
    q3 = compute_quantile(values, 0.75)
    iqr = q3 - q1

    lower_fence = q1 - 1.5 * iqr
    upper_fence = q3 + 1.5 * iqr

    outliers = [v for v in values if v < lower_fence or v > upper_fence]

    return {
        "q1": round(q1, 4),
        "median": round(med, 4),
        "q3": round(q3, 4),
        "iqr": round(iqr, 4),
        "lower_fence": round(lower_fence, 4),
        "upper_fence": round(upper_fence, 4),
        "outliers": outliers,
    }


def kolmogorov_smirnov_test(values):
    """
    Test de Kolmogorov-Smirnov conforme à la Procédure TTS §A1.6.
    Compare la distribution observée à la distribution théorique normale.
    Hypothèse H0 acceptée si D_max <= D_KS.
    """
    p = len(values)
    if p < 3:
        return {"d_max": None, "d_critical": None, "is_normal": False}

    sorted_vals = sorted(values)
    mean_val = sum(values) / p
    sd_val = std_dev(values)

    if sd_val == 0:
        return {"d_max": 0.0, "d_critical": 1.0, "is_normal": True}

    d_max = 0.0
    for i, x in enumerate(sorted_vals, 1):
        f_obs = i / float(p)
        f_theo = normal_cdf((x - mean_val) / sd_val)
        d = abs(f_obs - f_theo)
        if d > d_max:
            d_max = d

    if p <= 50:
        d_crit = KS_CRITICAL_05.get(p, 1.36 / math.sqrt(p))
    else:
        d_crit = 1.36 / math.sqrt(p)

    return {
        "d_max": round(d_max, 4),
        "d_critical": round(d_crit, 4),
        "is_normal": d_max <= d_crit,
    }


def kurtosis_and_skewness(values):
    """
    Kurtosis (aplatissement) et Skewness (asymétrie) selon Procédure TTS §A1.7 et §A1.8.
    - K = sum((x_i - mean)^4) / (n * sigma^4). K=3 (normale), K>3 (platokurtique), K<3 (leptokurtique).
    - Skewness = sum((x_i - mean)^3) / (n * sigma^3).
    """
    p = len(values)
    if p < 3:
        return {"kurtosis": None, "skewness": None, "kurtosis_type": "Insuffisant", "skewness_type": "Insuffisant"}

    mean_val = sum(values) / p
    sd_val = std_dev(values)

    if sd_val == 0:
        return {"kurtosis": 3.0, "skewness": 0.0, "kurtosis_type": "Normale", "skewness_type": "Symétrique"}

    m4 = sum((v - mean_val) ** 4 for v in values) / p
    m3 = sum((v - mean_val) ** 3 for v in values) / p

    kurt = m4 / (sd_val ** 4)
    skew = m3 / (sd_val ** 3)

    if abs(kurt - 3.0) < 0.2:
        k_type = "Normale (Mésokurtique)"
    elif kurt > 3.2:
        k_type = "Platokurtique (homogène / piquée)"
    else:
        k_type = "Leptokurtique (hétérogène / aplatie)"

    if abs(skew) < 0.1:
        s_type = "Symétrique (Normale)"
    elif skew > 0:
        s_type = "Asymétrie positive (décalée à droite)"
    else:
        s_type = "Asymétrie négative (décalée à gauche)"

    return {
        "kurtosis": round(kurt, 4),
        "skewness": round(skew, 4),
        "kurtosis_type": k_type,
        "skewness_type": s_type,
    }


def kernel_density_estimation(values, num_points=40):
    """
    Diagramme de densité par la méthode du noyau (Procédure TTS §A1.3).
    Génère les coordonnées (x, y) de la densité pour tracé dynamique.
    """
    if not values or len(values) < 2:
        return []

    p = len(values)
    sd_val = std_dev(values)
    if sd_val == 0:
        sd_val = 1e-4

    # Règle de Scott pour le h (largeur de bande)
    h = 1.06 * sd_val * (p ** (-0.2))

    min_x = min(values) - 3 * h
    max_x = max(values) + 3 * h
    step = (max_x - min_x) / (num_points - 1)

    points = []
    for i in range(num_points):
        x = min_x + i * step
        # Noyau gaussien
        density = sum(math.exp(-0.5 * (((x - v) / h) ** 2)) for v in values) / (p * h * math.sqrt(2 * math.pi))
        points.append({"x": round(x, 4), "y": round(density, 6)})

    return points


# ===========================================================================
# UTILITAIRES DE SYNTHÈSE & QUALITATIF
# ===========================================================================

def qualitative_consensus(results):
    """Résultat consensuel (mode) pour données qualitatives."""
    cleaned = [r for r in results if r]
    if not cleaned:
        return None, 0.0, {}
    counts = Counter(cleaned)
    consensus, nb = counts.most_common(1)[0]
    agreement_rate = round(nb / len(cleaned) * 100, 1)
    return consensus, agreement_rate, dict(counts)


# ---------------------------------------------------------------------------
# Solidité des couleurs — 9 paramètres
# ---------------------------------------------------------------------------
SOLIDITE_PARAMS = [
    {"key": "couleur", "label": "Dégradation de couleur"},
    {"key": "CA", "label": "Dégorgement — Acétate (CA)"},
    {"key": "CO", "label": "Dégorgement — Coton (CO)"},
    {"key": "PA", "label": "Dégorgement — Polyamide (PA)"},
    {"key": "PES", "label": "Dégorgement — Polyester (PES)"},
    {"key": "PAN", "label": "Dégorgement — Polyacrylique (PAN)"},
    {"key": "WO", "label": "Dégorgement — Laine (WO)"},
    {"key": "CTA", "label": "Dégorgement — Triacétate (CTA)"},
    {"key": "CV", "label": "Dégorgement — Viscose (CV)"},
]


def parse_solidite_data(raw_json):
    if not raw_json:
        return {}
    try:
        return json.loads(raw_json)
    except (ValueError, TypeError):
        return {}


def compute_homogeneity(sample_pairs, sigma_pt):
    pairs = [(p[0], p[1]) for p in sample_pairs if p[0] is not None and p[1] is not None]
    g = len(pairs)
    if g < 2:
        return {"s_within": None, "s_between": None, "criterion": None, "conform": None, "g": g, "grand_mean": None}

    means = [(x1 + x2) / 2 for x1, x2 in pairs]
    grand_mean = sum(means) / g
    ss_within = sum((x1 - x2) ** 2 for x1, x2 in pairs) / 2.0
    ms_within = ss_within / g

    ss_between = sum(2 * (m - grand_mean) ** 2 for m in means)
    ms_between = ss_between / (g - 1)

    s_within = ms_within ** 0.5
    variance_between = (ms_between - ms_within) / 2.0
    s_between = variance_between ** 0.5 if variance_between > 0 else 0.0

    criterion = 0.3 * sigma_pt if sigma_pt else None
    conform = (s_between <= criterion) if criterion is not None else None

    return {
        "s_within": round(s_within, 5),
        "s_between": round(s_between, 5),
        "criterion": round(criterion, 5) if criterion is not None else None,
        "conform": conform,
        "g": g,
        "grand_mean": round(grand_mean, 5),
    }


def compute_stability(first_value, last_value, sigma_pt):
    if first_value is None or last_value is None or not sigma_pt:
        return {"criterion": None, "conform": None, "difference": None}
    difference = abs(last_value - first_value)
    criterion = 0.3 * sigma_pt
    return {"criterion": round(criterion, 5), "conform": difference <= criterion, "difference": round(difference, 5)}


# ===========================================================================
# NOUVELLES FONCTIONS STATISTIQUES AVANCÉES
# ===========================================================================

def compute_cohen_kappa(results_list):
    """
    Coefficient Kappa de Cohen pour essais qualitatifs (ISO 13528 §9.5 / ISO 17043).
    Mesure l'accord inter-laboratoires au-delà du hasard.
    kappa = (P_o - P_e) / (1 - P_e)
    Interprétation :
        kappa >= 0.81 → Accord quasi-parfait
        0.61-0.80    → Accord fort
        0.41-0.60    → Accord modéré
        0.21-0.40    → Accord faible
        <= 0.20      → Accord très faible / aléatoire
    """
    cleaned = [r.strip().lower() for r in results_list if r]
    n = len(cleaned)
    if n < 2:
        return {
            "kappa": None, "interpretation": "Données insuffisantes",
            "badge": "secondary", "p_observed": None, "p_expected": None,
        }

    counts = Counter(cleaned)
    categories = list(counts.keys())
    total = n

    # P_o : proportion observée de l'accord (tous d'accord avec le consensus)
    consensus = counts.most_common(1)[0][0]
    p_observed = counts[consensus] / total

    # P_e : proportion d'accord attendue par hasard
    p_expected = sum((count / total) ** 2 for count in counts.values())

    if p_expected >= 1.0:
        kappa = 1.0
    else:
        kappa = (p_observed - p_expected) / (1.0 - p_expected)

    kappa = round(kappa, 4)

    if kappa >= 0.81:
        interp, badge = "Accord quasi-parfait", "success"
    elif kappa >= 0.61:
        interp, badge = "Accord fort", "success"
    elif kappa >= 0.41:
        interp, badge = "Accord modéré", "info"
    elif kappa >= 0.21:
        interp, badge = "Accord faible", "warning"
    else:
        interp, badge = "Accord très faible / aléatoire", "danger"

    return {
        "kappa": kappa,
        "interpretation": interp,
        "badge": badge,
        "p_observed": round(p_observed, 4),
        "p_expected": round(p_expected, 4),
        "n": n,
        "categories": {k: v for k, v in counts.items()},
        "consensus": consensus,
    }


def qualitative_weighted_agreement(results, ordered_categories=None):
    """
    Taux d'accord pondéré pour essais qualitatifs à échelle ordinale.
    Si les résultats sont des notes (ex: 1, 2, 3, 4, 5 pour solidité),
    pondère le désaccord selon la distance ordinale entre réponses.
    Retourne : accord_exact (%), accord_pondéré (%), écart_moyen
    """
    cleaned = [str(r).strip() for r in results if r is not None]
    n = len(cleaned)
    if n < 2:
        return {"exact_agreement": 0.0, "weighted_agreement": None, "mean_deviation": None}

    counts = Counter(cleaned)
    consensus = counts.most_common(1)[0][0]
    exact_agreement = round(counts[consensus] / n * 100, 1)

    # Si catégories ordonnées fournies, calculer la distance moyenne
    if ordered_categories:
        try:
            positions = {str(c): i for i, c in enumerate(ordered_categories)}
            consensus_pos = positions.get(consensus, 0)
            deviations = []
            for r in cleaned:
                pos = positions.get(r, 0)
                deviations.append(abs(pos - consensus_pos))
            mean_deviation = round(sum(deviations) / n, 3)
            max_possible = len(ordered_categories) - 1
            weighted_agreement = round((1 - mean_deviation / max_possible) * 100, 1) if max_possible > 0 else exact_agreement
        except Exception:
            mean_deviation = None
            weighted_agreement = None
    else:
        mean_deviation = None
        weighted_agreement = None

    return {
        "exact_agreement": exact_agreement,
        "weighted_agreement": weighted_agreement,
        "mean_deviation": mean_deviation,
        "consensus": consensus,
        "n": n,
    }


def solidite_matrix_stats(submissions, solidite_params=None):
    """
    Analyse matricielle des résultats de solidité des couleurs (EN ISO 105).
    Pour chaque paramètre de solidité (couleur, CA, CO, PA, PES, PAN, WO, CTA, CV),
    calcule : distribution des notes, note consensuelle (médiane/mode), taux de conformité.
    Retourne une structure matricielle pour heatmap.
    """
    if solidite_params is None:
        solidite_params = [
            {"key": "couleur", "label": "Dégradation couleur"},
            {"key": "CA", "label": "Acétate (CA)"},
            {"key": "CO", "label": "Coton (CO)"},
            {"key": "PA", "label": "Polyamide (PA)"},
            {"key": "PES", "label": "Polyester (PES)"},
            {"key": "PAN", "label": "Polyacrylique (PAN)"},
            {"key": "WO", "label": "Laine (WO)"},
            {"key": "CTA", "label": "Triacétate (CTA)"},
            {"key": "CV", "label": "Viscose (CV)"},
        ]

    # Construire la matrice : lab_code -> {param: note}
    matrix = []
    param_stats = {}

    for param in solidite_params:
        key = param["key"]
        notes = []
        for s in submissions:
            data = {}
            if s.solidite_data:
                try:
                    import json as _json
                    data = _json.loads(s.solidite_data)
                except Exception:
                    pass
            val = data.get(key)
            if val is not None:
                try:
                    notes.append(float(val))
                except (ValueError, TypeError):
                    pass

        if notes:
            note_counts = Counter([int(round(n)) for n in notes])
            consensus_note = int(round(compute_median(notes)))
            # Mode
            mode_note = note_counts.most_common(1)[0][0]
            mean_note = round(sum(notes) / len(notes), 2)
            sd_note = round(std_dev(notes), 3) if len(notes) > 1 else 0.0
            conformes = sum(1 for n in notes if n >= 4)
            conformity_rate = round(conformes / len(notes) * 100, 1)
        else:
            consensus_note = None
            mode_note = None
            mean_note = None
            sd_note = None
            conformity_rate = None
            note_counts = {}

        param_stats[key] = {
            "label": param["label"],
            "notes": notes,
            "n": len(notes),
            "consensus": consensus_note,
            "mode": mode_note,
            "mean": mean_note,
            "sd": sd_note,
            "distribution": dict(note_counts),
            "conformity_rate": conformity_rate,
        }

    # Construire la matrice par laboratoire
    for s in submissions:
        data = {}
        if s.solidite_data:
            try:
                import json as _json
                data = _json.loads(s.solidite_data)
            except Exception:
                pass
        row = {
            "lab_code": s.form.laboratory.code,
            "lab_name": s.form.laboratory.name,
            "params": {},
            "overall_score": None,
            "is_deviant": False,
        }
        scores = []
        for param in solidite_params:
            key = param["key"]
            val = data.get(key)
            note = None
            if val is not None:
                try:
                    note = float(val)
                except (ValueError, TypeError):
                    pass
            consensus_note = param_stats[key].get("consensus")
            deviant = False
            if note is not None and consensus_note is not None:
                deviant = abs(note - consensus_note) >= 2
                scores.append(note)
            row["params"][key] = {
                "note": note,
                "deviant": deviant,
            }
        if scores:
            row["overall_score"] = round(sum(scores) / len(scores), 2)
            row["is_deviant"] = any(row["params"][p["key"]]["deviant"] for p in solidite_params)
        matrix.append(row)

    # Calcul du radar (moyennes par paramètre pour graphique)
    radar_data = []
    for param in solidite_params:
        key = param["key"]
        stats = param_stats[key]
        radar_data.append({
            "label": param["label"],
            "key": key,
            "mean": stats["mean"] or 0,
            "conformity_rate": stats["conformity_rate"] or 0,
        })

    return {
        "matrix": matrix,
        "param_stats": param_stats,
        "radar_data": radar_data,
        "params": solidite_params,
    }


def mandel_h_statistic(submissions, target_val, target_sd):
    """
    Statistique h de Mandel (ISO 5725-2 / ISO 13528) pour évaluation de la fidélité
    inter-laboratoires. Détecte les laboratoires avec biais systématique.
    h_i = (y_i - y_bar) / sqrt(sum((y_j - y_bar)^2) / (p-1))
    Critères : |h| > h_crit(5%) → biais significatif
    """
    values = []
    lab_codes = []
    for s in submissions:
        if s.value is not None:
            values.append(s.value)
            lab_codes.append(s.form.laboratory.code)

    p = len(values)
    if p < 3:
        return {"results": [], "h_critical": None, "n": p}

    mean_all = sum(values) / p
    ss = sum((v - mean_all) ** 2 for v in values)
    denom = math.sqrt(ss / (p - 1)) if ss > 0 else 1e-9

    # Valeur critique de h (approximation standard, p. ex. table ISO 5725-2)
    # h_crit(5%, p) ≈ (p-1)/sqrt(p) * t(5%/p, p-2) — approximation simplifiée
    # On utilise la formule h_crit ≈ 1 + 3.5/sqrt(p) (approximation pratique)
    h_critical = round(1.0 + 3.5 / math.sqrt(p), 3)

    results = []
    for lab_code, val in zip(lab_codes, values):
        h = (val - mean_all) / denom
        significant = abs(h) > h_critical
        results.append({
            "lab_code": lab_code,
            "value": val,
            "h": round(h, 3),
            "significant": significant,
        })

    # Trier par |h| décroissant
    results.sort(key=lambda r: abs(r["h"]), reverse=True)

    return {
        "results": results,
        "h_critical": h_critical,
        "n": p,
        "mean": round(mean_all, 5),
    }


def compute_reliability_index(z_score, en_score=None, is_grubbs_outlier=False):
    """
    Indice de Fiabilité du Résultat (IFR) — Score composite par participant.
    Combine z-score, En-score (si disponible) et statut Grubbs en un score 0-100.
    IFR >= 80 : Excellent
    IFR >= 60 : Satisfaisant
    IFR >= 40 : Surveillance requise
    IFR <  40 : Non satisfaisant
    """
    if z_score is None:
        return {"ifr": None, "badge": "secondary", "label": "Non calculable"}

    az = abs(z_score)

    # Composante z-score (0-60 points)
    if az <= 2.0:
        z_pts = 60
    elif az <= 3.0:
        z_pts = max(0, int(60 - (az - 2.0) * 60))
    else:
        z_pts = 0

    # Composante En (0-30 points, si disponible)
    if en_score is not None:
        ae = abs(en_score)
        if ae < 1.0:
            en_pts = 30
        elif ae < 2.0:
            en_pts = max(0, int(30 - (ae - 1.0) * 30))
        else:
            en_pts = 0
    else:
        # En non disponible : redistribuer vers z_score
        en_pts = 20 if az <= 2.0 else (10 if az <= 3.0 else 0)

    # Pénalité Grubbs (-10 points)
    grubbs_penalty = -10 if is_grubbs_outlier else 0

    ifr = max(0, min(100, z_pts + en_pts + grubbs_penalty))

    if ifr >= 80:
        badge, label = "success", "Excellent"
    elif ifr >= 60:
        badge, label = "info", "Satisfaisant"
    elif ifr >= 40:
        badge, label = "warning", "Surveillance requise"
    else:
        badge, label = "danger", "Non satisfaisant"

    return {"ifr": ifr, "badge": badge, "label": label}


def detect_systematic_trend(z_scores_history):
    """
    Détection de tendance systématique ISO (ISO 13528 §9.6 / règle de Westgard).
    Alerte si >= 3 z-scores consécutifs du même signe (biais systématique probable).
    z_scores_history : liste ordonnée de z-scores (du plus ancien au plus récent).
    Retourne : alerte booléenne + message.
    """
    if not z_scores_history or len(z_scores_history) < 3:
        return {"trend_detected": False, "message": "Historique insuffisant (< 3 points)", "streak": 0}

    # Filtrer None
    zs = [z for z in z_scores_history if z is not None]
    if len(zs) < 3:
        return {"trend_detected": False, "message": "Données insuffisantes", "streak": 0}

    # Chercher la série la plus longue de signe identique en fin de liste
    last_sign = (1 if zs[-1] > 0 else -1) if zs[-1] != 0 else 0
    streak = 1
    for i in range(len(zs) - 2, -1, -1):
        current_sign = (1 if zs[i] > 0 else -1) if zs[i] != 0 else 0
        if current_sign == last_sign and last_sign != 0:
            streak += 1
        else:
            break

    if streak >= 3:
        direction = "positifs" if last_sign > 0 else "négatifs"
        msg = f"⚠️ Tendance systématique détectée : {streak} z-scores consécutifs {direction}. Vérifier le biais du laboratoire."
        return {"trend_detected": True, "message": msg, "streak": streak, "direction": direction}

    return {
        "trend_detected": False,
        "message": f"Aucune tendance détectée (série actuelle : {streak})",
        "streak": streak,
    }


def build_tools_summary(trial_type, stats, participant_results=None):
    """
    Construit la liste structurée des outils statistiques utilisés lors du traitement.
    Retourne une liste de dicts : {outil, reference, description, resultat, status}.
    Utilisé pour le tableau récapitulatif 'Outils Statistiques Utilisés' du dashboard.
    """
    tools = []

    if trial_type == "quantitative":
        # Algorithme A
        alg_a = stats.get("alg_a") or {}
        if alg_a.get("x_pt") is not None:
            tools.append({
                "outil": "Algorithme A",
                "reference": "ISO 13528 §C.3.1 / TTS §5.1.3.1",
                "description": "Estimation robuste itérative de la valeur assignée (x_pt) et de l'écart-type (σ_pt)",
                "resultat": f"x_pt = {alg_a.get('x_pt')} | σ_pt = {alg_a.get('sigma_pt')} | {alg_a.get('iterations')} itérations",
                "status": "success" if not alg_a.get("high_outliers_warning") else "warning",
                "warning": "Taux d'aberrants > 20% — Médiane recommandée" if alg_a.get("high_outliers_warning") else None,
            })

        # Médiane / MADe
        med = stats.get("med_stats") or {}
        if med.get("median") is not None:
            tools.append({
                "outil": "Médiane / MADe / nIQR",
                "reference": "ISO 13528 §C.3.1 / TTS §5.1.3.2",
                "description": "Estimateurs robustes non paramétriques : Médiane, Écart Médian Absolu normalisé",
                "resultat": f"Médiane = {med.get('median')} | MADe = {med.get('made')} | nIQR = {med.get('niqr')}",
                "status": "info",
            })

        # M-Estimateur
        m_est = stats.get("m_est") or {}
        if m_est.get("mu_hat") is not None:
            tools.append({
                "outil": "M-Estimateur Logistique",
                "reference": "TTS §5.1.3.2",
                "description": "Estimation robuste avec fonction d'influence logistique (poids pondérés par distance)",
                "resultat": f"μ̂ = {m_est.get('mu_hat')} | σ_robuste = {m_est.get('sigma_robuste')} | {m_est.get('iterations')} itérations",
                "status": "info",
            })

        # Grubbs
        grubbs = stats.get("grubbs") or {}
        if grubbs.get("G") is not None:
            st = grubbs.get("status", "")
            tools.append({
                "outil": "Test de Grubbs (simple & double)",
                "reference": "ISO 13528 / TTS §A1.4",
                "description": "Détection des valeurs aberrantes (*) et isolées (**) par statistique G vs. valeurs critiques à 1% et 5%",
                "resultat": f"G = {grubbs.get('G')} | G_crit(5%) = {grubbs.get('G_critical_5')} | G_crit(1%) = {grubbs.get('G_critical_1')} → {st}",
                "status": "danger" if "aberrante" in st else ("warning" if "isolée" in st else "success"),
            })

        # Droite d'Henry
        henry = stats.get("henry") or {}
        if henry.get("r_squared") is not None:
            tools.append({
                "outil": "Droite d'Henry",
                "reference": "TTS §A1.1",
                "description": "Vérification graphique de la normalité par corrélation rang-normit (R²)",
                "resultat": f"R² = {henry.get('r_squared')} → {'Distribution normale' if henry.get('normal_conform') else 'Distribution non normale'}",
                "status": "success" if henry.get("normal_conform") else "warning",
            })

        # K-S
        ks = stats.get("ks") or {}
        if ks.get("d_max") is not None:
            tools.append({
                "outil": "Test de Kolmogorov-Smirnov",
                "reference": "TTS §A1.6",
                "description": "Test non paramétrique de normalité : D_max observé vs. D_critique (α=5%)",
                "resultat": f"D_max = {ks.get('d_max')} | D_crit = {ks.get('d_critical')} → {'Normale ✓' if ks.get('is_normal') else 'Non normale ✗'}",
                "status": "success" if ks.get('is_normal') else "warning",
            })

        # Kurtosis / Skewness
        ks2 = stats.get("kurt_skew") or {}
        if ks2.get("kurtosis") is not None:
            tools.append({
                "outil": "Kurtosis & Asymétrie (Skewness)",
                "reference": "TTS §A1.7-A1.8",
                "description": "Mesures de forme de la distribution : aplatissement (K) et asymétrie (g1)",
                "resultat": f"K = {ks2.get('kurtosis')} ({ks2.get('kurtosis_type')}) | g1 = {ks2.get('skewness')} ({ks2.get('skewness_type')})",
                "status": "info",
            })

        # Boxplot
        bp = stats.get("boxplot") or {}
        if bp.get("q1") is not None:
            n_outliers = len(bp.get("outliers", []))
            tools.append({
                "outil": "Boîte à Moustaches (Boxplot)",
                "reference": "TTS §A1.2",
                "description": "Représentation des quartiles Q1, Médiane, Q3 et détection des outliers hors moustaches (1.5×IQR)",
                "resultat": f"Q1={bp.get('q1')} | Q3={bp.get('q3')} | IQR={bp.get('iqr')} | {n_outliers} outlier(s) détecté(s)",
                "status": "warning" if n_outliers > 0 else "success",
            })

        # z-score
        tools.append({
            "outil": "z-score / z'-score",
            "reference": "ISO 13528 §9.2-9.4 / TTS §5.1.4",
            "description": "Évaluation de la performance de chaque participant par rapport à la valeur assignée et σ_pt",
            "resultat": "Satisfaisant |z|≤2 | Douteux 2<|z|<3 | Non satisfaisant |z|≥3",
            "status": "info",
        })

    elif trial_type == "qualitative":
        tools.append({
            "outil": "Consensus Qualitatif (Mode)",
            "reference": "ISO 13528 §9.5 / ISO 17043",
            "description": "Résultat consensuel = réponse la plus fréquente (mode) parmi les participants",
            "resultat": f"Taux d'accord = {stats.get('agreement_rate', 'N/A')}%",
            "status": "success" if (stats.get("agreement_rate") or 0) >= 80 else "warning",
        })
        kappa = stats.get("kappa") or {}
        if kappa.get("kappa") is not None:
            tools.append({
                "outil": "Coefficient Kappa de Cohen",
                "reference": "ISO 13528 / Statistiques inter-juges",
                "description": "Mesure de l'accord inter-laboratoires corrigé du hasard",
                "resultat": f"κ = {kappa.get('kappa')} → {kappa.get('interpretation')}",
                "status": kappa.get("badge", "info"),
            })

    elif trial_type == "solidite":
        tools.append({
            "outil": "Analyse Matricielle Solidité",
            "reference": "EN ISO 105 / ISO 13528 (adapté)",
            "description": "Distribution et consensus des notes (1-5) pour chaque paramètre de solidité des couleurs",
            "resultat": f"{len(stats.get('param_stats', {}))} paramètres analysés",
            "status": "info",
        })
        tools.append({
            "outil": "Taux de Conformité par Paramètre",
            "reference": "EN ISO 105-A02/A04",
            "description": "Proportion des notes ≥ 4 (Bon à Excellent) par paramètre de solidité",
            "resultat": "Voir heatmap et tableau détaillé",
            "status": "info",
        })

    return tools
