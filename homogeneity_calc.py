"""Calculs d'homogénéité et de stabilité — portage de la fiche FTE 006 (version 06).

Référence : ISO 13528:2022 Annexe B (homogénéité, stabilité), ISO 5725-2:2019 (test de Cochran).
Module pur (sans Flask ni scipy) : les fonctions F et khi-deux sont calculées ici, de sorte que
le déploiement n'ait aucune dépendance supplémentaire. Les formules et les textes de décision
reprennent ceux de la feuille Excel afin que les résultats soient identiques.
"""
import math

# ─────────────────────────────────────────────────────────────────────────────
# Textes de décision (identiques à la feuille Excel FTE 006)
# ─────────────────────────────────────────────────────────────────────────────
T_RATIO_OK = "Le rapport sr/σpt est inférieur à 0,5"
T_RATIO_KO = "Le rapport sr/σpt est supérieur ou égal à 0,5"
T_HOM_OK = "Les échantillons sont suffisamment homogènes"
T_HOM_KO = "Les échantillons ne sont pas suffisamment homogènes"
T_TOL_OK = "L'échantillon n'est pas homogène, utiliser σ'pt pour évaluer la performance"
T_TOL_KO = "L'échantillon n'est pas homogène et le critère n'est pas vérifié"
T_STABLE = "Les échantillons sont stables"
T_UNSTABLE = "Les échantillons ne sont pas stables"
T_FINAL_HOM = ("Les échantillons sont homogènes selon les critères de l'annexe B de la norme ISO 13528:2022, "
               "σpt est à utiliser pour évaluer la performance des participants")
T_FINAL_SIGMA_PRIME = ("Un des critères d'homogénéité décrit dans l'annexe B de la norme ISO 13528:2022 n'est pas "
                       "respecté, σ'pt est à utiliser pour évaluer la performance des participants")
T_FINAL_CHANGE = "L'échantillon n'est pas homogène il faut changer d'entité soumise à l'essai"
T_STAB_OK = "Les échantillons sont stables, le Zscore est à utiliser pour évaluer la performance des participants"
T_STAB_KO = ("Les échantillons ne sont pas assez stables, utiliser Z'score pour évaluer la performance "
             "des participants")
T_CONCL_Z = "Utiliser le Zscore pour déterminer la performance des participants"
T_CONCL_SIGMA = "Utiliser σ'pt pour évaluer la performance des participants"
T_CONCL_ZP = "Z'score est à utiliser pour vérifier la performance des participants"
T_CONCL_ZP_SIGMA = "Z'score et σ'pt sont à utiliser pour vérifier la performance des participants"
T_CONCL_DECIDE = "Il faut prendre une décision concernant l'échantillon et son homogénéité"
T_TREND_NONE = "Pas de tendance significative, passez à l'évaluation de l'homogénéité"
T_TREND_A = "Présence de tendance significative, voir annexe B paragraphe B.2.1.a) de l'ISO 13528 page 49"
T_TREND_B = "Présence de tendance significative, voir annexe B paragraphe B.2.1.b) de l'ISO 13528 page 50"
T_COCHRAN_NONE = "Pas de valeurs aberrantes détectées"

MAX_G, MAX_M = 20, 6          # jusqu'à 20 échantillons x 6 prises d'essai (comme la feuille Excel)
MAX_STAB_G = 4                # 2 à 4 échantillons pour les essais de stabilité

COCHRAN_CRITICAL = {  # ISO 5725-2:2019 p.33 — {p laboratoires (échantillons): {n réplicats: (G 1 %, G 5 %)}}
    2: {2: (None, None), 3: (0.995, 0.975), 4: (0.979, 0.939), 5: (0.959, 0.906), 6: (0.937, 0.877)},
    3: {2: (0.993, 0.967), 3: (0.942, 0.871), 4: (0.883, 0.798), 5: (0.834, 0.746), 6: (0.793, 0.707)},
    4: {2: (0.968, 0.906), 3: (0.864, 0.768), 4: (0.781, 0.684), 5: (0.721, 0.629), 6: (0.676, 0.59)},
    5: {2: (0.928, 0.841), 3: (0.788, 0.684), 4: (0.696, 0.598), 5: (0.633, 0.544), 6: (0.588, 0.506)},
    6: {2: (0.883, 0.781), 3: (0.722, 0.616), 4: (0.626, 0.532), 5: (0.564, 0.48), 6: (0.52, 0.445)},
    7: {2: (0.838, 0.727), 3: (0.664, 0.561), 4: (0.568, 0.48), 5: (0.508, 0.431), 6: (0.466, 0.397)},
    8: {2: (0.794, 0.68), 3: (0.615, 0.516), 4: (0.521, 0.438), 5: (0.463, 0.391), 6: (0.423, 0.36)},
    9: {2: (0.754, 0.638), 3: (0.573, 0.478), 4: (0.481, 0.403), 5: (0.425, 0.358), 6: (0.387, 0.329)},
    10: {2: (0.718, 0.602), 3: (0.536, 0.445), 4: (0.447, 0.373), 5: (0.393, 0.331), 6: (0.357, 0.303)},
    11: {2: (0.684, 0.57), 3: (0.504, 0.417), 4: (0.418, 0.348), 5: (0.366, 0.308), 6: (0.332, 0.281)},
    12: {2: (0.653, 0.541), 3: (0.475, 0.392), 4: (0.392, 0.326), 5: (0.343, 0.288), 6: (0.31, 0.262)},
    13: {2: (0.624, 0.515), 3: (0.45, 0.371), 4: (0.369, 0.307), 5: (0.322, 0.271), 6: (0.291, 0.243)},
    14: {2: (0.599, 0.492), 3: (0.427, 0.352), 4: (0.349, 0.291), 5: (0.304, 0.255), 6: (0.274, 0.232)},
    15: {2: (0.575, 0.471), 3: (0.407, 0.335), 4: (0.332, 0.276), 5: (0.288, 0.242), 6: (0.259, 0.22)},
    16: {2: (0.553, 0.452), 3: (0.388, 0.319), 4: (0.316, 0.262), 5: (0.274, 0.23), 6: (0.246, 0.208)},
    17: {2: (0.532, 0.434), 3: (0.372, 0.305), 4: (0.301, 0.25), 5: (0.261, 0.219), 6: (0.234, 0.198)},
    18: {2: (0.514, 0.418), 3: (0.356, 0.293), 4: (0.288, 0.24), 5: (0.249, 0.209), 6: (0.223, 0.189)},
    19: {2: (0.496, 0.403), 3: (0.343, 0.281), 4: (0.276, 0.23), 5: (0.238, 0.2), 6: (0.214, 0.181)},
    20: {2: (0.48, 0.389), 3: (0.33, 0.27), 4: (0.265, 0.22), 5: (0.229, 0.192), 6: (0.205, 0.174)},
}


# ─────────────────────────────────────────────────────────────────────────────
# Fonctions statistiques (équivalents de CHIINV, FINV de la feuille Excel)
# ─────────────────────────────────────────────────────────────────────────────
def _gamma_p(a, x):
    """Fonction gamma incomplète régularisée P(a, x)."""
    if x <= 0:
        return 0.0
    if x < a + 1:
        term = total = 1.0 / a
        n = a
        for _ in range(1000):
            n += 1
            term *= x / n
            total += term
            if abs(term) < abs(total) * 1e-15:
                break
        return total * math.exp(-x + a * math.log(x) - math.lgamma(a))
    tiny = 1e-300
    b = x + 1 - a
    c = 1 / tiny
    d = 1 / b
    h = d
    for i in range(1, 1000):
        an = -i * (i - a)
        b += 2
        d = an * d + b
        d = tiny if abs(d) < tiny else d
        c = b + an / c
        c = tiny if abs(c) < tiny else c
        d = 1 / d
        delta = d * c
        h *= delta
        if abs(delta - 1) < 1e-15:
            break
    return 1 - math.exp(-x + a * math.log(x) - math.lgamma(a)) * h


def _beta_i(a, b, x):
    """Fonction bêta incomplète régularisée I_x(a, b)."""
    if x <= 0:
        return 0.0
    if x >= 1:
        return 1.0
    front = math.exp(math.lgamma(a + b) - math.lgamma(a) - math.lgamma(b) + a * math.log(x) + b * math.log(1 - x))

    def cf(a, b, x):
        tiny = 1e-300
        qab, qap, qam = a + b, a + 1, a - 1
        c, d = 1.0, 1 - qab * x / qap
        d = tiny if abs(d) < tiny else d
        d = 1 / d
        h = d
        for m in range(1, 1000):
            m2 = 2 * m
            aa = m * (b - m) * x / ((qam + m2) * (a + m2))
            d = 1 + aa * d
            d = tiny if abs(d) < tiny else d
            c = 1 + aa / c
            c = tiny if abs(c) < tiny else c
            d = 1 / d
            h *= d * c
            aa = -(a + m) * (qab + m) * x / ((a + m2) * (qap + m2))
            d = 1 + aa * d
            d = tiny if abs(d) < tiny else d
            c = 1 + aa / c
            c = tiny if abs(c) < tiny else c
            d = 1 / d
            delta = d * c
            h *= delta
            if abs(delta - 1) < 1e-15:
                break
        return h

    if x < (a + 1) / (a + b + 2):
        return front * cf(a, b, x) / a
    return 1 - front * cf(b, a, 1 - x) / b


def _bisect_isf(sf, p, hi=1.0):
    """Cherche x tel que sf(x) = p, sf étant décroissante sur [0, +inf[."""
    lo = 0.0
    while sf(hi) > p:
        lo, hi = hi, hi * 2
        if hi > 1e12:
            break
    for _ in range(200):
        mid = (lo + hi) / 2
        if sf(mid) > p:
            lo = mid
        else:
            hi = mid
    return (lo + hi) / 2


def chi2_isf(p, k):
    """Équivalent de CHIINV(p, k) : valeur critique unilatérale du khi-deux."""
    return _bisect_isf(lambda x: 1 - _gamma_p(k / 2.0, x / 2.0), p, hi=max(1.0, float(k)))


def f_isf(p, d1, d2):
    """Équivalent de FINV(p, d1, d2) : valeur critique unilatérale de Fisher-Snedecor."""
    return _bisect_isf(lambda x: 1 - _beta_i(d1 / 2.0, d2 / 2.0, d1 * x / (d1 * x + d2)), p, hi=2.0)


def t_two_sided_p(t, df):
    """Probabilité bilatérale d'une statistique t de Student."""
    if df <= 0:
        return None
    return _beta_i(df / 2.0, 0.5, df / (df + t * t))


def mean(xs):
    return sum(xs) / len(xs)


def devsq(xs):
    m = mean(xs)
    return sum((x - m) ** 2 for x in xs)


def variance(xs):
    return devsq(xs) / (len(xs) - 1)


# ─────────────────────────────────────────────────────────────────────────────
# Mise en forme des grilles saisies
# ─────────────────────────────────────────────────────────────────────────────
def clean_rows(rows, labels=None):
    """Garde les lignes dont le 1er résultat est renseigné (règle de la feuille Excel),
    et supprime les cases vides à l'intérieur d'une ligne. Retourne (lignes, étiquettes, indices)."""
    out, labs, idx = [], [], []
    for i, row in enumerate(rows or []):
        if row and row[0] is not None:
            out.append([v for v in row if v is not None])
            labs.append(labels[i] if labels and i < len(labels) and labels[i] else f"HO {i + 1:03d}")
            idx.append(i)
    return out, labs, idx


def _same_m(rows):
    """Nombre de prises d'essai m (1re ligne, comme l'Excel) et liste des lignes incohérentes."""
    m = len(rows[0])
    bad = [i for i, r in enumerate(rows) if len(r) != m]
    return m, bad


# ─────────────────────────────────────────────────────────────────────────────
# Test de Cochran (ISO 5725-2) — feuille « Test de cochran »
# ─────────────────────────────────────────────────────────────────────────────
def cochran_test(rows, labels):
    g = len(rows)
    if g < 2 or len(rows[0]) < 2:
        return None
    m = len(rows[0])
    variances = [variance(r) if len(r) > 1 else 0.0 for r in rows]
    total, vmax = sum(variances), max(variances)
    if total == 0:
        return {"g": g, "m": m, "variances": variances, "C": None, "g1": None, "g5": None,
                "decision": "Si² correcte", "to_remove": None, "text": T_COCHRAN_NONE, "available": False}
    c_stat = vmax / total
    crit = COCHRAN_CRITICAL.get(g, {}).get(m)
    g1, g5 = (crit if crit else (None, None))
    if g5 is None:
        return {"g": g, "m": m, "variances": variances, "C": c_stat, "g1": g1, "g5": g5,
                "decision": None, "to_remove": None, "available": False,
                "text": "Table de Cochran indisponible pour g = %d et m = %d (g de 3 à 20, m de 2 à 6)." % (g, m)}
    if c_stat <= g5:
        decision = "Si² correcte"
    elif c_stat <= g1:
        decision = "Si² isolée"
    else:
        decision = "Si² aberrante"
    worst = variances.index(vmax)
    to_remove = labels[worst] if decision == "Si² aberrante" else None
    return {"g": g, "m": m, "variances": variances, "C": c_stat, "g1": g1, "g5": g5, "decision": decision,
            "to_remove": to_remove, "worst_index": worst, "available": True,
            "text": T_COCHRAN_NONE if to_remove is None else to_remove}


# ─────────────────────────────────────────────────────────────────────────────
# Homogénéité (ISO 13528:2022 Annexe B)
# ─────────────────────────────────────────────────────────────────────────────
def resolve_sigma_pt(mode, refs, direct, xbar2):
    """σpt de TTS (feuille Excel, tableau I.2) : moyenne des σpt de références, en valeur absolue
    ou relative (multipliée par la moyenne générale x̿). Mode « direct » : valeur saisie."""
    refs = [r for r in (refs or []) if r is not None]
    if mode == "direct":
        return direct if direct else None
    if not refs:
        return None
    base = mean(refs)
    if mode == "absolute":
        return base
    if mode == "relative":
        return base * xbar2
    return None


def homogeneity(rows, sigma_mode, sigma_refs, sigma_direct):
    g = len(rows)
    if g < 2:
        return {"error": "Il faut au moins 2 échantillons (g ≥ 2)."}
    m, bad = _same_m(rows)
    if m < 2:
        return {"error": "Il faut au moins 2 prises d'essai par échantillon (m ≥ 2)."}
    if bad:
        return {"error": "Chaque échantillon doit avoir le même nombre de résultats (m = %d)." % m}

    means = [mean(r) for r in rows]
    variances = [variance(r) for r in rows]
    xbar2 = mean(means)                               # x̿
    sx2 = devsq(means) / (g - 1)                      # Sx̄²
    sw2 = sum(variances) / g                          # Sw²
    sr = math.sqrt(sw2)                               # Sr = Sw
    ss_w2 = sx2 + (1 - 1.0 / m) * sw2                 # Ss,w²
    ss2 = sx2 - sw2 / m                               # Ss²
    ss = 0.0 if ss2 < 0 else math.sqrt(ss2)           # Ss

    out = {"g": g, "m": m, "means": means, "variances": variances, "xbar2": xbar2, "sx2": sx2, "sw2": sw2,
           "sr": sr, "ss_w2": ss_w2, "ss2": ss2, "ss": ss, "ss_warning": ss2 < 0,
           "sigma_pt": None, "ratio": None, "ratio_ok": None, "ratio_text": None, "f1": None, "f2": None,
           "sigma_allow2": None, "c": None, "homogeneous": None, "hom_text": None, "tolerance": None,
           "tolerance_ok": None, "tol_text": None, "outcome": None, "final_text": None}

    sigma = resolve_sigma_pt(sigma_mode, sigma_refs, sigma_direct, xbar2)
    out["sigma_pt"] = sigma
    if not sigma:
        return out

    out["ratio"] = sr / sigma
    out["ratio_ok"] = out["ratio"] < 0.5
    out["ratio_text"] = T_RATIO_OK if out["ratio_ok"] else T_RATIO_KO

    out["sigma_allow2"] = (0.3 * sigma) ** 2                                  # σallow²
    out["f1"] = chi2_isf(0.05, g - 1) / (g - 1)                               # F1
    out["f2"] = (f_isf(0.05, g - 1, g * (m - 1)) - 1) / m                     # F2
    out["c"] = out["f1"] * out["sigma_allow2"] + out["f2"] * sw2              # c
    out["homogeneous"] = not (ss > math.sqrt(out["c"]))                        # Ss ≤ √c
    out["hom_text"] = T_HOM_OK if out["homogeneous"] else T_HOM_KO

    if out["homogeneous"]:
        out["outcome"], out["final_text"] = "homogeneous", T_FINAL_HOM
    else:
        out["tolerance"] = sigma / math.sqrt(sigma ** 2 + ss ** 2)             # critère de tolérance (formule 18)
        out["tolerance_ok"] = 0.96 < out["tolerance"] < 1
        out["tol_text"] = T_TOL_OK if out["tolerance_ok"] else T_TOL_KO
        if out["tolerance_ok"]:
            out["outcome"], out["final_text"] = "sigma_prime", T_FINAL_SIGMA_PRIME
        else:
            out["outcome"], out["final_text"] = "change_entity", T_FINAL_CHANGE
    return out


# ─────────────────────────────────────────────────────────────────────────────
# Stabilité (ISO 13528:2022 Annexe B §B.4 à B.6)
# ─────────────────────────────────────────────────────────────────────────────
def stability_block(before_mean, rows, sigma):
    """Essai de stabilité : moyenne après, Sw, rapport sr/σpt, écart avec la moyenne avant (≤ 0,3 σpt)."""
    if not rows or sigma is None or before_mean is None:
        return None
    g = len(rows)
    m, bad = _same_m(rows)
    if m < 2:
        return {"error": "Il faut au moins 2 prises d'essai par échantillon pour la stabilité."}
    if bad:
        return {"error": "Chaque échantillon de stabilité doit avoir le même nombre de résultats (m = %d)." % m}
    variances = [variance(r) for r in rows]
    after = mean([v for r in rows for v in r])
    sw = math.sqrt(sum(variances) / g)
    diff = abs(before_mean - after)
    stable = diff <= 0.3 * sigma
    ratio = sw / sigma
    return {"g": g, "m": m, "variances": variances, "mean_before": before_mean, "mean_after": after, "sw": sw,
            "ratio": ratio, "ratio_text": T_RATIO_OK if ratio < 0.5 else T_RATIO_KO, "ratio_ok": ratio < 0.5,
            "difference": diff, "limit": 0.3 * sigma, "stable": stable,
            "text": T_STABLE if stable else T_UNSTABLE}


# ─────────────────────────────────────────────────────────────────────────────
# Tendance (indicatif) : régression linéaire des valeurs en fonction de l'ordre de réalisation.
# La feuille Excel laisse l'opérateur juger sur graphique ; le test de pente aide à décider.
# ─────────────────────────────────────────────────────────────────────────────
def trend_hint(values):
    pts = [(i + 1, v) for i, v in enumerate(values) if v is not None]
    n = len(pts)
    if n < 4:
        return None
    xs, ys = [p[0] for p in pts], [p[1] for p in pts]
    xm, ym = mean(xs), mean(ys)
    sxx = sum((x - xm) ** 2 for x in xs)
    sxy = sum((x - xm) * (y - ym) for x, y in pts)
    slope = sxy / sxx
    intercept = ym - slope * xm
    resid = sum((y - (intercept + slope * x)) ** 2 for x, y in pts)
    if resid == 0:
        return {"slope": slope, "intercept": intercept, "p_value": 0.0 if slope else 1.0, "n": n,
                "significant": bool(slope)}
    se = math.sqrt(resid / (n - 2) / sxx)
    p = t_two_sided_p(slope / se, n - 2) if se else None
    return {"slope": slope, "intercept": intercept, "p_value": p, "n": n,
            "significant": bool(p is not None and p < 0.05)}


# ─────────────────────────────────────────────────────────────────────────────
# Évaluation complète d'une étude quantitative
# ─────────────────────────────────────────────────────────────────────────────
def evaluate(inputs):
    """inputs : voir homogeneity_study dans blueprints/homogeneity.py. Retourne tous les résultats
    et les décisions, avec les mêmes conclusions que la feuille Excel FTE 006 v06."""
    res = {"warnings": [], "hom": None, "cochran": None, "trends": {}, "stab_end": None, "transport": None,
           "storage1": None, "storage2": None, "stability": None, "conclusion": None, "sigma_pt": None}
    hom_in = inputs.get("hom") or {}
    all_rows, all_labels, idx = clean_rows(hom_in.get("rows"), hom_in.get("codes"))
    excluded = set(hom_in.get("excluded") or [])
    if not all_rows:
        return res

    res["cochran"] = cochran_test(all_rows, all_labels)
    keep = [(r, l) for r, l, i in zip(all_rows, all_labels, idx) if i not in excluded]
    rows = [r for r, _ in keep]
    labels = [l for _, l in keep]
    res["excluded_labels"] = [l for l, i in zip(all_labels, idx) if i in excluded]

    sig = inputs.get("sigma") or {}
    hom = homogeneity(rows, sig.get("mode"), sig.get("refs"), sig.get("direct"))
    if hom.get("error"):
        res["warnings"].append(hom["error"])
        return res
    res["hom"] = hom
    res["labels"] = labels
    if hom.get("ss_warning"):
        res["warnings"].append("La variance intra-échantillon est supérieure à la variance composée (Ss² < 0) : Ss est ramené à 0.")
    sigma = hom["sigma_pt"]
    res["sigma_pt"] = sigma
    if not sigma:
        res["warnings"].append("σpt n'est pas défini : renseignez les σpt de référence (ou une valeur directe).")

    # tendances (indicatives) : valeurs individuelles par prise d'essai, et moyennes
    m = hom["m"]
    res["trends"]["individual"] = [trend_hint([r[j] for r in rows]) for j in range(m)]
    res["trends"]["means"] = trend_hint(hom["means"])
    t_ind, t_means = inputs.get("trend_individual"), inputs.get("trend_means")
    res["trend_texts"] = {
        "individual": T_TREND_A if t_ind == "present" else (T_TREND_NONE if t_ind == "none" else None),
        "means": T_TREND_B if t_means == "present" else (T_TREND_NONE if t_means == "none" else None),
    }

    before_mean = mean([v for r in rows for v in r])
    # II.1 stabilité à la fin de l'essai d'aptitude
    s_rows, _, _ = clean_rows((inputs.get("stab_end") or {}).get("rows"))
    res["stab_end"] = stability_block(before_mean, s_rows, sigma) if s_rows else None

    # II.2 transport (si l'entité y est sensible)
    tr = inputs.get("transport") or {}
    t_rows, _, _ = clean_rows(tr.get("rows"))
    res["transport"] = stability_block(before_mean, t_rows, sigma) if (tr.get("sensitive") and t_rows) else None

    # II.3 stockage (si l'entité risque d'être instable jusqu'à la fin de l'essai)
    st = inputs.get("storage") or {}
    if st.get("at_risk"):
        r1, _, _ = clean_rows(st.get("rows1"))
        r2, _, _ = clean_rows(st.get("rows2"))
        res["storage1"] = stability_block(before_mean, r1, sigma) if r1 else None
        res["storage2"] = stability_block(before_mean, r2, sigma) if r2 else None
        res["storage_trend_text"] = {"none": "Pas de tendance significative",
                                     "present": "Présence de tendance significative"}.get(st.get("trend"))

        storage_points = []
        for point in (st.get("timeline") or []):
            mean_v = point.get("mean")
            if mean_v is None:
                x1 = point.get("x1")
                x2 = point.get("x2")
                if x1 is not None or x2 is not None:
                    mean_v = (x1 + x2) / 2 if x1 is not None and x2 is not None else (x1 if x1 is not None else x2)
            if mean_v is not None:
                storage_points.append({"date": point.get("date"), "condition": point.get("condition"), "mean": mean_v})
        res["storage_points"] = storage_points

        seen = set()
        def add_mean(value):
            if value is None:
                return
            key = round(float(value), 12)
            if key not in seen:
                seen.add(key)
                series.append(float(value))

        series = [before_mean]
        for point in storage_points:
            add_mean(point.get("mean"))
        for block in (res["storage1"], res["storage2"]):
            if block and not block.get("error"):
                add_mean(block.get("mean_after"))
        if res["stab_end"] and not res["stab_end"].get("error"):
            add_mean(res["stab_end"].get("mean_after"))
        res["storage_means"] = series

    blocks = [res["stab_end"], res["transport"], res["storage1"], res["storage2"]]
    for b, name in zip(blocks, ["fin d'essai", "transport", "stockage 1", "stockage 2"]):
        if b and b.get("error"):
            res["warnings"].append("Stabilité (%s) : %s" % (name, b["error"]))
    valid = [b for b in blocks if b and not b.get("error")]

    # Récapitulatif de la stabilité (cellules Q167 à J172 de la feuille Excel)
    if sigma and hom.get("ratio_text") is not None:
        ratios_ko = (not hom["ratio_ok"]) or any(not b["ratio_ok"] for b in valid)
        storage_stable = None
        if res["storage1"] and res["storage2"] and not res["storage1"].get("error") and not res["storage2"].get("error"):
            storage_stable = res["storage1"]["stable"] and res["storage2"]["stable"]
        unstable = [
            storage_stable is False,
            bool(res["stab_end"] and not res["stab_end"].get("error") and not res["stab_end"]["stable"]),
            bool(res["transport"] and not res["transport"].get("error") and not res["transport"]["stable"]),
        ]
        any_unstable = any(unstable)
        res["stability"] = {
            "ratio_text": T_RATIO_KO if ratios_ko else T_RATIO_OK,
            "storage_stable": storage_stable,
            "end_stable": res["stab_end"]["stable"] if res["stab_end"] and not res["stab_end"].get("error") else None,
            "transport_stable": res["transport"]["stable"] if res["transport"] and not res["transport"].get("error") else None,
            "unstable": any_unstable,
            "text": T_STAB_KO if any_unstable else T_STAB_OK,
            "complete": bool(res["stab_end"] and not res["stab_end"].get("error")),
        }
        if not res["stability"]["complete"]:
            res["warnings"].append("L'étude de stabilité à la fin de l'essai d'aptitude (II.1) n'est pas renseignée : la conclusion est provisoire.")

        # Conclusion générale (cellule L181)
        outcome = hom["outcome"]
        if outcome == "change_entity":
            concl = T_CONCL_DECIDE
        elif outcome == "homogeneous":
            concl = T_CONCL_ZP if any_unstable else T_CONCL_Z
        else:
            concl = T_CONCL_ZP_SIGMA if any_unstable else T_CONCL_SIGMA
        res["conclusion"] = {"text": concl, "use_z_prime": any_unstable, "use_sigma_prime": outcome == "sigma_prime",
                             "provisional": not res["stability"]["complete"]}
    return res
