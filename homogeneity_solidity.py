"""Étude d'homogénéité et de stabilité — essais de solidité (fiche FTE 006 v06, page 6/6).

Même logique que les essais qualitatifs ordinaux, appliquée séparément à chacun des 9 paramètres
(dégradation de couleur et 8 dégorgements), avec 2 résultats par échantillon. La référence de chaque
paramètre est le mode ou la médiane de ses résultats (série 1 puis série 2, comme MODE.SNGL(F25:F34;F40:F49)).

Écart volontaire avec la feuille Excel : la décision de stabilité au stockage tient compte des quatre
essais (n° 1, 2, 3 et après la campagne). Dans la feuille, les essais 2, 3 et final sont calculés mais
aucune cellule ne les utilise (seul l'essai n° 1 intervient). L'option excel_storage_first_only=True
reproduit ce comportement pour les comparaisons."""
from homogeneity_ordinal import excel_mode, median

PARAM_NAMES = ["Dégradation de couleur", "Dégorgement - Acétate (CA)", "Dégorgement - Cotton (CO)",
               "Dégorgement - Polyamide (PA)", "Dégorgement - Polyester (PES)", "Dégorgement - Polyacrylique (PAN)",
               "Dégorgement - Laine (WO)", "Dégorgement - Triacétate (CTA)", "Dégorgement - Viscose (CV)"]
N_PARAMS = 9
HOM_G = 10
STAB_G = 4

T_HOM_OK = "Les échantillons sont homogènes"
T_HOM_KO = ("Les échantillons ne sont pas homogènes; éliminer les résultats indiqués en rouge. "
            "Ne pas éliminer plus que 1 résultat")
T_STABLE = "Les échantillons sont stables"
T_UNSTABLE = "Les échantillons ne sont pas stables"
T_PARAM_OK = "Les échantillons sont homogènes et stables"
T_PARAM_KO = "Les échantillons sont soit non homogène soit non stables"
T_SOL_OK = ("Les échantillons sont homogènes et stables. L'écart maximal toléré choisi est à utiliser pour "
            "évaluer la performance des participants")
T_SOL_KO = "Les échantillons sont instables, une décision est prise suite à une réunion"


def _rows_for(set1, set2, j, nrows):
    """Lignes [résultat 1, résultat 2] du paramètre j ; un échantillon compte dès que son résultat 1 est saisi."""
    rows = []
    for i in range(nrows):
        a = set1[i][j] if i < len(set1) else None
        b = set2[i][j] if i < len(set2) else None
        if a is None:
            continue
        rows.append((i, [a] + ([b] if b is not None else [])))
    return rows


def _mode_values(rows):
    """Ordre d'Excel : tous les résultats 1 puis tous les résultats 2."""
    return [r[0] for r in rows] + [r[1] for r in rows if len(r) > 1]


def _block(rows, labels, ref, tol, ref_mode, ref_median):
    values = _mode_values(rows)
    mode, med = excel_mode(values), median(values)
    judged = []
    for lab, r in zip(labels, rows):
        dec = ["NOT OK" if abs(v - ref) > tol else "OK" for v in r]
        diff = abs(r[0] - r[1]) if len(r) > 1 else None
        judged.append({"label": lab, "values": r, "decisions": dec, "diff": diff,
                       "diff_ok": diff is None or diff <= tol})
    all_ok = all(d == "OK" for j in judged for d in j["decisions"]) and all(j["diff_ok"] for j in judged)
    return {"rows": judged, "mode": mode, "median": med, "all_ok": all_ok,
            "range_issue": any(not j["diff_ok"] for j in judged),
            "shift_mode": None if (mode is None or ref_mode is None) else abs(mode - ref_mode) > tol,
            "shift_median": abs(med - ref_median) > tol}


def _analyse(j, name, inputs, basis, tol, first_only):
    hom_in = inputs.get("hom") or {}
    pairs = _rows_for(hom_in.get("set1") or [], hom_in.get("set2") or [], j, HOM_G)
    if not pairs:
        return None
    codes = hom_in.get("codes") or []
    rows = [r for _, r in pairs]
    labels = [codes[i] if i < len(codes) and codes[i] else f"HO {i + 1:03d}" for i, _ in pairs]
    out = {"name": name, "warnings": []}
    values = _mode_values(rows)
    mode, med = excel_mode(values), median(values)
    out["mode"], out["median"] = mode, med
    ref = mode if basis == "mode" else med
    out["reference"] = ref
    if ref is None:
        out["warnings"].append("%s : aucune valeur n'est répétée, le mode n'est pas défini (choisir la médiane)." % name)
        return out
    if any(len(r) < 2 for r in rows):
        out["warnings"].append("%s : un échantillon n'a pas de résultat 2." % name)
    hom = _block(rows, labels, ref, tol, mode, med)
    hom["text"] = T_HOM_OK if hom["all_ok"] else T_HOM_KO
    hom["eliminated"] = [f"{j_['label']} (résultat {k + 1})" for j_ in hom["rows"]
                         for k, d in enumerate(j_["decisions"]) if d == "NOT OK"]
    out["hom"] = hom

    def stab(block_in):
        block_in = block_in or {}
        p = _rows_for(block_in.get("set1") or [], block_in.get("set2") or [], j, STAB_G)
        if not p:
            return None
        r = [x for _, x in p]
        b = _block(r, [f"Éch. {i + 1}" for i in range(len(r))], ref, tol, mode, med)
        shift = b["shift_mode"] if basis == "mode" else b["shift_median"]
        b["unstable"] = bool(shift)
        b["text"] = T_UNSTABLE if shift else T_STABLE
        b["after"] = b["mode"] if basis == "mode" else b["median"]
        return b

    out["stab_end"] = stab(inputs.get("stab_end"))
    st = inputs.get("storage") or {}
    tests = [stab((st.get("tests") or {}).get(k)) if st.get("active") else None for k in ("t1", "t2", "t3", "tf")]
    out["storage"] = tests
    used = tests[:1] if first_only else tests
    present = [b for b in used if b]
    tr = inputs.get("transport") or {}
    out["transport"] = stab(tr) if tr.get("active") else None
    out["stability"] = {
        "end": out["stab_end"]["text"] if out["stab_end"] else None,
        "storage": (T_UNSTABLE if any(b["unstable"] for b in present) else T_STABLE) if present else None,
        "transport": out["transport"]["text"] if out["transport"] else None,
    }
    ok = hom["all_ok"] and all(v in (None, T_STABLE) for v in out["stability"].values())
    out["ok"] = ok
    out["text"] = T_PARAM_OK if ok else T_PARAM_KO
    out["provisional"] = out["stab_end"] is None
    return out


def evaluate_solidity(inputs, excel_storage_first_only=False):
    basis = inputs.get("basis") if inputs.get("basis") in ("mode", "median") else "median"
    tol = inputs.get("tolerance")
    res = {"warnings": [], "basis": basis, "params": [], "conclusion": None}
    if tol is None:
        res["warnings"].append("La tolérance (± classes par rapport à la valeur assignée) n'est pas renseignée : elle est prise égale à 0.")
        tol = 0.0
    res["tolerance"] = tol
    names = inputs.get("params") or PARAM_NAMES
    for j in range(N_PARAMS):
        name = (names[j] if j < len(names) and names[j] else PARAM_NAMES[j])
        a = _analyse(j, name, inputs, basis, tol, excel_storage_first_only)
        res["params"].append(a)
        if a:
            res["warnings"].extend(a["warnings"])
    active = [a for a in res["params"] if a and a.get("hom")]
    if active:
        ok = all(a["ok"] for a in active)
        res["conclusion"] = {"text": T_SOL_OK if ok else T_SOL_KO, "ok": ok,
                             "provisional": any(a["provisional"] for a in active)}
        if res["conclusion"]["provisional"]:
            res["warnings"].append("L'étude de stabilité à la fin de l'essai d'aptitude (II.1) n'est pas renseignée pour tous les paramètres : la conclusion est provisoire.")
    return res
