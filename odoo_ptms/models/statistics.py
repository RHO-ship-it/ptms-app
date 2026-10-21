# -*- coding: utf-8 -*-
"""
PTMS Statistical Functions
Basé sur ISO 13528, ISO 17043 et les procédures TTS.
Comprend : Algorithme A, Algorithme S, Z-score, En, Test de Grubbs, Droite d'Henry, etc.
"""
import math

# Tables de Grubbs (extrait pour simplification, on peut l'étendre selon le fichier source)
GRUBBS_FULL_TABLE = {
    3: (1.155, 1.155, None, None),
    4: (1.496, 1.481, 0.0000, 0.0002),
    5: (1.764, 1.715, 0.0018, 0.0090),
    6: (1.973, 1.887, 0.0116, 0.0349),
    7: (2.139, 2.020, 0.0308, 0.0708),
    8: (2.274, 2.126, 0.0563, 0.1101),
    9: (2.387, 2.215, 0.0851, 0.1492),
    10: (2.482, 2.290, 0.1150, 0.1864),
    # ... autres valeurs à compléter ou à importer depuis un dictionnaire complet
    100: (3.754, 3.383, None, None)
}

ALGORITHM_S_PARAMS = {
    1: (1.645, 1.097), 2: (1.517, 1.054), 3: (1.444, 1.039),
    4: (1.395, 1.032), 5: (1.359, 1.027), 6: (1.332, 1.024),
    7: (1.310, 1.021), 8: (1.292, 1.019), 9: (1.277, 1.018), 10: (1.264, 1.017)
}


def compute_median(vals):
    if not vals: return None
    s = sorted(vals)
    n = len(s)
    mid = n // 2
    return (s[mid - 1] + s[mid]) / 2.0 if n % 2 == 0 else float(s[mid])


def compute_quantile(vals, q):
    if not vals: return None
    s = sorted(vals)
    n = len(s)
    if n == 1: return float(s[0])
    pos = q * (n - 1)
    idx = int(pos)
    frac = pos - idx
    if idx >= n - 1: return float(s[-1])
    return s[idx] + frac * (s[idx + 1] - s[idx])


def std_dev(values):
    if not values or len(values) < 2: return 0.0
    p = len(values)
    mean_val = sum(values) / p
    var = sum((v - mean_val) ** 2 for v in values) / (p - 1)
    return math.sqrt(var)


def algorithm_a(values, tol=1e-6, max_iter=100):
    """Algorithme A conforme à ISO 13528 §C.3.1"""
    if not values or len(values) < 2:
        return {"x_pt": values[0] if values else None, "sigma_pt": None, "u_xpt": None}

    p = len(values)
    med = compute_median(values)
    abs_devs = [abs(v - med) for v in values]
    made = 1.483 * compute_median(abs_devs)

    if made == 0:
        mean_val = sum(values) / p
        made = compute_median([abs(v - mean_val) for v in values])

    x_star = med
    s_star = made if made > 0 else (std_dev(values) or 1e-6)

    for _ in range(1, max_iter + 1):
        delta = 1.5 * s_star
        x_win = [max(x_star - delta, min(x_star + delta, v)) for v in values]
        x_new = sum(x_win) / p
        variance = sum((w - x_new) ** 2 for w in x_win) / (p - 1)
        s_new = 1.134 * math.sqrt(variance)

        if abs(x_new - x_star) < tol and abs(s_new - s_star) < tol:
            x_star = x_new
            s_star = s_new
            break
        x_star = x_new
        s_star = s_new

    u_xpt = 1.25 * s_star / math.sqrt(p) if p > 0 else None
    return {
        "x_pt": round(x_star, 5),
        "sigma_pt": round(s_star, 5),
        "u_xpt": round(u_xpt, 5) if u_xpt else None,
    }


def compute_z_score(value, target_value, target_sd, u_xpt=None):
    if None in (value, target_value) or not target_sd or target_sd <= 0:
        return None, False

    denom = target_sd
    if u_xpt is not None and u_xpt >= 0.3 * target_sd:
        denom = math.sqrt(target_sd ** 2 + u_xpt ** 2)

    score = (value - target_value) / denom
    anomaly = abs(score) >= 3.0
    return round(score, 3), anomaly


def grubbs_test(values):
    """Test de Grubbs (simplifié)"""
    p = len(values)
    if p < 3: return {"outlier_value": None, "status": "Insuffisant"}
    mean_val = sum(values) / p
    sd_val = std_dev(values)
    if sd_val == 0: return {"outlier_value": None, "status": "Homogène"}

    min_val, max_val = min(values), max(values)
    g_1 = (mean_val - min_val) / sd_val
    g_p = (max_val - mean_val) / sd_val
    g_max = max(g_1, g_p)
    candidate = min_val if g_1 > g_p else max_val

    # Récupérer les valeurs critiques (simplifié pour l'exemple)
    c1, c5 = GRUBBS_FULL_TABLE.get(p, GRUBBS_FULL_TABLE.get(100))[:2]
    outlier = None
    status = "Correct"
    
    if c1 and g_max > c1:
        outlier = candidate
        status = "Valeur aberrante (**)"
    elif c5 and g_max > c5:
        outlier = candidate
        status = "Valeur isolée (*)"
        
    return {"outlier_value": outlier, "G": round(g_max, 3), "status": status}
