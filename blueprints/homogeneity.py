import json
import math
from datetime import datetime
from pathlib import Path

from markupsafe import Markup

from flask import Blueprint, render_template, redirect, url_for, request, flash, send_file
from flask_login import login_required, current_user

from extensions import db
from models import Trial, HomogeneityStabilityAssessment, HomogeneityStudy
from utils import compute_homogeneity, compute_stability, log_action
from blueprints.auth import roles_required
import homogeneity_calc as hc
import homogeneity_ordinal as ho
import homogeneity_solidity as hs

homogeneity_bp = Blueprint("homogeneity", __name__, template_folder="../templates/homogeneity")


@homogeneity_bp.route("/trial/<int:trial_id>", methods=["GET", "POST"])
@login_required
@roles_required("admin", "chef_technique", "auditeur")
def assess(trial_id):
    """Évaluation d'homogénéité et de stabilité de l'échantillon d'essai
    (ISO 13528 Annexe B), à réaliser par l'organisateur AVANT diffusion aux
    participants (homogénéité) et comparée en fin de tour (stabilité)."""
    trial = Trial.query.get_or_404(trial_id)
    assessment = trial.homogeneity_assessment

    if request.method == "POST":
        action = request.form.get("action")

        # Récupère les g paires de réplicats saisies dynamiquement (hom_x1_0, hom_x2_0, ...)
        sample_pairs = []
        i = 0
        while f"hom_x1_{i}" in request.form:
            x1_raw = request.form.get(f"hom_x1_{i}", "").strip()
            x2_raw = request.form.get(f"hom_x2_{i}", "").strip()
            x1 = float(x1_raw) if x1_raw else None
            x2 = float(x2_raw) if x2_raw else None
            if x1 is not None or x2 is not None:
                sample_pairs.append([x1, x2])
            i += 1

        stab_first = request.form.get("stability_first_value", "").strip()
        stab_last = request.form.get("stability_last_value", "").strip()
        notes = request.form.get("notes", "").strip()

        sigma_pt = trial.target_sd  # sigma_pt de l'essai, tracé via Trial.sigma_pt_method

        hom_result = compute_homogeneity(sample_pairs, sigma_pt)
        stab_result = compute_stability(
            float(stab_first) if stab_first else None,
            float(stab_last) if stab_last else None,
            sigma_pt,
        )

        if not assessment:
            assessment = HomogeneityStabilityAssessment(trial_id=trial.id)
            db.session.add(assessment)

        assessment.sample_data = json.dumps(sample_pairs)
        assessment.s_within = hom_result["s_within"]
        assessment.s_between = hom_result["s_between"]
        assessment.homogeneity_criterion = hom_result["criterion"]
        assessment.homogeneity_conform = hom_result["conform"]

        assessment.stability_first_value = float(stab_first) if stab_first else None
        assessment.stability_last_value = float(stab_last) if stab_last else None
        assessment.stability_criterion = stab_result["criterion"]
        assessment.stability_conform = stab_result["conform"]

        assessment.sigma_pt_used = sigma_pt
        assessment.notes = notes
        assessment.assessed_by = current_user.id
        assessment.assessed_at = datetime.utcnow()

        db.session.commit()
        log_action("assess_homogeneity", "Trial", trial.id,
                   f"Évaluation homogénéité/stabilité pour {trial.name} par {current_user.username}")
        flash("Évaluation d'homogénéité/stabilité enregistrée.", "success")
        return redirect(url_for("homogeneity.assess", trial_id=trial.id))

    sample_pairs = []
    if assessment and assessment.sample_data:
        try:
            sample_pairs = json.loads(assessment.sample_data)
        except (ValueError, TypeError):
            sample_pairs = []
    if not sample_pairs:
        sample_pairs = [[None, None] for _ in range(10)]  # 10 lignes vides par défaut (g=10 usuel)

    return render_template("homogeneity/assess.html", trial=trial, assessment=assessment,
                            sample_pairs=sample_pairs)


# ═════════════════════════════════════════════════════════════════════════════
#  ÉTUDE D'HOMOGÉNÉITÉ ET DE STABILITÉ — digitalisation de la fiche FTE 006 (v06)
# ═════════════════════════════════════════════════════════════════════════════

SIGMA_REF_ROWS = 7           # lignes du tableau « σpt de TTS dans des campagnes antérieures / norme »
STUDY_ROLES = ("admin", "chef_technique", "auditeur")


def _num(raw):
    """Texte saisi -> float (virgule acceptée) ; None si vide ou invalide."""
    raw = (raw or "").strip().replace(",", ".").replace(" ", "")
    if not raw:
        return None
    try:
        value = float(raw)
    except ValueError:
        return None
    return value if math.isfinite(value) else None


def _read_grid(form, prefix, nrows, ncols):
    return [[_num(form.get(f"{prefix}_{r}_{c}")) for c in range(ncols)] for r in range(nrows)]


def _flag(raw):
    return {"yes": True, "no": False}.get(raw)


def _default_inputs(trial):
    return {
        "meta": {"campaign_ref": trial.campaign.reference if trial.campaign else "",
                 "sample_ref": getattr(trial, "sample_reference", "") or "",
                 "method": getattr(trial, "method", "") or trial.name or "",
                 "hom_date": "", "end_round_date": "", "stab_date": "", "transport_desc": ""},
        "sigma": {"mode": "relative", "direct": None, "refs": [None] * SIGMA_REF_ROWS,
                  "labels": [{"org": "", "report": "", "code": ""} for _ in range(SIGMA_REF_ROWS)]},
        "hom": {"codes": [f"HO {i + 1:03d}" for i in range(hc.MAX_G)],
                "rows": [[None] * hc.MAX_M for _ in range(hc.MAX_G)], "excluded": []},
        "trend_individual": "", "trend_means": "",
        "stab_end": {"rows": [[None] * hc.MAX_M for _ in range(hc.MAX_STAB_G)]},
        "transport": {"sensitive": None, "rows": [[None] * hc.MAX_M for _ in range(hc.MAX_STAB_G)]},
        "storage": {"at_risk": None, "rows1": [[None] * hc.MAX_M for _ in range(hc.MAX_STAB_G)],
                    "rows2": [[None] * hc.MAX_M for _ in range(hc.MAX_STAB_G)], "trend": "", "timeline": []},
    }


def _inputs_from_form(form, trial):
    base = _default_inputs(trial)
    f = form.get
    meta = {k: (f(k) or "").strip()[:200] for k in base["meta"]}
    labels = [{"org": (f(f"sigma_org_{i}") or "").strip()[:120], "report": (f(f"sigma_rep_{i}") or "").strip()[:80],
               "code": (f(f"sigma_code_{i}") or "").strip()[:80]} for i in range(SIGMA_REF_ROWS)]
    sigma = {"mode": f("sigma_mode") if f("sigma_mode") in ("relative", "absolute", "direct") else "relative",
             "direct": _num(f("sigma_direct")), "refs": [_num(f(f"sigma_ref_{i}")) for i in range(SIGMA_REF_ROWS)],
             "labels": labels}
    hom = {"codes": [(f(f"hom_code_{i}") or f"HO {i + 1:03d}").strip()[:30] for i in range(hc.MAX_G)],
           "rows": _read_grid(form, "hom", hc.MAX_G, hc.MAX_M),
           "excluded": [i for i in range(hc.MAX_G) if form.get(f"hom_excl_{i}")]}
    trend = lambda k: f(k) if f(k) in ("none", "present") else ""
    storage_timeline = []
    i = 0
    while True:
        row_keys = [
            f"storage_date_{i}", f"storage_condition_{i}", f"storage_x1_{i}", f"storage_x2_{i}", f"storage_mean_{i}",
        ]
        if not any(k in form for k in row_keys):
            break
        date_raw = (f(f"storage_date_{i}") or "").strip()
        condition = (f(f"storage_condition_{i}") or "").strip()[:80]
        x1 = _num(f(f"storage_x1_{i}"))
        x2 = _num(f(f"storage_x2_{i}"))
        mean = _num(f(f"storage_mean_{i}"))
        if mean is None and (x1 is not None or x2 is not None):
            mean = (x1 + x2) / 2 if x1 is not None and x2 is not None else (x1 if x1 is not None else x2)
        if date_raw or condition or x1 is not None or x2 is not None or mean is not None:
            storage_timeline.append({"date": date_raw, "condition": condition, "x1": x1, "x2": x2, "mean": mean})
        i += 1
    return {
        "meta": meta, "sigma": sigma, "hom": hom,
        "trend_individual": trend("trend_individual"), "trend_means": trend("trend_means"),
        "stab_end": {"rows": _read_grid(form, "stab", hc.MAX_STAB_G, hc.MAX_M)},
        "transport": {"sensitive": _flag(f("transport_sensitive")),
                      "rows": _read_grid(form, "trn", hc.MAX_STAB_G, hc.MAX_M)},
        "storage": {"at_risk": _flag(f("storage_risk")), "rows1": _read_grid(form, "sto1", hc.MAX_STAB_G, hc.MAX_M),
                    "rows2": _read_grid(form, "sto2", hc.MAX_STAB_G, hc.MAX_M), "trend": trend("storage_trend"),
                    "timeline": storage_timeline},
    }


def _merge_inputs(trial, stored):
    """Complète des données enregistrées avec les valeurs par défaut (formulaire qui évolue)."""
    base = _default_inputs(trial)
    for key, value in (stored or {}).items():
        if isinstance(value, dict) and isinstance(base.get(key), dict):
            base[key].update(value)
        else:
            base[key] = value
    return base


# ---------- graphiques SVG (sans bibliothèque externe) ----------
_PALETTE = ["#2563eb", "#dc2626", "#16a34a", "#d97706", "#7c3aed", "#0891b2"]


def _svg_scatter(series, hline=None, line=None, width=560, height=230, xlabel="Ordre de réalisation des essais"):
    """series : [(nom, [(x, y), ...])] ; hline : (y, texte) ; line : (pente, ordonnée) droite de régression."""
    pts = [p for _, s in series for p in s]
    if not pts:
        return Markup("")
    xs, ys = [p[0] for p in pts], [p[1] for p in pts]
    if hline:
        ys.append(hline[0])
    xmin, xmax = min(xs), max(xs)
    ymin, ymax = min(ys), max(ys)
    pad = (ymax - ymin) * 0.15 or (abs(ymax) * 0.01 or 1.0)
    ymin, ymax = ymin - pad, ymax + pad
    left, right, top, bottom = 58, 14, 12, 34
    sx = lambda x: left + (x - xmin) / ((xmax - xmin) or 1) * (width - left - right)
    sy = lambda y: top + (1 - (y - ymin) / (ymax - ymin)) * (height - top - bottom)
    o = [f'<svg viewBox="0 0 {width} {height}" class="w-100" style="max-width:{width}px" role="img">',
         f'<rect x="0" y="0" width="{width}" height="{height}" fill="none"/>',
         f'<line x1="{left}" y1="{top}" x2="{left}" y2="{height - bottom}" stroke="#94a3b8"/>',
         f'<line x1="{left}" y1="{height - bottom}" x2="{width - right}" y2="{height - bottom}" stroke="#94a3b8"/>']
    for k in range(5):
        yv = ymin + (ymax - ymin) * k / 4
        o.append(f'<line x1="{left}" y1="{sy(yv):.1f}" x2="{width - right}" y2="{sy(yv):.1f}" stroke="#e2e8f0"/>')
        o.append(f'<text x="{left - 6}" y="{sy(yv) + 4:.1f}" font-size="10" text-anchor="end" fill="#64748b">{yv:.4g}</text>')
    for x in sorted(set(xs)):
        o.append(f'<text x="{sx(x):.1f}" y="{height - bottom + 14}" font-size="10" text-anchor="middle" fill="#64748b">{x:g}</text>')
    o.append(f'<text x="{(left + width - right) / 2:.0f}" y="{height - 4}" font-size="10" text-anchor="middle" fill="#64748b">{xlabel}</text>')
    if hline:
        o.append(f'<line x1="{left}" y1="{sy(hline[0]):.1f}" x2="{width - right}" y2="{sy(hline[0]):.1f}" stroke="#16a34a" stroke-dasharray="5 4"/>')
        o.append(f'<text x="{width - right - 2}" y="{sy(hline[0]) - 4:.1f}" font-size="10" text-anchor="end" fill="#16a34a">{hline[1]}</text>')
    if line:
        y1, y2 = line[1] + line[0] * xmin, line[1] + line[0] * xmax
        o.append(f'<line x1="{sx(xmin):.1f}" y1="{sy(y1):.1f}" x2="{sx(xmax):.1f}" y2="{sy(y2):.1f}" stroke="#dc2626" stroke-width="1.5"/>')
    for i, (name, s) in enumerate(series):
        color = _PALETTE[i % len(_PALETTE)]
        for x, y in s:
            o.append(f'<circle cx="{sx(x):.1f}" cy="{sy(y):.1f}" r="3.2" fill="{color}"><title>{name} : {y:.5g}</title></circle>')
    legend_x = left + 6
    for i, (name, _) in enumerate(series):
        if len(series) > 1:
            o.append(f'<rect x="{legend_x}" y="{top + 2}" width="8" height="8" fill="{_PALETTE[i % len(_PALETTE)]}"/>'
                     f'<text x="{legend_x + 11}" y="{top + 10}" font-size="10" fill="#334155">{name}</text>')
            legend_x += 14 + 7 * len(name)
    o.append("</svg>")
    return Markup("".join(o))


def _charts(res):
    charts = {}
    hom = res.get("hom")
    if not hom:
        return charts
    rows = [list(r) for r in (res.get("_rows") or [])]
    if rows:
        charts["individual"] = _svg_scatter(
            [(f"Résultat {j + 1}", [(i + 1, r[j]) for i, r in enumerate(rows)]) for j in range(hom["m"])])
    tr = (res.get("trends") or {}).get("means")
    charts["means"] = _svg_scatter(
        [("Moyenne x̄t", [(i + 1, v) for i, v in enumerate(hom["means"])])],
        hline=(hom["xbar2"], "x̿ = %.5g" % hom["xbar2"]),
        line=(tr["slope"], tr["intercept"]) if tr else None)
    sm = res.get("storage_means")
    if sm and len(sm) >= 2:
        charts["storage"] = _svg_scatter([("Moyenne", [(i + 1, v) for i, v in enumerate(sm)])],
                                         xlabel="1 = avant · puis essais de stabilité · dernier = après campagne")
    return charts


def _compute(inputs):
    res = hc.evaluate(inputs)
    rows, _, idx = hc.clean_rows((inputs.get("hom") or {}).get("rows"), (inputs.get("hom") or {}).get("codes"))
    excluded = set((inputs.get("hom") or {}).get("excluded") or [])
    res["_rows"] = [r for r, i in zip(rows, idx) if i not in excluded]
    return res


def _sync_assessment(trial, res, user):
    """À la validation : reporte le résultat dans l'évaluation historique de l'essai, que la liste des
    essais et le traitement statistique consultent déjà (homogénéité / stabilité conformes ou non)."""
    hom, stab, end = res["hom"], res.get("stability"), res.get("stab_end")
    a = HomogeneityStabilityAssessment.query.filter_by(trial_id=trial.id).first()
    if a is None:
        a = HomogeneityStabilityAssessment(trial_id=trial.id)
        db.session.add(a)
    a.s_within, a.s_between = round(hom["sr"], 5), round(hom["ss"], 5)
    a.homogeneity_criterion = round(math.sqrt(hom["c"]), 5) if hom.get("c") else None
    a.homogeneity_conform = hom["outcome"] == "homogeneous"
    if end and not end.get("error"):
        a.stability_first_value, a.stability_last_value = end["mean_before"], end["mean_after"]
        a.stability_criterion = end["limit"]
    a.stability_conform = (not stab["unstable"]) if stab else None
    a.sigma_pt_used = hom["sigma_pt"]
    a.assessed_by, a.assessed_at = user.id, datetime.utcnow()


@homogeneity_bp.route("/trial/<int:trial_id>/study", methods=["GET", "POST"])
@login_required
@roles_required(*STUDY_ROLES)
def study(trial_id):
    trial = Trial.query.get_or_404(trial_id)
    if trial.trial_type == "qualitative":
        return redirect(url_for("homogeneity.study_ordinal", trial_id=trial.id))
    if trial.trial_type == "solidite":
        return redirect(url_for("homogeneity.study_solidity", trial_id=trial.id))
    if trial.trial_type != "quantitative":
        return redirect(url_for("homogeneity.assess", trial_id=trial.id))

    record = HomogeneityStudy.query.filter_by(trial_id=trial.id, kind="quantitative").first()
    can_validate = current_user.role in ("admin", "chef_technique")

    if request.method == "POST":
        action = request.form.get("action", "save")
        if record is None:
            record = HomogeneityStudy(trial_id=trial.id, kind="quantitative")
            db.session.add(record)
        if action == "reopen":
            if not can_validate:
                flash("Seul l'administrateur ou le chef technique peut rouvrir une étude validée.", "danger")
            elif record.status == "validated":
                record.status, record.validated_at, record.validated_by = "draft", None, None
                db.session.commit()
                log_action("reopen", "HomogeneityStudy", record.id, f"Étude réouverte : {trial.name}")
                flash("Étude rouverte : elle peut de nouveau être modifiée.", "info")
            return redirect(url_for("homogeneity.study", trial_id=trial.id))
        if record.status == "validated":
            flash("Cette étude est validée : rouvrez-la pour la modifier.", "warning")
            return redirect(url_for("homogeneity.study", trial_id=trial.id))

        inputs = _inputs_from_form(request.form, trial)
        res = _compute(inputs)
        record.inputs = json.dumps(inputs)
        record.result = json.dumps({k: v for k, v in res.items() if not k.startswith("_")})
        record.conclusion = (res.get("conclusion") or {}).get("text")
        record.updated_by, record.updated_at = current_user.id, datetime.utcnow()

        if action == "validate":
            if not can_validate:
                flash("Seul l'administrateur ou le chef technique peut valider l'étude.", "danger")
            elif not res.get("conclusion") or res["conclusion"].get("provisional"):
                flash("Étude incomplète : renseignez σpt, l'homogénéité et la stabilité à la fin de l'essai (II.1) "
                      "avant de valider.", "warning")
            else:
                record.status = "validated"
                record.validated_by, record.validated_at = current_user.id, datetime.utcnow()
                _sync_assessment(trial, res, current_user)
                log_action("validate", "HomogeneityStudy", record.id, f"Étude validée : {trial.name} — {record.conclusion}")
                flash("Étude validée et reportée sur l'essai.", "success")
        else:
            log_action("save", "HomogeneityStudy", record.id, f"Étude enregistrée : {trial.name}")
            flash("Étude enregistrée (brouillon) : les résultats sont recalculés ci-dessous.", "success")
        db.session.commit()
        return redirect(url_for("homogeneity.study", trial_id=trial.id))

    stored = None
    if record and record.inputs:
        try:
            stored = json.loads(record.inputs)
        except (ValueError, TypeError):
            stored = None
    inputs = _merge_inputs(trial, stored)
    res = _compute(inputs)
    return render_template("homogeneity/study.html", trial=trial, record=record, inputs=inputs, res=res,
                           charts=_charts(res), readonly=bool(record and record.status == "validated"),
                           can_validate=can_validate, max_g=hc.MAX_G, max_m=hc.MAX_M, stab_g=hc.MAX_STAB_G,
                           sigma_rows=SIGMA_REF_ROWS)


def _pdf_symbol_text(c, x, y, text, size=10, font='Helvetica', color=None):
    """Dessine le texte en basculant automatiquement vers la police Symbol pour les grecs / symboles."""
    if color is not None:
        c.setFillColor(color)
    current_font = font
    if not text:
        return x
    for ch in text:
        target_font = 'Symbol' if ch in "σμΣπΩαβγδϵε≥≤√±°µ∑∏" else font
        if target_font != current_font:
            c.setFont(target_font, size)
            current_font = target_font
        c.drawString(x, y, ch)
        x += c.stringWidth(ch, current_font, size)
    return x


def _study_payload(trial, kind):
    """Charge les données d'une étude FTE 006 sous le bon format pour export PDF/Excel."""
    record = HomogeneityStudy.query.filter_by(trial_id=trial.id, kind=kind).first()
    if kind == "quantitative":
        stored = json.loads(record.inputs) if record and record.inputs else None
        inputs = _merge_inputs(trial, stored)
        res = _compute(inputs)
    elif kind == "ordinal":
        stored = json.loads(record.inputs) if record and record.inputs else None
        inputs = _default_ordinal_inputs(trial)
        if stored:
            for key, value in stored.items():
                if isinstance(value, dict) and isinstance(inputs.get(key), dict):
                    inputs[key].update(value)
                else:
                    inputs[key] = value
        res = ho.evaluate_ordinal(inputs)
    else:
        stored = json.loads(record.inputs) if record and record.inputs else None
        inputs = _default_solidity_inputs(trial)
        if stored:
            for key, value in stored.items():
                if isinstance(value, dict) and isinstance(inputs.get(key), dict):
                    inputs[key].update(value)
                else:
                    inputs[key] = value
        res = hs.evaluate_solidity(inputs)
    return inputs, res


def _render_study_pdf(trial, kind):
    """Génère un PDF de l’étude FTE 006 sans ajouter de police externe au dépôt."""
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.units import mm
    from reportlab.pdfgen import canvas

    inputs, res = _study_payload(trial, kind)

    buffer = __import__("io").BytesIO()
    c = canvas.Canvas(buffer, pagesize=A4)
    page_w, page_h = A4
    y = page_h - 20 * mm
    c.setTitle(f"Étude FTE 006 — {trial.name}")
    c.setFont("Helvetica-Bold", 16)
    c.drawString(20 * mm, y, f"Étude d'homogénéité et de stabilité — {trial.name}")
    y -= 10 * mm
    c.setFont("Helvetica", 10)
    x = _pdf_symbol_text(c, 20 * mm, y, "FTE 006 v06 · ", size=10)
    c.drawString(x, y, f"Campagne {trial.campaign.reference if trial.campaign else ''}")
    y -= 7 * mm

    if kind == "quantitative":
        c.setFont("Helvetica-Bold", 12)
        c.drawString(20 * mm, y, "Résumé")
        y -= 7 * mm
        c.setFont("Helvetica", 10)
        x = _pdf_symbol_text(c, 20 * mm, y, "σpt : ", size=10)
        val = res.get("sigma_pt") if isinstance(res, dict) else None
        if val is None:
            val = "non renseigné"
        c.drawString(x, y, str(val))
        y -= 6 * mm
        concl = (res.get("conclusion") or {}).get("text") if isinstance(res, dict) else None
        if concl:
            c.drawString(20 * mm, y, "Conclusion :")
            y -= 6 * mm
            wrapped = []
            line = ""
            for word in concl.split():
                candidate = f"{line} {word}".strip()
                if c.stringWidth(candidate, "Helvetica", 10) > (page_w - 40 * mm):
                    wrapped.append(line)
                    line = word
                else:
                    line = candidate
            if line:
                wrapped.append(line)
            for line in wrapped[:8]:
                c.drawString(20 * mm, y, line)
                y -= 5 * mm
        else:
            c.drawString(20 * mm, y, "Aucune conclusion calculée.")
            y -= 6 * mm
    else:
        c.setFont("Helvetica-Bold", 12)
        c.drawString(20 * mm, y, "Conclusion")
        y -= 7 * mm
        c.setFont("Helvetica", 10)
        conclusion = (res.get("conclusion") or {}).get("text") or "Étude non calculée"
        for line in [conclusion[i:i+80] for i in range(0, len(conclusion), 80)][:8]:
            c.drawString(20 * mm, y, line)
            y -= 5 * mm

    if kind == "ordinal":
        c.setFont("Helvetica-Bold", 11)
        c.drawString(20 * mm, y, "Décisions par paramètre")
        y -= 7 * mm
        for item in res.get("params", [])[:8]:
            if not item:
                continue
            c.setFont("Helvetica-Bold", 9)
            c.drawString(20 * mm, y, item.get("name", "Paramètre"))
            c.setFont("Helvetica", 9)
            x = 90 * mm
            _pdf_symbol_text(c, x, y, f"réf. {item.get('reference', '')}  ")
            c.drawString(x + c.stringWidth("réf. ", "Helvetica", 9) + c.stringWidth(str(item.get('reference', '')), 'Helvetica', 9), y, f"• {item.get('text', '')}")
            y -= 5 * mm
            if y < 30 * mm:
                c.showPage(); y = page_h - 20 * mm
    elif kind == "solidity":
        c.setFont("Helvetica-Bold", 11)
        c.drawString(20 * mm, y, "Paramètres")
        y -= 7 * mm
        for item in (res.get("params") or [])[:9]:
            if not item:
                continue
            label = item.get("name", "Paramètre")
            c.setFont("Helvetica-Bold", 9)
            c.drawString(20 * mm, y, label[:60])
            c.setFont("Helvetica", 9)
            c.drawString(100 * mm, y, item.get("text", ""))
            y -= 5 * mm
            if y < 30 * mm:
                c.showPage(); y = page_h - 20 * mm

    c.setFont("Helvetica-Oblique", 8)
    c.drawString(20 * mm, 12 * mm, "Document généré depuis PTMS — police native ReportLab (sans fichier ajouté au dépôt).")
    c.save()
    return buffer.getvalue()


def _render_study_excel(trial, kind):
    """Génère un classeur Excel en reprenant le template FTE 006 fourni par l'utilisateur."""
    from io import BytesIO
    from openpyxl import load_workbook

    template_path = Path(__file__).resolve().parent.parent / "exports" / "FTE_006_homogeneity_template.xlsx"
    if not template_path.exists():
        # Fallback de sécurité pour les environnements sans template embarqué.
        from openpyxl import Workbook
        from openpyxl.styles import Font, PatternFill, Alignment

        inputs, res = _study_payload(trial, kind)
        hom_stats = (res or {}).get("hom") or {}
        conclusion_text = ((res or {}).get("conclusion") or {}).get("text")

        wb = Workbook()
        ws = wb.active
        ws.title = "Synthèse"
        header_fill = PatternFill("solid", fgColor="D9EAF7")
        title_font = Font(bold=True, color="1F1F1F")

        ws.append(["Étude FTE 006", trial.name])
        ws.append(["Type", "Quantitatif" if kind == "quantitative" else "Qualitatif ordinal" if kind == "ordinal" else "Solidité"])
        ws.append(["Campagne", trial.campaign.reference if trial.campaign else ""])
        ws.append(["Méthode", getattr(trial, "method", "") or trial.name])
        ws.append(["Généré le", datetime.utcnow().strftime("%d/%m/%Y %H:%M")])
        ws.append([])
        if kind == "quantitative":
            summary = {
                "σpt": (res or {}).get("sigma_pt"),
                "Moyenne générale x̿": hom_stats.get("xbar2"),
                "Sr": hom_stats.get("sr"),
                "Ss": hom_stats.get("ss"),
                "F1": hom_stats.get("f1"),
                "F2": hom_stats.get("f2"),
                "Conclusion": conclusion_text,
            }
            for key, value in summary.items():
                ws.append([key, value])
        else:
            ws.append(["Conclusion", conclusion_text])
        ws.append([])
        for row_idx in range(1, ws.max_row + 1):
            for col_idx in range(1, ws.max_column + 1):
                cell_obj = ws.cell(row=row_idx, column=col_idx)
                if row_idx == 1:
                    cell_obj.font = title_font
                if row_idx in (6, 12, 20, 24):
                    cell_obj.fill = header_fill
                if cell_obj.value is None:
                    cell_obj.value = ""
                cell_obj.alignment = Alignment(vertical="top", wrap_text=True)
        buf = BytesIO(); wb.save(buf); return buf.getvalue()

    inputs, _ = _study_payload(trial, kind)
    sheet_map = {
        "quantitative": "Homogénéité-stabilité-quantita ",
        "ordinal": "Homogénéité-stabilité-qualita",
        "solidity": "Homogénéité-stabilité-solidité",
    }
    sheet_name = sheet_map.get(kind)
    if not sheet_name:
        raise ValueError(f"Type d’étude non supporté pour l’export Excel : {kind}")

    wb = load_workbook(template_path, data_only=False)
    ws = wb[sheet_name]

    ws["E9"] = trial.campaign.reference if trial.campaign else ""
    ws["E10"] = getattr(trial, "sample_reference", "") or ""
    ws["E11"] = getattr(trial, "method", "") or (trial.name or "")
    ws["O12"] = "relatif" if (inputs.get("sigma", {}).get("mode") == "relative") else "absolu"

    hom_rows = (inputs.get("hom") or {}).get("rows", [])
    for i, row_values in enumerate(hom_rows[:20]):
        row_idx = 23 + i
        for col_offset, value in enumerate(row_values[:6], start=4):
            ws.cell(row=row_idx, column=col_offset, value=value)

    stability_rows = ((inputs.get("stab_end") or {}).get("rows", []))
    for i, row_values in enumerate(stability_rows[:4]):
        row_idx = 88 + i
        for col_offset, value in enumerate(row_values[:6], start=11):
            ws.cell(row=row_idx, column=col_offset, value=value)

    # S’il existe une mini table de transport / stockage dans le template, on remplit aussi les premières lignes.
    transport_rows = ((inputs.get("transport") or {}).get("rows", []))
    for i, row_values in enumerate(transport_rows[:4]):
        row_idx = 117 + i
        for col_offset, value in enumerate(row_values[:6], start=11):
            ws.cell(row=row_idx, column=col_offset, value=value)

    buf = BytesIO()
    wb.save(buf)
    return buf.getvalue()


@homogeneity_bp.route("/trial/<int:trial_id>/study-pdf")
@login_required
@roles_required(*STUDY_ROLES)
def study_pdf(trial_id):
    """Télécharge le PDF de l’étude FTE 006 pour l’essai courant."""
    trial = Trial.query.get_or_404(trial_id)
    if trial.trial_type == "quantitative":
        kind = "quantitative"
    elif trial.trial_type == "qualitative":
        kind = "ordinal"
    elif trial.trial_type == "solidite":
        kind = "solidity"
    else:
        flash("Type d’essai non pris en charge pour l’export PDF.", "warning")
        return redirect(url_for("homogeneity.assess", trial_id=trial.id))
    pdf = _render_study_pdf(trial, kind)
    filename = f"FTE_006_{trial.trial_type}_{trial.campaign.reference if trial.campaign else 'campagne'}_{trial.name}.pdf".replace("/", "-")
    return send_file(__import__("io").BytesIO(pdf), mimetype="application/pdf", as_attachment=True, download_name=filename)


@homogeneity_bp.route("/trial/<int:trial_id>/study-excel")
@login_required
@roles_required(*STUDY_ROLES)
def study_excel(trial_id):
    """Télécharge le classeur Excel complet de l’étude FTE 006 pour l’essai courant."""
    trial = Trial.query.get_or_404(trial_id)
    if trial.trial_type == "quantitative":
        kind = "quantitative"
    elif trial.trial_type == "qualitative":
        kind = "ordinal"
    elif trial.trial_type == "solidite":
        kind = "solidity"
    else:
        flash("Type d’essai non pris en charge pour l’export Excel.", "warning")
        return redirect(url_for("homogeneity.assess", trial_id=trial.id))
    xlsx = _render_study_excel(trial, kind)
    filename = f"FTE_006_{trial.trial_type}_{trial.campaign.reference if trial.campaign else 'campagne'}_{trial.name}.xlsx".replace("/", "-")
    return send_file(__import__("io").BytesIO(xlsx), mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", as_attachment=True, download_name=filename)


# ═════════════════════════════════════════════════════════════════════════════
#  ÉTUDE QUALITATIVE ORDINALE (fiche FTE 006 v06, page 5/6)
# ═════════════════════════════════════════════════════════════════════

def _blank_grid(nrows):
    return [[None] * ho.MAX_M for _ in range(nrows)]


def _default_ordinal_inputs(trial):
    return {
        "meta": {"campaign_ref": trial.campaign.reference if trial.campaign else "",
                 "sample_ref": getattr(trial, "sample_reference", "") or "",
                 "method": getattr(trial, "method", "") or trial.name or "",
                 "hom_date": "", "end_round_date": "", "stab_date": "", "eliminated_note": "", "transport_desc": ""},
        "basis": "median", "tolerance": None,
        "hom": {"codes": [f"HO {i + 1:03d}" for i in range(ho.MAX_HOM_G)], "rows": _blank_grid(ho.MAX_HOM_G)},
        "stab_end": {"rows": _blank_grid(ho.MAX_STAB_G)},
        "storage": {"active": None, "rows1": _blank_grid(ho.MAX_STAB_G), "rows2": _blank_grid(ho.MAX_STAB_G),
                    "rows3": _blank_grid(ho.MAX_STAB_G), "rows_final": _blank_grid(ho.MAX_STAB_G)},
        "transport": {"active": None, "rows": _blank_grid(ho.MAX_STAB_G)},
    }


def _ordinal_inputs_from_form(form, trial):
    base = _default_ordinal_inputs(trial)
    f = form.get
    return {
        "meta": {k: (f(k) or "").strip()[:300] for k in base["meta"]},
        "basis": f("basis") if f("basis") in ("mode", "median") else "median",
        "tolerance": _num(f("tolerance")),
        "hom": {"codes": [(f(f"hom_code_{i}") or f"HO {i + 1:03d}").strip()[:30] for i in range(ho.MAX_HOM_G)],
                "rows": _read_grid(form, "hom", ho.MAX_HOM_G, ho.MAX_M)},
        "stab_end": {"rows": _read_grid(form, "stab", ho.MAX_STAB_G, ho.MAX_M)},
        "storage": {"active": _flag(f("storage_active")),
                    "rows1": _read_grid(form, "s1", ho.MAX_STAB_G, ho.MAX_M),
                    "rows2": _read_grid(form, "s2", ho.MAX_STAB_G, ho.MAX_M),
                    "rows3": _read_grid(form, "s3", ho.MAX_STAB_G, ho.MAX_M),
                    "rows_final": _read_grid(form, "sf", ho.MAX_STAB_G, ho.MAX_M)},
        "transport": {"active": _flag(f("transport_active")), "rows": _read_grid(form, "trn", ho.MAX_STAB_G, ho.MAX_M)},
    }


def _sync_ordinal_assessment(trial, res, user):
    """Reporte l'étude ordinale validée dans l'évaluation historique de l'essai (voir _sync_assessment)."""
    hom, stab, end = res["hom"], res["stability"], res.get("stab_end")
    a = HomogeneityStabilityAssessment.query.filter_by(trial_id=trial.id).first()
    if a is None:
        a = HomogeneityStabilityAssessment(trial_id=trial.id)
        db.session.add(a)
    a.s_within = a.s_between = None
    a.homogeneity_criterion = res["tolerance"]
    a.homogeneity_conform = bool(hom["all_ok"])
    if end:
        a.stability_first_value, a.stability_last_value = res["reference"], end["after"]
        a.stability_criterion = res["tolerance"]
    a.stability_conform = all(v in (None, ho.T_STABLE) for v in stab.values())
    a.sigma_pt_used = None
    a.assessed_by, a.assessed_at = user.id, datetime.utcnow()


@homogeneity_bp.route("/trial/<int:trial_id>/study-ordinal", methods=["GET", "POST"])
@login_required
@roles_required(*STUDY_ROLES)
def study_ordinal(trial_id):
    trial = Trial.query.get_or_404(trial_id)
    if trial.trial_type != "qualitative":
        return redirect(url_for("homogeneity.study", trial_id=trial.id))
    record = HomogeneityStudy.query.filter_by(trial_id=trial.id, kind="ordinal").first()
    can_validate = current_user.role in ("admin", "chef_technique")

    if request.method == "POST":
        action = request.form.get("action", "save")
        if record is None:
            record = HomogeneityStudy(trial_id=trial.id, kind="ordinal")
            db.session.add(record)
        if action == "reopen":
            if not can_validate:
                flash("Seul l'administrateur ou le chef technique peut rouvrir une étude validée.", "danger")
            elif record.status == "validated":
                record.status, record.validated_at, record.validated_by = "draft", None, None
                db.session.commit()
                log_action("reopen", "HomogeneityStudy", record.id, f"Étude ordinale réouverte : {trial.name}")
                flash("Étude rouverte : elle peut de nouveau être modifiée.", "info")
            return redirect(url_for("homogeneity.study_ordinal", trial_id=trial.id))
        if record.status == "validated":
            flash("Cette étude est validée : rouvrez-la pour la modifier.", "warning")
            return redirect(url_for("homogeneity.study_ordinal", trial_id=trial.id))

        inputs = _ordinal_inputs_from_form(request.form, trial)
        res = ho.evaluate_ordinal(inputs)
        record.inputs = json.dumps(inputs)
        record.result = json.dumps(res)
        record.conclusion = (res.get("conclusion") or {}).get("text")
        record.updated_by, record.updated_at = current_user.id, datetime.utcnow()

        if action == "validate":
            if not can_validate:
                flash("Seul l'administrateur ou le chef technique peut valider l'étude.", "danger")
            elif not res.get("conclusion") or res["conclusion"].get("provisional"):
                flash("Étude incomplète : renseignez l'homogénéité et la stabilité à la fin de l'essai (II.1) "
                      "avant de valider.", "warning")
            else:
                record.status = "validated"
                record.validated_by, record.validated_at = current_user.id, datetime.utcnow()
                _sync_ordinal_assessment(trial, res, current_user)
                log_action("validate", "HomogeneityStudy", record.id, f"Étude ordinale validée : {trial.name} — {record.conclusion}")
                flash("Étude validée et reportée sur l'essai.", "success")
        else:
            log_action("save", "HomogeneityStudy", record.id, f"Étude ordinale enregistrée : {trial.name}")
            flash("Étude enregistrée (brouillon) : les résultats sont recalculés ci-dessous.", "success")
        db.session.commit()
        return redirect(url_for("homogeneity.study_ordinal", trial_id=trial.id))

    stored = None
    if record and record.inputs:
        try:
            stored = json.loads(record.inputs)
        except (ValueError, TypeError):
            stored = None
    inputs = _default_ordinal_inputs(trial)
    for key, value in (stored or {}).items():
        if isinstance(value, dict) and isinstance(inputs.get(key), dict):
            inputs[key].update(value)
        else:
            inputs[key] = value
    res = ho.evaluate_ordinal(inputs)
    return render_template("homogeneity/study_ordinal.html", trial=trial, record=record, inputs=inputs, res=res,
                           readonly=bool(record and record.status == "validated"), can_validate=can_validate,
                           hom_g=ho.MAX_HOM_G, stab_g=ho.MAX_STAB_G, max_m=ho.MAX_M)


# ═════════════════════════════════════════════════════════════════════════════
#  ÉTUDE DES ESSAIS DE SOLIDITÉ (fiche FTE 006 v06, page 6/6) : 9 paramètres, 2 résultats par échantillon
# ═════════════════════════════════════════════════════════════════════════════

_SOL_BLOCKS = [("t1", "s1"), ("t2", "s2"), ("t3", "s3"), ("tf", "sf")]   # (clé d'essai, préfixe des champs)


def _sol_grid(nrows):
    return [[None] * hs.N_PARAMS for _ in range(nrows)]


def _sol_pair(nrows):
    return {"set1": _sol_grid(nrows), "set2": _sol_grid(nrows)}


def _sol_param_names():
    from utils import SOLIDITE_PARAMS
    return [p["label"] for p in SOLIDITE_PARAMS]


def _default_solidity_inputs(trial):
    return {
        "meta": {"campaign_ref": trial.campaign.reference if trial.campaign else "",
                 "sample_ref": getattr(trial, "sample_reference", "") or "",
                 "method": getattr(trial, "method", "") or trial.name or "",
                 "hom_date": "", "end_round_date": "", "stab_date": "", "eliminated_note": "",
                 "transport_desc": "", "pv_note": ""},
        "basis": "median", "tolerance": None, "params": _sol_param_names(),
        "hom": {"codes": [f"HO {i + 1:03d}" for i in range(hs.HOM_G)], **_sol_pair(hs.HOM_G)},
        "stab_end": _sol_pair(hs.STAB_G),
        "storage": {"active": None, "tests": {k: _sol_pair(hs.STAB_G) for k, _ in _SOL_BLOCKS}},
        "transport": {"active": None, **_sol_pair(hs.STAB_G)},
    }


def _solidity_inputs_from_form(form, trial):
    base = _default_solidity_inputs(trial)
    f = form.get
    pair = lambda a, b, n: {"set1": _read_grid(form, a, n, hs.N_PARAMS), "set2": _read_grid(form, b, n, hs.N_PARAMS)}
    names = [(f(f"param_{j}") or base["params"][j]).strip()[:60] for j in range(hs.N_PARAMS)]
    return {
        "meta": {k: (f(k) or "").strip()[:300] for k in base["meta"]},
        "basis": f("basis") if f("basis") in ("mode", "median") else "median",
        "tolerance": _num(f("tolerance")), "params": names,
        "hom": {"codes": [(f(f"hom_code_{i}") or f"HO {i + 1:03d}").strip()[:30] for i in range(hs.HOM_G)],
                **pair("h1", "h2", hs.HOM_G)},
        "stab_end": pair("e1", "e2", hs.STAB_G),
        "storage": {"active": _flag(f("storage_active")),
                    "tests": {k: pair(p + "a", p + "b", hs.STAB_G) for k, p in _SOL_BLOCKS}},
        "transport": {"active": _flag(f("transport_active")), **pair("ta", "tb", hs.STAB_G)},
    }


def _sync_solidity_assessment(trial, res, user):
    active = [a for a in res["params"] if a and a.get("hom")]
    a = HomogeneityStabilityAssessment.query.filter_by(trial_id=trial.id).first()
    if a is None:
        a = HomogeneityStabilityAssessment(trial_id=trial.id)
        db.session.add(a)
    a.s_within = a.s_between = None
    a.homogeneity_criterion = res["tolerance"]
    a.homogeneity_conform = all(p["hom"]["all_ok"] for p in active)
    a.stability_conform = all(v in (None, hs.T_STABLE) for p in active for v in p["stability"].values())
    a.sigma_pt_used = None
    a.assessed_by, a.assessed_at = user.id, datetime.utcnow()


@homogeneity_bp.route("/trial/<int:trial_id>/study-solidity", methods=["GET", "POST"])
@login_required
@roles_required(*STUDY_ROLES)
def study_solidity(trial_id):
    trial = Trial.query.get_or_404(trial_id)
    if trial.trial_type != "solidite":
        return redirect(url_for("homogeneity.study", trial_id=trial.id))
    record = HomogeneityStudy.query.filter_by(trial_id=trial.id, kind="solidity").first()
    can_validate = current_user.role in ("admin", "chef_technique")

    if request.method == "POST":
        action = request.form.get("action", "save")
        if record is None:
            record = HomogeneityStudy(trial_id=trial.id, kind="solidity")
            db.session.add(record)
        if action == "reopen":
            if not can_validate:
                flash("Seul l'administrateur ou le chef technique peut rouvrir une étude validée.", "danger")
            elif record.status == "validated":
                record.status, record.validated_at, record.validated_by = "draft", None, None
                db.session.commit()
                log_action("reopen", "HomogeneityStudy", record.id, f"Étude solidité réouverte : {trial.name}")
                flash("Étude rouverte : elle peut de nouveau être modifiée.", "info")
            return redirect(url_for("homogeneity.study_solidity", trial_id=trial.id))
        if record.status == "validated":
            flash("Cette étude est validée : rouvrez-la pour la modifier.", "warning")
            return redirect(url_for("homogeneity.study_solidity", trial_id=trial.id))

        inputs = _solidity_inputs_from_form(request.form, trial)
        res = hs.evaluate_solidity(inputs)
        record.inputs = json.dumps(inputs)
        record.result = json.dumps(res)
        record.conclusion = (res.get("conclusion") or {}).get("text")
        record.updated_by, record.updated_at = current_user.id, datetime.utcnow()

        if action == "validate":
            if not can_validate:
                flash("Seul l'administrateur ou le chef technique peut valider l'étude.", "danger")
            elif not res.get("conclusion") or res["conclusion"].get("provisional"):
                flash("Étude incomplète : renseignez l'homogénéité et la stabilité à la fin de l'essai (II.1) "
                      "pour chaque paramètre avant de valider.", "warning")
            else:
                record.status = "validated"
                record.validated_by, record.validated_at = current_user.id, datetime.utcnow()
                _sync_solidity_assessment(trial, res, current_user)
                log_action("validate", "HomogeneityStudy", record.id, f"Étude solidité validée : {trial.name} — {record.conclusion}")
                flash("Étude validée et reportée sur l'essai.", "success")
        else:
            log_action("save", "HomogeneityStudy", record.id, f"Étude solidité enregistrée : {trial.name}")
            flash("Étude enregistrée (brouillon) : les résultats sont recalculés ci-dessous.", "success")
        db.session.commit()
        return redirect(url_for("homogeneity.study_solidity", trial_id=trial.id))

    stored = None
    if record and record.inputs:
        try:
            stored = json.loads(record.inputs)
        except (ValueError, TypeError):
            stored = None
    inputs = _default_solidity_inputs(trial)
    for key, value in (stored or {}).items():
        if isinstance(value, dict) and isinstance(inputs.get(key), dict):
            inputs[key].update(value)
        else:
            inputs[key] = value
    res = hs.evaluate_solidity(inputs)
    return render_template("homogeneity/study_solidity.html", trial=trial, record=record, inputs=inputs, res=res,
                           readonly=bool(record and record.status == "validated"), can_validate=can_validate,
                           hom_g=hs.HOM_G, stab_g=hs.STAB_G, n_params=hs.N_PARAMS)
