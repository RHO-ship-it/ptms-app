import os
import io
from datetime import datetime

from flask import Blueprint, render_template, request, send_file, current_app, flash, redirect, url_for, abort
from flask_login import login_required, current_user

from models import Campaign, Trial, ParticipantForm
from utils import z_score_interpretation, log_action, robust_stats, qualitative_consensus
from blueprints.auth import roles_required

reports_bp = Blueprint("reports", __name__, template_folder="../templates/reports")


@reports_bp.route("/")
@login_required
@roles_required("admin", "chef_technique", "auditeur")
def index():
    campaigns = Campaign.query.order_by(Campaign.created_at.desc()).all()
    return render_template("reports/export.html", campaigns=campaigns)


def _gather_report_rows(campaign):
    rows = []
    for trial in campaign.trials:
        for form in trial.forms:
            sub = form.submission
            if not sub:
                continue
            if trial.trial_type == "quantitative":
                interp, _ = z_score_interpretation(sub.z_score)
                result_display = sub.value
            else:
                if sub.qualitative_conform is True:
                    interp = "Conforme"
                elif sub.qualitative_conform is False:
                    interp = "Non conforme"
                else:
                    interp = "-"
                result_display = sub.qualitative_result
            rows.append({
                "trial": trial.name,
                "trial_type": trial.type_label(),
                "parameter": trial.parameter,
                "unit": trial.unit,
                "lab": form.laboratory.code,
                "value": result_display,
                "target_value": trial.target_value if trial.trial_type == "quantitative" else trial.expected_qualitative_result,
                "target_sd": trial.target_sd,
                "z_score": sub.z_score,
                "interpretation": interp,
                "validated": "Oui" if sub.validated else "Non",
                "submitted_at": sub.submitted_at.strftime("%Y-%m-%d %H:%M") if sub.submitted_at else "",
            })
    return rows


@reports_bp.route("/campaign/<int:campaign_id>/export/<fmt>")
@login_required
@roles_required("admin", "chef_technique", "auditeur")
def export_campaign(campaign_id, fmt):
    campaign = Campaign.query.get_or_404(campaign_id)
    rows = _gather_report_rows(campaign)

    if fmt == "excel":
        return _export_excel(campaign, rows)
    elif fmt == "pdf":
        return _export_pdf(campaign, rows)
    elif fmt == "word":
        return _export_word(campaign, rows)
    else:
        flash("Format d'export inconnu.", "danger")
        return redirect(url_for("reports.index"))


@reports_bp.route("/campaign/<int:campaign_id>/my-report")
@login_required
@roles_required("participant")
def my_report(campaign_id):
    """Rapport PDF d'un participant : uniquement les résultats de son propre
    laboratoire, et seulement une fois le rapport publié par l'administrateur."""
    campaign = Campaign.query.get_or_404(campaign_id)
    if not campaign.report_published:
        flash("Le rapport de cette campagne n'est pas encore publié.", "warning")
        return redirect(url_for("participants.portal"))
    lab = current_user.laboratory
    if lab is None:
        flash("Votre compte n'est rattaché à aucun laboratoire.", "warning")
        return redirect(url_for("dashboard.index"))
    rows = [row for row in _gather_report_rows(campaign) if row["lab"] == lab.code]
    return _export_pdf(campaign, rows)


# ═════════════════════════════════════════════════════════════════════════════
#  ESPACE PARTICIPANT : rapports et certificats (comme GSD / BIPEA)
# ═════════════════════════════════════════════════════════════════════════════

def _participant_lab():
    """Laboratoire du participant connecté (ou None)."""
    return getattr(current_user, "laboratory", None)


def _lab_campaigns(lab):
    """Campagnes auxquelles le laboratoire est inscrit (au moins un formulaire attribué)."""
    return (Campaign.query.join(Trial, Trial.campaign_id == Campaign.id)
            .join(ParticipantForm, ParticipantForm.trial_id == Trial.id)
            .filter(ParticipantForm.laboratory_id == lab.id)
            .distinct().order_by(Campaign.created_at.desc()).all())


def _lab_submissions(campaign, lab):
    """Soumissions du laboratoire pour cette campagne, avec leur essai."""
    out = []
    for trial in campaign.trials:
        for form in trial.forms:
            if form.laboratory_id == lab.id and form.submission:
                out.append((trial, form.submission))
    return out


def _published_campaign_for_lab(campaign_id):
    """Campagne publiée à laquelle le participant connecté est inscrit, sinon redirection."""
    campaign = Campaign.query.get_or_404(campaign_id)
    lab = _participant_lab()
    if lab is None:
        flash("Votre compte n'est rattaché à aucun laboratoire.", "warning")
        return None, None, redirect(url_for("dashboard.index"))
    if not campaign.report_published:
        flash("Le rapport de cette campagne n'est pas encore publié.", "warning")
        return None, None, redirect(url_for("reports.my_reports_page"))
    if campaign.id not in {c.id for c in _lab_campaigns(lab)}:
        abort(403)
    return campaign, lab, None


@reports_bp.route("/mes-rapports")
@login_required
@roles_required("participant")
def my_reports_page():
    """Page « Mes rapports » : campagnes en cours, terminées et certificats."""
    lab = _participant_lab()
    ongoing, completed, certificates = [], [], []
    if lab is not None:
        for campaign in _lab_campaigns(lab):
            subs = _lab_submissions(campaign, lab)
            item = {
                "campaign": campaign,
                "trials": len(campaign.trials),
                "submitted": len(subs),
                "has_uncertainty": any(
                    t.trial_type == "quantitative" and s.uncertainty is not None for t, s in subs),
            }
            if campaign.report_published:
                completed.append(item)
                if subs:
                    item["share_url"] = _external_url(
                        "reports.shared_certificate", token=_certificate_token(lab, campaign))
                    certificates.append(item)
            else:
                ongoing.append(item)
    return render_template("participants/reports.html", lab=lab, ongoing=ongoing,
                           completed=completed, certificates=certificates)


@reports_bp.route("/campaign/<int:campaign_id>/my-global-report")
@login_required
@roles_required("participant")
def my_global_report(campaign_id):
    """Rapport global (Word) : tous les résultats, laboratoires identifiés par leur code seul."""
    campaign, lab, redirection = _published_campaign_for_lab(campaign_id)
    if redirection:
        return redirection
    return _export_full_report_word(campaign, anonymous=True, own_lab=lab)


@reports_bp.route("/campaign/<int:campaign_id>/my-uncertainty-report")
@login_required
@roles_required("participant")
def my_uncertainty_report(campaign_id):
    """Rapport d'incertitude (PDF) : U(x) déclarée, valeur assignée, score En (ISO 13528 §9.3)."""
    campaign, lab, redirection = _published_campaign_for_lab(campaign_id)
    if redirection:
        return redirection
    rows = [(t, s) for t, s in _lab_submissions(campaign, lab)
            if t.trial_type == "quantitative" and s.uncertainty is not None]
    if not rows:
        flash("Aucune incertitude n'a été déclarée pour cette campagne.", "warning")
        return redirect(url_for("reports.my_reports_page"))
    return _uncertainty_pdf(campaign, lab, rows)


@reports_bp.route("/campaign/<int:campaign_id>/my-certificate")
@login_required
@roles_required("participant")
def my_certificate(campaign_id):
    """Certificat de participation (PDF) du laboratoire pour cette campagne."""
    campaign, lab, redirection = _published_campaign_for_lab(campaign_id)
    if redirection:
        return redirection
    subs = _lab_submissions(campaign, lab)
    if not subs:
        flash("Aucun résultat soumis : aucun certificat de participation n'est émis.", "warning")
        return redirect(url_for("reports.my_reports_page"))
    verify_url = _external_url("reports.shared_certificate", token=_certificate_token(lab, campaign))
    return _certificate_pdf(campaign, lab, subs, verify_url, as_attachment=True)


@reports_bp.route("/certificate/shared/<token>")
def shared_certificate(token):
    """Lien partageable (sans connexion) vers un certificat : la signature du lien
    garantit qu'il n'a pas été modifié ; il n'expose que le certificat lui-même."""
    from itsdangerous import BadSignature
    try:
        data = _certificate_serializer().loads(token)
        campaign = Campaign.query.get(int(data["c"]))
        from models import Laboratory
        lab = Laboratory.query.get(int(data["l"]))
    except (BadSignature, KeyError, ValueError, TypeError):
        abort(404)
    if campaign is None or lab is None or not campaign.report_published:
        abort(404)
    subs = _lab_submissions(campaign, lab)
    if not subs:
        abort(404)
    return _certificate_pdf(campaign, lab, subs, _external_url("reports.shared_certificate", token=token),
                            as_attachment=False)


def _certificate_serializer():
    from itsdangerous import URLSafeSerializer
    return URLSafeSerializer(current_app.config["SECRET_KEY"], salt="ptms-certificate")


def _certificate_token(lab, campaign):
    return _certificate_serializer().dumps({"l": lab.id, "c": campaign.id})


def _external_url(endpoint, **values):
    """URL absolue, en https dès que le site n'est pas servi en local (Render termine le TLS)."""
    url = url_for(endpoint, _external=True, **values)
    host = request.host.split(":")[0]
    if url.startswith("http://") and host not in ("localhost", "127.0.0.1"):
        url = "https://" + url[len("http://"):]
    return url


def _certificate_pdf(campaign, lab, subs, verify_url, as_attachment):
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4, landscape
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.lib.enums import TA_CENTER
    from reportlab.lib.units import cm
    from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle
    from reportlab.graphics.barcode import qr
    from reportlab.graphics.shapes import Drawing
    from xml.sax.saxutils import escape

    buf = io.BytesIO()
    page = landscape(A4)

    def _frame(canvas, _doc):
        canvas.saveState()
        canvas.setStrokeColor(colors.HexColor("#1F4E78"))
        canvas.setLineWidth(3)
        canvas.rect(1 * cm, 1 * cm, page[0] - 2 * cm, page[1] - 2 * cm)
        canvas.setLineWidth(0.8)
        canvas.rect(1.3 * cm, 1.3 * cm, page[0] - 2.6 * cm, page[1] - 2.6 * cm)
        canvas.restoreState()

    doc = SimpleDocTemplate(buf, pagesize=page, topMargin=2 * cm, bottomMargin=1.8 * cm,
                            leftMargin=2 * cm, rightMargin=2 * cm)
    center = ParagraphStyle("c", alignment=TA_CENTER, fontSize=11, leading=16)
    title = ParagraphStyle("t", alignment=TA_CENTER, fontSize=28, leading=32,
                           textColor=colors.HexColor("#1F4E78"), fontName="Helvetica-Bold")
    labname = ParagraphStyle("l", alignment=TA_CENTER, fontSize=20, leading=24, fontName="Helvetica-Bold")
    small = ParagraphStyle("s", alignment=TA_CENTER, fontSize=8, leading=11, textColor=colors.grey)

    number = f"{campaign.reference}/{lab.code}"
    issued = (campaign.end_date or datetime.now()).strftime("%d/%m/%Y")
    el = [
        Paragraph("CERTIFICATE OF PARTICIPATION", title),
        Paragraph("Certificat de participation — essais d'aptitude inter-laboratoires (ISO/IEC 17043)", center),
        Spacer(1, 0.7 * cm),
        Paragraph("This is to certify that / Il est certifié que", center),
        Spacer(1, 0.3 * cm),
        Paragraph(escape(lab.name), labname),
        Paragraph(f"Code laboratoire : {escape(lab.code)}", center),
        Spacer(1, 0.4 * cm),
        Paragraph(
            f"a participé au programme d'essais d'aptitude <b>{escape(campaign.name)}</b> "
            f"(réf. {escape(campaign.reference)}), pour les essais suivants :", center),
        Spacer(1, 0.3 * cm),
    ]
    tests = Table([["Essai", "Paramètre", "Résultat soumis le"]] + [
        [t.name, t.parameter or "-",
         s.submitted_at.strftime("%d/%m/%Y") if s.submitted_at else "-"] for t, s in subs],
        hAlign="CENTER", repeatRows=1)
    tests.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#1F4E78")),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("FONTSIZE", (0, 0), (-1, -1), 9),
        ("GRID", (0, 0), (-1, -1), 0.5, colors.grey),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.whitesmoke, colors.white]),
    ]))
    el.append(tests)
    el.append(Spacer(1, 0.4 * cm))

    widget = qr.QrCodeWidget(verify_url)
    x0, y0, x1, y1 = widget.getBounds()
    size = 2.6 * cm
    drawing = Drawing(size, size, transform=[size / (x1 - x0), 0, 0, size / (y1 - y0), 0, 0])
    drawing.add(widget)
    footer = Table([[drawing, Paragraph(
        f"Certificat n° <b>{escape(number)}</b><br/>Date d'émission : {issued}<br/>"
        "Scannez le code pour vérifier ce certificat en ligne.", ParagraphStyle("f", fontSize=9, leading=13))]],
        colWidths=[3 * cm, 10 * cm], hAlign="CENTER")
    el.append(footer)
    el.append(Spacer(1, 0.2 * cm))
    el.append(Paragraph(
        "Ce certificat atteste la participation du laboratoire. Il ne constitue pas une évaluation de sa performance, "
        "qui figure dans le rapport individuel.", small))
    doc.build(el, onFirstPage=_frame, onLaterPages=_frame)
    buf.seek(0)
    safe = f"certificat_{campaign.reference}_{lab.code}.pdf".replace("/", "-")
    return send_file(buf, as_attachment=as_attachment, download_name=safe, mimetype="application/pdf")


def _uncertainty_pdf(campaign, lab, rows):
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4, landscape
    from reportlab.lib.styles import getSampleStyleSheet
    from reportlab.lib.units import cm
    from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer

    buf = io.BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=landscape(A4), topMargin=1.5 * cm, bottomMargin=1.5 * cm)
    styles = getSampleStyleSheet()
    el = [
        Paragraph(f"Rapport d'incertitude : {campaign.reference} — {campaign.name}", styles["Title"]),
        Paragraph(f"Laboratoire {lab.code} — généré le {datetime.now().strftime('%d/%m/%Y %H:%M')}", styles["Normal"]),
        Spacer(1, 0.4 * cm),
    ]

    def fmt(v):
        return "-" if v is None else f"{v:g}"

    data = [["Essai", "Unité", "Valeur", "U(x) déclarée", "Valeur assignée", "Z-score", "En", "Évaluation En"]]
    for trial, sub in rows:
        if sub.en_score is None:
            verdict = "-"
        else:
            verdict = "Satisfaisant (|En| ≤ 1)" if abs(sub.en_score) <= 1 else "Non satisfaisant (|En| > 1)"
        data.append([trial.name, trial.unit or "-", fmt(sub.value), fmt(sub.uncertainty),
                     fmt(trial.target_value), fmt(sub.z_score),
                     "-" if sub.en_score is None else f"{sub.en_score:.2f}", verdict])
    table = Table(data, repeatRows=1)
    table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#1F4E78")),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("FONTSIZE", (0, 0), (-1, -1), 8),
        ("GRID", (0, 0), (-1, -1), 0.5, colors.grey),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.whitesmoke, colors.white]),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
    ]))
    el.append(table)
    el.append(Spacer(1, 0.4 * cm))
    el.append(Paragraph(
        "Le score En compare l'écart entre le résultat du laboratoire et la valeur assignée à l'incertitude élargie "
        "combinée (ISO 13528 §9.3). |En| ≤ 1 indique que le résultat est compatible avec la valeur assignée "
        "compte tenu de l'incertitude déclarée.", styles["Normal"]))
    doc.build(el)
    buf.seek(0)
    return send_file(buf, as_attachment=True,
                     download_name=f"rapport_incertitude_{campaign.reference}.pdf".replace("/", "-"),
                     mimetype="application/pdf")


@reports_bp.route("/campaign/<int:campaign_id>/export-fte/<template>")
@login_required
@roles_required("admin", "chef_technique", "auditeur")
def export_fte(campaign_id, template):
    """Export vers un classeur reprenant la structure des modèles internes
    FTE_004 (données qualitatives) et FTE_008 (données quantitatives).

    Remarque : le modèle de données de PTMS reste volontairement simple
    (une seule valeur soumise par formulaire). Les colonnes des modèles FTE
    qui nécessitent des informations non stockées dans PTMS (accréditation
    ISO 17025, instrument, température/humidité, réplicats détaillés...)
    sont générées vides, à compléter manuellement.
    """
    campaign = Campaign.query.get_or_404(campaign_id)
    if template not in ("fte004", "fte008"):
        flash("Modèle FTE inconnu.", "danger")
        return redirect(url_for("reports.index"))
    return _export_fte_workbook(campaign, template)


@reports_bp.route("/campaign/<int:campaign_id>/export-report-word")
@login_required
@roles_required("admin", "chef_technique", "auditeur")
def export_full_report_word(campaign_id):
    """Rapport final complet au format Word, avec la structure du rapport
    TTS Proficiency Testing (sections, tableaux, résumé statistique)."""
    campaign = Campaign.query.get_or_404(campaign_id)
    return _export_full_report_word(campaign)


def _export_excel(campaign, rows):
    from openpyxl import Workbook
    from openpyxl.styles import Font, PatternFill, Alignment
    import re

    wb = Workbook()
    wb.remove(wb.active)  # Remove default sheet

    header_fill = PatternFill(start_color="1F4E78", end_color="1F4E78", fill_type="solid")
    header_font = Font(bold=True, color="FFFFFF")

    # Group rows by trial
    trials_data = {}
    for r in rows:
        t_name = r["trial"]
        if t_name not in trials_data:
            trials_data[t_name] = []
        trials_data[t_name].append(r)

    if not trials_data:
        ws = wb.create_sheet("Vide")
        ws.append(["Aucun résultat soumis pour cette campagne."])
    else:
        for t_name, t_rows in trials_data.items():
            # Sanitize sheet name: <= 31 chars, no invalid chars
            safe_name = re.sub(r'[\\*?:/\[\]]', '', t_name)[:31]
            ws = wb.create_sheet(safe_name)

            headers = ["Essai", "Paramètre", "Unité", "Laboratoire", "Valeur soumise",
                       "Valeur assignée", "Écart-type (sigma)", "Z-score", "Interprétation", "Validé", "Date de soumission"]
            ws.append(headers)
            
            for cell in ws[1]:
                cell.font = header_font
                cell.fill = header_fill
                cell.alignment = Alignment(horizontal="center")

            for r in t_rows:
                ws.append([r["trial"], r["parameter"], r["unit"], r["lab"], r["value"],
                           r["target_value"], r["target_sd"], r["z_score"], r["interpretation"],
                           r["validated"], r["submitted_at"]])

            for col in ws.columns:
                max_len = max((len(str(c.value)) for c in col if c.value is not None), default=10)
                ws.column_dimensions[col[0].column_letter].width = min(max_len + 3, 50)

    buf = io.BytesIO()
    wb.save(buf)
    buf.seek(0)
    log_action("export", "Campaign", campaign.id, f"Export Excel de {campaign.reference}")
    return send_file(buf, as_attachment=True,
                      download_name=f"rapport_{campaign.reference}.xlsx",
                      mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")


def _export_pdf(campaign, rows):
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4, landscape
    from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer
    from reportlab.lib.styles import getSampleStyleSheet
    from reportlab.lib.units import cm

    buf = io.BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=landscape(A4), topMargin=1.5 * cm, bottomMargin=1.5 * cm)
    styles = getSampleStyleSheet()
    elements = []

    elements.append(Paragraph(f"Rapport de campagne : {campaign.reference} — {campaign.name}", styles["Title"]))
    elements.append(Paragraph(f"Généré le {datetime.now().strftime('%d/%m/%Y %H:%M')}", styles["Normal"]))
    elements.append(Spacer(1, 0.5 * cm))

    data = [["Essai", "Labo", "Valeur", "Val. assignée", "Sigma", "Z-score", "Interprétation", "Validé"]]
    for r in rows:
        data.append([r["trial"], r["lab"], r["value"], r["target_value"],
                     r["target_sd"], r["z_score"], r["interpretation"], r["validated"]])

    table = Table(data, repeatRows=1)
    table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#1F4E78")),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("FONTSIZE", (0, 0), (-1, -1), 8),
        ("GRID", (0, 0), (-1, -1), 0.5, colors.grey),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.whitesmoke, colors.white]),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
    ]))
    elements.append(table)
    doc.build(elements)
    buf.seek(0)

    log_action("export", "Campaign", campaign.id, f"Export PDF de {campaign.reference}")
    return send_file(buf, as_attachment=True,
                      download_name=f"rapport_{campaign.reference}.pdf",
                      mimetype="application/pdf")


def _export_word(campaign, rows):
    from docx import Document
    from docx.shared import Pt, Cm
    from docx.enum.text import WD_ALIGN_PARAGRAPH

    doc = Document()
    doc.add_heading(f"Rapport de campagne : {campaign.reference}", level=1)
    doc.add_paragraph(f"Nom : {campaign.name}")
    doc.add_paragraph(f"Statut : {campaign.status}")
    doc.add_paragraph(f"Généré le {datetime.now().strftime('%d/%m/%Y %H:%M')}")

    table = doc.add_table(rows=1, cols=8)
    table.style = "Light Grid Accent 1"
    hdr = table.rows[0].cells
    headers = ["Essai", "Labo", "Valeur", "Val. assignée", "Sigma", "Z-score", "Interprétation", "Validé"]
    for i, h in enumerate(headers):
        hdr[i].text = h

    for r in rows:
        cells = table.add_row().cells
        cells[0].text = str(r["trial"])
        cells[1].text = str(r["lab"])
        cells[2].text = str(r["value"])
        cells[3].text = str(r["target_value"])
        cells[4].text = str(r["target_sd"])
        cells[5].text = str(r["z_score"])
        cells[6].text = str(r["interpretation"])
        cells[7].text = str(r["validated"])

    buf = io.BytesIO()
    doc.save(buf)
    buf.seek(0)

    log_action("export", "Campaign", campaign.id, f"Export Word de {campaign.reference}")
    return send_file(buf, as_attachment=True,
                      download_name=f"rapport_{campaign.reference}.docx",
                      mimetype="application/vnd.openxmlformats-officedocument.wordprocessingml.document")


# ---------------------------------------------------------------------------
# Export vers les modèles internes FTE_004 (qualitatif) / FTE_008 (quantitatif)
# ---------------------------------------------------------------------------

def _export_fte_workbook(campaign, template):
    """Export vers un classeur reprenant la structure des modèles internes
    FTE_004 (essais qualitatifs / solidité) et FTE_008 (essais quantitatifs).

    FTE_004 reprend les essais de type "qualitative" et "solidite" de la
    campagne ; FTE_008 reprend les essais de type "quantitative". Les champs
    désormais capturés lors de la soumission (accréditation ISO 17025, dates
    de réception/essai, température/humidité, instrument, incertitude) sont
    reportés automatiquement ; seules les colonnes non couvertes par PTMS
    restent vides.
    """
    from openpyxl import Workbook
    from openpyxl.styles import Font, PatternFill, Alignment
    from openpyxl.utils import get_column_letter

    wb = Workbook()
    wb.remove(wb.active)

    header_fill = PatternFill(start_color="1F4E78", end_color="1F4E78", fill_type="solid")
    header_font = Font(bold=True, color="FFFFFF", name="Arial", size=10)
    body_font = Font(name="Arial", size=10)
    title_font = Font(bold=True, name="Arial", size=12)

    if template == "fte008":
        headers = [
            "N° participant", "Laboratory Name", "Code",
            "ISO 17025 Accreditation", "Sample Reception Date", "Test date",
            "Temperature (°C)", "Relative humidity (%)", "Standard / Method Used",
            "Reported Value", "Replicate 2", "Mean Value", "Expanded Uncertainty U(x)", "Comments",
        ]
        relevant_trials = [t for t in campaign.trials if t.trial_type == "quantitative"]
    else:
        headers = [
            "N° participant", "Laboratory Name", "Code",
            "ISO 17025 Accreditation", "Sample Reception Date", "Test date",
            "Method used", "Instrument", "Result (Grade)",
            "Uncertainty U(x) = ± Grade", "Comments",
        ]
        relevant_trials = [t for t in campaign.trials if t.trial_type in ("qualitative", "solidite")]

    if not relevant_trials:
        ws = wb.create_sheet("Aucun essai")
        msg = ("Cette campagne ne contient aucun essai quantitatif." if template == "fte008"
               else "Cette campagne ne contient aucun essai qualitatif / de solidité.")
        ws["A1"] = msg
    else:
        for trial in relevant_trials:
            # Nom d'onglet limité à 31 caractères (contrainte Excel) et nettoyé
            safe_name = "".join(c for c in trial.name if c not in '[]:*?/\\')[:31] or f"Essai_{trial.id}"
            ws = wb.create_sheet(safe_name)

            ws["A1"] = f"{trial.name} — Campagne {campaign.reference}"
            ws["A1"].font = title_font
            ws.merge_cells(start_row=1, start_column=1, end_row=1, end_column=len(headers))

            ws["A2"] = f"Paramètre : {trial.parameter or '-'}   |   Unité : {trial.unit or '-'}   |   " \
                       f"Méthode recommandée : {trial.method or '-'}   |   " \
                       f"Réf. échantillon : {trial.sample_reference or '-'}"
            ws["A2"].font = Font(italic=True, name="Arial", size=9)
            ws.merge_cells(start_row=2, start_column=1, end_row=2, end_column=len(headers))

            header_row = 4
            for col_idx, h in enumerate(headers, start=1):
                cell = ws.cell(row=header_row, column=col_idx, value=h)
                cell.font = header_font
                cell.fill = header_fill
                cell.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)

            row_idx = header_row + 1
            participant_num = 1
            for form in trial.forms:
                sub = form.submission
                lab = form.laboratory

                accreditation = ("Oui" if sub and sub.iso17025_accredited else ("Non" if sub else ""))
                reception_date = sub.sample_reception_date.strftime("%Y-%m-%d") if sub and sub.sample_reception_date else ""
                test_date = (sub.test_date.strftime("%Y-%m-%d") if sub and sub.test_date
                             else (sub.submitted_at.strftime("%Y-%m-%d") if sub and sub.submitted_at else ""))

                if template == "fte008":
                    mean_value = ""
                    if sub and sub.value is not None:
                        if sub.replicate_2 is not None:
                            mean_value = round((sub.value + sub.replicate_2) / 2, 4)
                        else:
                            mean_value = sub.value
                    values = [
                        participant_num, lab.name, lab.code,
                        accreditation, reception_date, test_date,
                        sub.temperature if sub and sub.temperature is not None else "",
                        sub.humidity if sub and sub.humidity is not None else "",
                        sub.method_used or trial.method or "" if sub else (trial.method or ""),
                        sub.value if sub and sub.value is not None else "",
                        sub.replicate_2 if sub and sub.replicate_2 is not None else "",
                        mean_value,
                        sub.uncertainty if sub and sub.uncertainty is not None else "",
                        sub.comments if sub else "",
                    ]
                else:
                    values = [
                        participant_num, lab.name, lab.code,
                        accreditation, reception_date, test_date,
                        (sub.method_used or trial.method or "") if sub else (trial.method or ""),
                        sub.instrument if sub and sub.instrument else "",
                        sub.qualitative_result if sub and sub.qualitative_result else "",
                        sub.uncertainty if sub and sub.uncertainty is not None else "",
                        sub.comments if sub else "",
                    ]

                for col_idx, v in enumerate(values, start=1):
                    cell = ws.cell(row=row_idx, column=col_idx, value=v)
                    cell.font = body_font
                row_idx += 1
                participant_num += 1

            for col_idx in range(1, len(headers) + 1):
                letter = get_column_letter(col_idx)
                ws.column_dimensions[letter].width = 20
            ws.freeze_panes = f"A{header_row + 1}"

    buf = io.BytesIO()
    wb.save(buf)
    buf.seek(0)

    log_action("export", "Campaign", campaign.id,
               f"Export modèle {template.upper()} de {campaign.reference}")
    return send_file(buf, as_attachment=True,
                      download_name=f"{template.upper()}_{campaign.reference}.xlsx",
                      mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")


# ---------------------------------------------------------------------------
# Rapport final complet au format Word (structure inspirée du rapport TTS)
# ---------------------------------------------------------------------------

def _export_full_report_word(campaign, anonymous=False, own_lab=None):
    from docx import Document
    from docx.shared import Pt, Cm, RGBColor
    from docx.enum.text import WD_ALIGN_PARAGRAPH
    from docx.enum.table import WD_TABLE_ALIGNMENT

    doc = Document()

    def _lab_label(lab):
        """Code du laboratoire ; marqué « (vous) » pour le laboratoire du lecteur."""
        label = lab.code
        if own_lab is not None and lab.id == own_lab.id:
            label += " (vous)"
        return label

    # Style de base
    normal = doc.styles["Normal"]
    normal.font.name = "Calibri"
    normal.font.size = Pt(10.5)

    # ----- Page de garde -----
    title = doc.add_heading("PROFICIENCY TESTING FINAL REPORT", level=0)
    title.alignment = WD_ALIGN_PARAGRAPH.CENTER

    p = doc.add_paragraph()
    p.alignment = WD_ALIGN_PARAGRAPH.CENTER
    run = p.add_run(f"Report Number: {campaign.reference}")
    run.bold = True
    run.font.size = Pt(14)

    p2 = doc.add_paragraph()
    p2.alignment = WD_ALIGN_PARAGRAPH.CENTER
    p2.add_run(campaign.name).italic = True

    p3 = doc.add_paragraph()
    p3.alignment = WD_ALIGN_PARAGRAPH.CENTER
    date_str = campaign.created_at.strftime("%B %Y") if campaign.created_at else ""
    p3.add_run(date_str)

    doc.add_page_break()

    # ----- 1. Scope -----
    doc.add_heading("1) Scope of Proficiency Testing", level=1)
    doc.add_paragraph(
        "This proficiency testing represents an ability test. It is used to ensure the "
        "validity of results and shows, for each testing field, the performance/ability of "
        "the laboratory to perform the requested test methods."
    )

    # ----- 2. Confidentiality -----
    doc.add_heading("2) Confidentiality Statement", level=1)
    doc.add_paragraph(
        "All data presented in this report must be regarded as confidential and for use by "
        "the participating laboratories only. Each laboratory is identified by a unique "
        "registration code. Disclosure of any information in this report to third parties is "
        "only allowed with written permission."
    )

    # ----- 3. Requested test parameters -----
    doc.add_heading("3) Requested Test Parameters", level=1)
    table = doc.add_table(rows=1, cols=3)
    table.style = "Light Grid Accent 1"
    hdr = table.rows[0].cells
    hdr[0].text, hdr[1].text, hdr[2].text = "Essai", "Paramètre / Méthode", "Unité"
    for trial in campaign.trials:
        cells = table.add_row().cells
        cells[0].text = trial.name
        cells[1].text = f"{trial.parameter or '-'} ({trial.method or '-'})"
        cells[2].text = trial.unit or "-"

    # ----- 4. Calendar -----
    doc.add_heading("4) Calendar", level=1)
    cal_table = doc.add_table(rows=1, cols=2)
    cal_table.style = "Light Grid Accent 1"
    hdr = cal_table.rows[0].cells
    hdr[0].text, hdr[1].text = "Activité", "Date"
    calendar_rows = [
        ("Création de la campagne", campaign.created_at.strftime("%d/%m/%Y") if campaign.created_at else "-"),
        ("Date de début", campaign.start_date.strftime("%d/%m/%Y") if campaign.start_date else "-"),
        ("Date de fin / échéance de soumission", campaign.end_date.strftime("%d/%m/%Y") if campaign.end_date else "-"),
        ("Statut actuel", campaign.status),
    ]
    for label, val in calendar_rows:
        cells = cal_table.add_row().cells
        cells[0].text = label
        cells[1].text = str(val)

    # ----- 5. Participants -----
    doc.add_heading("5) Participants", level=1)
    doc.add_paragraph(f"{len(campaign.laboratories)} laboratoire(s) ont participé à cette campagne :")
    for cl in campaign.laboratories:
        if anonymous:
            # Version participant : codes uniquement, jamais les noms des laboratoires.
            doc.add_paragraph(_lab_label(cl.laboratory), style="List Bullet")
        else:
            doc.add_paragraph(f"{cl.laboratory.code} — {cl.laboratory.name}", style="List Bullet")

    # ----- 6. Statistics & Results per trial -----
    doc.add_heading("6) Statistics and Results", level=1)
    doc.add_paragraph(
        "Pour chaque essai, la valeur consensuelle (x*) et l'écart-type (s*) sont estimés "
        "à partir de la médiane robuste et du MAD normalisé des résultats soumis "
        "(ou à partir de la valeur assignée et de l'écart-type définis a priori, lorsqu'ils "
        "sont disponibles). Le score utilisé pour l'évaluation de performance est le z-score : "
        "z = (xi - x*) / s*, avec |z| ≤ 2 satisfaisant, 2 < |z| < 3 douteux, |z| ≥ 3 non satisfaisant."
    )

    total_satisfactory = total_questionable = total_unsatisfactory = 0

    for trial in campaign.trials:
        doc.add_heading(f"6.{campaign.trials.index(trial) + 1}) {trial.name} ({trial.type_label()})", level=2)
        doc.add_paragraph(f"Paramètre : {trial.parameter or '-'} | Unité : {trial.unit or '-'} | "
                           f"Méthode : {trial.method or '-'}")

        submissions = [f.submission for f in trial.forms if f.submission]

        if trial.trial_type == "quantitative":
            values = [s.value for s in submissions if s.value is not None]
            median, robust_sd = robust_stats(values)

            x_star = trial.target_value if trial.target_value is not None else median
            s_star = trial.target_sd if trial.target_sd is not None else robust_sd

            doc.add_paragraph(f"Nombre de participants : {len(values)}   |   "
                               f"Valeur consensuelle x* : {x_star if x_star is not None else '-'}   |   "
                               f"Écart-type s* : {s_star if s_star is not None else '-'}")

            res_table = doc.add_table(rows=1, cols=5)
            res_table.style = "Light Grid Accent 1"
            hdr = res_table.rows[0].cells
            hdr[0].text, hdr[1].text, hdr[2].text, hdr[3].text, hdr[4].text = \
                "Laboratoire", "Valeur soumise", "Z-score", "Interprétation", "Validé"

            for s in submissions:
                interp, _ = z_score_interpretation(s.z_score)
                if interp == "Satisfaisant":
                    total_satisfactory += 1
                elif interp == "Douteux":
                    total_questionable += 1
                elif interp == "Non satisfaisant":
                    total_unsatisfactory += 1

                cells = res_table.add_row().cells
                cells[0].text = _lab_label(s.form.laboratory)
                cells[1].text = str(s.value) if s.value is not None else "-"
                cells[2].text = str(s.z_score) if s.z_score is not None else "-"
                cells[3].text = interp
                cells[4].text = "Oui" if s.validated else "Non"

        else:
            results = [s.qualitative_result for s in submissions if s.qualitative_result]
            consensus, agreement_rate, _ = qualitative_consensus(results)

            doc.add_paragraph(f"Nombre de participants : {len(results)}   |   "
                               f"Résultat consensuel : {consensus or '-'}   |   "
                               f"Taux d'accord : {agreement_rate}%")

            res_table = doc.add_table(rows=1, cols=4)
            res_table.style = "Light Grid Accent 1"
            hdr = res_table.rows[0].cells
            hdr[0].text, hdr[1].text, hdr[2].text, hdr[3].text = \
                "Laboratoire", "Résultat", "Conforme au consensus", "Validé"

            for s in submissions:
                conform = s.qualitative_conform
                if conform is None and consensus is not None and s.qualitative_result:
                    conform = s.qualitative_result.strip().lower() == consensus.strip().lower()

                if conform is True:
                    total_satisfactory += 1
                    conform_label = "Conforme"
                elif conform is False:
                    total_unsatisfactory += 1
                    conform_label = "Non conforme"
                else:
                    conform_label = "-"

                cells = res_table.add_row().cells
                cells[0].text = _lab_label(s.form.laboratory)
                cells[1].text = s.qualitative_result or "-"
                cells[2].text = conform_label
                cells[3].text = "Oui" if s.validated else "Non"

        if not submissions:
            doc.add_paragraph("Aucune soumission reçue pour cet essai.", style="Intense Quote")

    # ----- 7. Performance evaluation summary -----
    doc.add_heading("7) Performance Evaluation Summary", level=1)
    summary_table = doc.add_table(rows=1, cols=2)
    summary_table.style = "Light Grid Accent 1"
    hdr = summary_table.rows[0].cells
    hdr[0].text, hdr[1].text = "Résultat", "Nombre"
    for label, val in [("Satisfaisant", total_satisfactory),
                       ("Douteux", total_questionable),
                       ("Non satisfaisant", total_unsatisfactory)]:
        cells = summary_table.add_row().cells
        cells[0].text = label
        cells[1].text = str(val)

    # ----- 8. Recommendations -----
    doc.add_heading("8) Recommendations", level=1)
    doc.add_paragraph(
        "Il est recommandé aux laboratoires ayant obtenu un résultat douteux ou non "
        "satisfaisant de vérifier l'exactitude de leur analyse, notamment à l'aide de "
        "matériaux de référence, et de mettre en place un suivi régulier de leur qualité "
        "analytique (cartes de contrôle, matériaux de référence, participation régulière "
        "à des essais d'aptitude)."
    )

    # ----- 9. Conclusion -----
    doc.add_heading("9) Conclusion", level=1)
    doc.add_paragraph(
        "La variation observée entre les participants de cette campagne peut résulter de la "
        "performance de l'analyse. Chaque participant est invité à évaluer sa propre "
        "performance et à décider des actions correctives nécessaires."
    )

    buf = io.BytesIO()
    doc.save(buf)
    buf.seek(0)

    log_action("export", "Campaign", campaign.id, f"Export rapport complet Word de {campaign.reference}")
    return send_file(buf, as_attachment=True,
                      download_name=f"rapport_{'global' if anonymous else 'complet'}_{campaign.reference}.docx",
                      mimetype="application/vnd.openxmlformats-officedocument.wordprocessingml.document")
