"""Étude d'homogénéité et de stabilité — essais qualitatifs ordinaux (fiche FTE 006 v06, page 5/6).

Même logique que la feuille Excel : une valeur de référence (mode ou médiane de tous les résultats
d'homogénéité), une tolérance en nombre de classes, un jugement OK / NOT OK de chaque résultat, et la
comparaison du mode ou de la médiane après distribution, stockage et transport avec la référence."""
from homogeneity_calc import clean_rows

T_HOM_OK = "Les échantillons sont homogènes"
T_HOM_KO = ("Les échantillons ne sont pas homogènes; éliminer les résultats indiqués en rouge. "
            "Ne pas éliminer plus que 1 résultat")
T_RANGE_KO = ("L'écart entre les valeurs min et max est important vérifier ou si possible remplacer "
              "par d'autres résultats")
T_STABLE = "Les échantillons sont stables"
T_UNSTABLE = "Les échantillons ne sont pas stables"
T_FINAL_OK = "Les échantillons sont homogènes et stables"
T_FINAL_KO = "Les échantillons sont soit non homogènes soit non stables"

MAX_HOM_G = 10     # échantillons d'homogénéité (feuille Excel : HO 001 à HO 010)
MAX_STAB_G = 4     # échantillons par essai de stabilité
MAX_M = 6          # prises d'essai


def excel_mode(values):
    """MODE.SNGL d'Excel : valeur la plus fréquente ; à égalité, la première rencontrée (ordre ligne par ligne).
    None si aucune valeur n'est répétée (Excel renvoie #N/A)."""
    counts, first = {}, {}
    for i, v in enumerate(values):
        counts[v] = counts.get(v, 0) + 1
        first.setdefault(v, i)
    best = max(counts.values(), default=0)
    if best < 2:
        return None
    return min((v for v, c in counts.items() if c == best), key=lambda v: first[v])


def median(values):
    s = sorted(values)
    n = len(s)
    return s[n // 2] if n % 2 else (s[n // 2 - 1] + s[n // 2]) / 2


def _flat(rows):
    return [v for r in rows for v in r]


def judge(rows, labels, ref, tol):
    """Décision de chaque résultat (|x − référence| > tolérance → NOT OK) et écart max − min par échantillon."""
    out = []
    for label, row in zip(labels, rows):
        decisions = ["NOT OK" if (ref is not None and abs(v - ref) > tol) else ("OK" if ref is not None else None) for v in row]
        spread = max(row) - min(row)
        out.append({"label": label, "values": row, "decisions": decisions, "max": max(row), "min": min(row),
                    "spread": spread, "spread_ok": spread <= tol})
    return out


def block(rows, labels, ref, tol, ref_mode, ref_median):
    """Un essai (homogénéité ou stabilité) : jugements, mode/médiane, décisions."""
    values = _flat(rows)
    mode, med = excel_mode(values), median(values)
    judged = judge(rows, labels, ref, tol)
    all_ok = ref is not None and all(d == "OK" for j in judged for d in j["decisions"]) and all(j["spread_ok"] for j in judged)
    shift_mode = None if (mode is None or ref_mode is None) else abs(mode - ref_mode) > tol
    shift_median = abs(med - ref_median) > tol if ref_median is not None else None
    return {"g": len(rows), "m": len(rows[0]), "rows": judged, "mode": mode, "median": med, "all_ok": all_ok,
            "range_issue": any(not j["spread_ok"] for j in judged),
            "not_ok_count": sum(1 for j in judged for d in j["decisions"] if d == "NOT OK"),
            "shift_mode": shift_mode, "shift_median": shift_median}


def evaluate_ordinal(inputs):
    res = {"warnings": [], "reference": None, "mode": None, "median": None, "hom": None, "stab_end": None,
           "storage": [], "transport": None, "stability": None, "conclusion": None}
    basis = inputs.get("basis") if inputs.get("basis") in ("mode", "median") else "median"
    tol = inputs.get("tolerance")
    res["basis"] = basis
    if tol is None:
        res["warnings"].append("La tolérance (± classes par rapport à la valeur assignée) n'est pas renseignée : elle est prise égale à 0.")
        tol = 0.0
    res["tolerance"] = tol

    hom_in = inputs.get("hom") or {}
    rows, labels, _ = clean_rows(hom_in.get("rows"), hom_in.get("codes"))
    if not rows:
        return res
    m = len(rows[0])
    if any(len(r) != m for r in rows):
        res["warnings"].append("Chaque échantillon doit avoir le même nombre de résultats (m = %d)." % m)
        return res
    values = _flat(rows)
    mode, med = excel_mode(values), median(values)
    res["mode"], res["median"] = mode, med
    ref = mode if basis == "mode" else med
    res["reference"] = ref
    if ref is None:
        res["warnings"].append("Aucune valeur n'est répétée : le mode n'est pas défini. Choisissez la médiane ou complétez les résultats.")
        return res

    hom = block(rows, labels, ref, tol, mode, med)
    hom["text"] = T_HOM_OK if hom["all_ok"] else T_HOM_KO
    hom["eliminated"] = [f"{j['label']} (résultat {i + 1})" for j in hom["rows"]
                         for i, d in enumerate(j["decisions"]) if d == "NOT OK"]
    res["hom"] = hom

    def stab(rows_in):
        r, l, _ = clean_rows(rows_in)
        if not r:
            return None
        if any(len(x) != len(r[0]) for x in r):
            res["warnings"].append("Chaque échantillon d'un essai de stabilité doit avoir le même nombre de résultats.")
            return None
        b = block(r, [f"Éch. {i + 1}" for i in range(len(r))], ref, tol, mode, med)
        b["text_block"] = T_STABLE if b["all_ok"] else T_RANGE_KO
        shift = b["shift_mode"] if basis == "mode" else b["shift_median"]
        b["unstable"] = bool(shift)
        b["text"] = T_UNSTABLE if shift else T_STABLE
        b["after"] = b["mode"] if basis == "mode" else b["median"]
        return b

    res["stab_end"] = stab((inputs.get("stab_end") or {}).get("rows"))
    st = inputs.get("storage") or {}
    for key in ("rows1", "rows2", "rows3", "rows_final"):
        res["storage"].append(stab(st.get(key)) if st.get("active") else None)
    res["transport"] = stab((inputs.get("transport") or {}).get("rows")) if (inputs.get("transport") or {}).get("active") else None

    storage_present = [b for b in res["storage"] if b]
    res["stability"] = {
        "end": res["stab_end"]["text"] if res["stab_end"] else None,
        "storage": (T_UNSTABLE if any(b["unstable"] for b in storage_present) else T_STABLE) if storage_present else None,
        "transport": res["transport"]["text"] if res["transport"] else None,
    }
    ok = (hom["all_ok"]
          and all(v in (None, T_STABLE) for v in res["stability"].values()))
    res["conclusion"] = {"text": T_FINAL_OK if ok else T_FINAL_KO, "ok": ok,
                         "provisional": res["stab_end"] is None}
    if res["stab_end"] is None:
        res["warnings"].append("L'étude de stabilité à la fin de l'essai d'aptitude (II.1) n'est pas renseignée : la conclusion est provisoire.")
    return res
