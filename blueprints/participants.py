import io
import json
import os
import uuid
from datetime import date, datetime

from flask import (Blueprint, render_template, redirect, url_for, request, flash, abort,
                    send_file, current_app, send_from_directory)
from flask_login import login_required, current_user
from werkzeug.utils import secure_filename

from extensions import db
from models import ParticipantForm, Submission, Trial, Campaign, Laboratory, User
from scoring import fixed_scores
from utils import (log_action, notify, notify_role, compute_z_score, z_score_interpretation,
                    qualitative_consensus, compute_en, SOLIDITE_PARAMS, parse_solidite_data)
from blueprints.auth import roles_required

ALLOWED_ATTACHMENT_EXT = {"pdf", "jpg", "jpeg", "png", "xlsx", "xls", "docx", "csv"}

participants_bp = Blueprint("participants", __name__, template_folder="../templates/participants")


# ---------- Portail participant ----------

@participants_bp.route("/portal")
@login_required
@roles_required("participant")
def portal():
    if not current_user.laboratory_id:
        flash("Votre compte n'est rattaché à aucun laboratoire.", "warning")
        return redirect(url_for("dashboard.index"))

    campaign_id = request.args.get("campaign_id", type=int)
    campaigns = (Campaign.query
                 .join(Trial)
                 .join(ParticipantForm)
                 .filter(ParticipantForm.laboratory_id == current_user.laboratory_id)
                 .distinct()
                 .all())

    selected_campaign = None
    if campaign_id:
        selected_campaign = Campaign.query.get(campaign_id)
    elif campaigns:
        selected_campaign = next((c for c in campaigns if c.reference == 'TTS-A2601'), campaigns[0])

    forms_query = ParticipantForm.query.filter_by(laboratory_id=current_user.laboratory_id).join(Trial)
    if selected_campaign:
        forms_query = forms_query.filter(Trial.campaign_id == selected_campaign.id)

    forms = forms_query.all()
    today = date.today()

    # Colis du laboratoire pour la campagne (un colis, plusieurs essais)
    parcel = groups = pstate = closing = None
    if selected_campaign and forms:
        from blueprints.parcels import get_parcel, trial_groups, parcel_state
        from blueprints.sheets import load_config, sheet_key_of
        parcel = get_parcel(selected_campaign.id, current_user.laboratory_id)
        if parcel:
            cfg = load_config(selected_campaign)
            groups = trial_groups(forms)
            for g in groups:
                g["tab"] = sheet_key_of(cfg, g["forms"][0].trial) or "identification"
            pstate = parcel_state(parcel, forms)
            dues = [f.trial.due_date for f in forms if f.trial.due_date]
            closing = min(dues) if dues else None
    return render_template("participants/portal.html",
                           parcel=parcel, groups=groups, pstate=pstate, closing=closing,
                           forms=forms,
                           campaigns=campaigns,
                           selected_campaign=selected_campaign,
                           today=today)


@participants_bp.route("/form/<int:form_id>/submit", methods=["GET", "POST"])
@login_required
@roles_required("participant")
def submit_form(form_id):
    form = ParticipantForm.query.get_or_404(form_id)
    if form.laboratory_id != current_user.laboratory_id:
        flash("Accès non autorisé à ce formulaire.", "danger")
        return redirect(url_for("participants.portal"))

    if request.method == "GET":   # la saisie se fait désormais dans la feuille de la campagne
        from blueprints.sheets import load_config, sheet_key_of
        cfg = load_config(form.trial.campaign)
        return redirect(url_for("sheets.entry", campaign_id=form.trial.campaign_id,
                                tab=sheet_key_of(cfg, form.trial) or "identification"))

    if request.method == "POST":
        trial_type = form.trial.trial_type
        method_used = request.form.get("method_used", "").strip()
        comments = request.form.get("comments", "").strip()
        instrument = request.form.get("instrument", "").strip()
        iso17025 = request.form.get("iso17025_accredited") == "on"
        temperature = request.form.get("temperature", "")
        humidity = request.form.get("humidity", "")
        uncertainty = request.form.get("uncertainty", "")
        sample_reception_date_str = request.form.get("sample_reception_date", "")
        test_date_str = request.form.get("test_date", "")
        sample_reception_date = (datetime.strptime(sample_reception_date_str, "%Y-%m-%d").date()
                                  if sample_reception_date_str else None)

        attachment_path = form.submission.attachment_path if form.submission else None
        attachment_filename = form.submission.attachment_filename if form.submission else None
        upload_file = request.files.get("attachment")
        if upload_file and upload_file.filename:
            ext = upload_file.filename.rsplit(".", 1)[-1].lower() if "." in upload_file.filename else ""
            if ext not in ALLOWED_ATTACHMENT_EXT:
                flash("Type de fichier non autorisé. Formats acceptés : PDF, JPG, PNG, XLSX, XLS, DOCX, CSV.", "danger")
                return redirect(url_for("participants.submit_form", form_id=form.id))
            subdir = os.path.join(current_app.config["UPLOAD_FOLDER"], "submissions", str(form.id))
            os.makedirs(subdir, exist_ok=True)
            stored_name = f"{uuid.uuid4().hex}.{ext}"
            upload_file.save(os.path.join(subdir, stored_name))
            attachment_path = os.path.join("submissions", str(form.id), stored_name)
            attachment_filename = secure_filename(upload_file.filename)
        test_date = datetime.strptime(test_date_str, "%Y-%m-%d").date() if test_date_str else None

        value = None
        replicate_2 = None
        qualitative_result = None
        solidite_data = None
        z = None
        en = None
        anomaly = False
        qualitative_conform = None

        if trial_type == "quantitative":
            value = float(request.form["value"])
            replicate_2_str = request.form.get("replicate_2", "")
            replicate_2 = float(replicate_2_str) if replicate_2_str else None
            uncertainty_str = request.form.get("uncertainty", "")
            lab_uncertainty = float(uncertainty_str) if uncertainty_str else None
            # z (ou z' si u(x_pt) > 0,3 sigma_pt) et En avec la convention ISO 13528 (scoring.py)
            z, anomaly, en = fixed_scores(form.trial, value, lab_uncertainty)
        elif trial_type == "solidite":
            values = {}
            for prm in SOLIDITE_PARAMS:
                raw = request.form.get(f"sol_{prm['key']}", "")
                values[prm["key"]] = float(raw) if raw.strip() != "" else None
            solidite_data = json.dumps(values)
            numeric_values = [v for v in values.values() if v is not None]
            qualitative_result = f"{min(numeric_values):.1f}" if numeric_values else None
            if form.trial.expected_qualitative_result and numeric_values:
                try:
                    threshold = float(form.trial.expected_qualitative_result.replace(",", "."))
                    qualitative_conform = min(numeric_values) >= threshold
                    anomaly = not qualitative_conform
                except ValueError:
                    pass
        else:
            qualitative_result = request.form.get("qualitative_result", "").strip()
            if form.trial.expected_qualitative_result:
                qualitative_conform = (qualitative_result.strip().lower()
                                        == form.trial.expected_qualitative_result.strip().lower())
                anomaly = qualitative_conform is False

        if form.submission:
            sub = form.submission
            sub.value = value
            sub.replicate_2 = replicate_2
            sub.qualitative_result = qualitative_result
            sub.solidite_data = solidite_data
            sub.qualitative_conform = qualitative_conform
            sub.method_used = method_used
            sub.instrument = instrument
            sub.iso17025_accredited = iso17025
            sub.temperature = float(temperature) if temperature else None
            sub.humidity = float(humidity) if humidity else None
            sub.uncertainty = float(uncertainty) if uncertainty else None
            sub.sample_reception_date = sample_reception_date
            sub.test_date = test_date
            sub.comments = comments
            sub.attachment_path = attachment_path
            sub.attachment_filename = attachment_filename
            sub.submitted_at = datetime.utcnow()
            sub.z_score = z
            sub.en_score = en
            sub.anomaly_flag = anomaly
            sub.validated = False
            sub.confirmed = False
            sub.confirmed_at = None
        else:
            sub = Submission(
                form_id=form.id,
                submitted_by=current_user.id,
                value=value,
                replicate_2=replicate_2,
                qualitative_result=qualitative_result,
                solidite_data=solidite_data,
                qualitative_conform=qualitative_conform,
                method_used=method_used,
                instrument=instrument,
                iso17025_accredited=iso17025,
                temperature=float(temperature) if temperature else None,
                humidity=float(humidity) if humidity else None,
                uncertainty=float(uncertainty) if uncertainty else None,
                sample_reception_date=sample_reception_date,
                test_date=test_date,
                comments=comments,
                attachment_path=attachment_path,
                attachment_filename=attachment_filename,
                z_score=z,
                en_score=en,
                anomaly_flag=anomaly,
            )
            db.session.add(sub)

        form.status = "anomaly" if anomaly else "submitted"
        db.session.commit()

        log_action("submit", "Submission", sub.id,
                    f"Soumission résultat pour essai {form.trial.name} par {current_user.username}")
        notify_role("chef_technique", f"Nouvelle soumission de {form.laboratory.code} pour {form.trial.name}",
                    link=url_for("participants.validation_center"))

        flash("Résultat enregistré. Merci de vérifier puis confirmer votre soumission.", "success")
        return redirect(url_for("participants.confirm_submission", form_id=form.id))

    existing_solidite = parse_solidite_data(form.submission.solidite_data) if form.submission else {}
    return render_template("participants/submit.html", form=form,
                            solidite_params=SOLIDITE_PARAMS, existing_solidite=existing_solidite)


@participants_bp.route("/form/<int:form_id>/attachment")
@login_required
@roles_required("participant", "admin", "chef_technique", "auditeur")
def download_attachment(form_id):
    form = ParticipantForm.query.get_or_404(form_id)
    if current_user.role == "participant" and form.laboratory_id != current_user.laboratory_id:
        flash("Accès non autorisé à ce formulaire.", "danger")
        return redirect(url_for("participants.portal"))
    if not form.submission or not form.submission.attachment_path:
        flash("Aucune pièce jointe pour cette soumission.", "warning")
        return redirect(url_for("participants.portal"))

    directory = os.path.join(current_app.config["UPLOAD_FOLDER"], os.path.dirname(form.submission.attachment_path))
    filename = os.path.basename(form.submission.attachment_path)
    return send_from_directory(directory, filename, as_attachment=True,
                                download_name=form.submission.attachment_filename or filename)


# ---------- Étape de confirmation (validation finale par le participant) ----------

@participants_bp.route("/form/<int:form_id>/confirm", methods=["GET", "POST"])
@login_required
@roles_required("participant")
def confirm_submission(form_id):
    form = ParticipantForm.query.get_or_404(form_id)
    if form.laboratory_id != current_user.laboratory_id:
        flash("Accès non autorisé à ce formulaire.", "danger")
        return redirect(url_for("participants.portal"))
    if not form.submission:
        flash("Aucun résultat n'a encore été saisi pour cet essai.", "warning")
        return redirect(url_for("participants.submit_form", form_id=form.id))

    if request.method == "POST":
        sub = form.submission
        sub.confirmed = True
        sub.confirmed_at = datetime.utcnow()
        db.session.commit()
        log_action("confirm", "Submission", sub.id,
                    f"Confirmation finale de la soumission pour {form.trial.name} par {current_user.username}")
        flash("Votre soumission a été confirmée et finalisée. Vous pouvez télécharger votre récapitulatif PDF.", "success")
        return redirect(url_for("participants.portal"))

    return render_template("participants/confirm.html", form=form)


# ---------- Récapitulatif PDF téléchargeable ----------

@participants_bp.route("/form/<int:form_id>/summary-pdf")
@login_required
@roles_required("participant", "admin", "chef_technique", "auditeur")
def submission_summary_pdf(form_id):
    form = ParticipantForm.query.get_or_404(form_id)
    if current_user.role == "participant" and form.laboratory_id != current_user.laboratory_id:
        flash("Accès non autorisé à ce formulaire.", "danger")
        return redirect(url_for("participants.portal"))
    if not form.submission:
        flash("Aucun résultat n'a encore été saisi pour cet essai.", "warning")
        return redirect(url_for("participants.portal"))

    from reportlab.lib.pagesizes import A4
    from reportlab.lib.units import mm
    from reportlab.lib import colors
    from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer
    from reportlab.lib.styles import getSampleStyleSheet

    sub = form.submission
    lab = form.laboratory
    trial = form.trial

    buf = io.BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=A4, topMargin=20 * mm, bottomMargin=20 * mm)
    styles = getSampleStyleSheet()
    elements = [
        Paragraph("Récapitulatif de soumission — Essai d'aptitude", styles["Title"]),
        Spacer(1, 8),
        Paragraph(f"Campagne : {trial.campaign.reference} — {trial.campaign.name}", styles["Normal"]),
        Paragraph(f"Essai : {trial.name} ({trial.type_label()})", styles["Normal"]),
        Spacer(1, 12),
    ]

    contact_data = [
        ["Laboratoire", lab.name],
        ["Code", lab.code],
        ["Contact", lab.contact_name or "-"],
        ["Email", lab.email or "-"],
        ["Téléphone", lab.phone or "-"],
    ]
    t1 = Table(contact_data, colWidths=[120, 320])
    t1.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (0, -1), colors.HexColor("#1F4E78")),
        ("TEXTCOLOR", (0, 0), (0, -1), colors.white),
        ("FONTSIZE", (0, 0), (-1, -1), 9),
        ("GRID", (0, 0), (-1, -1), 0.5, colors.grey),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
    ]))
    elements += [t1, Spacer(1, 16)]

    result_display = sub.qualitative_result if sub.qualitative_result else sub.value
    result_data = [
        ["Résultat soumis", str(result_display) if result_display is not None else "-"],
        ["Réplicat 2", str(sub.replicate_2) if sub.replicate_2 is not None else "-"],
        ["Date de réception de l'échantillon", sub.sample_reception_date.strftime("%d/%m/%Y") if sub.sample_reception_date else "-"],
        ["Date de l'essai", sub.test_date.strftime("%d/%m/%Y") if sub.test_date else "-"],
        ["Méthode utilisée", sub.method_used or "-"],
        ["Instrument", sub.instrument or "-"],
        ["Accréditation ISO 17025", "Oui" if sub.iso17025_accredited else "Non"],
        ["Pièce jointe", sub.attachment_filename or "-"],
        ["Date de soumission", sub.submitted_at.strftime("%d/%m/%Y %H:%M") if sub.submitted_at else "-"],
        ["Statut de confirmation", "Confirmé le " + sub.confirmed_at.strftime("%d/%m/%Y %H:%M") if sub.confirmed and sub.confirmed_at else "Non confirmé"],
        ["Commentaires", sub.comments or "-"],
    ]
    t2 = Table(result_data, colWidths=[180, 260])
    t2.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (0, -1), colors.HexColor("#E9EEF3")),
        ("FONTSIZE", (0, 0), (-1, -1), 9),
        ("GRID", (0, 0), (-1, -1), 0.5, colors.grey),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
    ]))
    elements += [t2, Spacer(1, 20)]
    elements.append(Paragraph(
        "Ce document est une confirmation des données saisies par le laboratoire participant. "
        "Il ne préjuge pas de l'évaluation statistique de performance, réalisée séparément.",
        styles["Italic"]))

    doc.build(elements)
    buf.seek(0)
    return send_file(buf, as_attachment=True,
                      download_name=f"recapitulatif_{lab.code}_{trial.name}.pdf",
                      mimetype="application/pdf")


# ---------- Mes coordonnées (accès participant restreint à ses coordonnées de contact) ----------

@participants_bp.route("/mes-coordonnees", methods=["GET", "POST"])
@login_required
@roles_required("participant")
def my_contact_info():
    if not current_user.laboratory_id:
        flash("Votre compte n'est rattaché à aucun laboratoire.", "warning")
        return redirect(url_for("dashboard.index"))

    lab = Laboratory.query.get_or_404(current_user.laboratory_id)

    if request.method == "POST":
        lab.contact_name = request.form.get("contact_name", "").strip()
        lab.email = request.form.get("email", "").strip()
        lab.phone = request.form.get("phone", "").strip()
        lab.address = request.form.get("address", "").strip()
        db.session.commit()
        log_action("update", "Laboratory", lab.id,
                    f"Mise à jour des coordonnées de contact par {current_user.username}")
        flash("Vos coordonnées ont été mises à jour.", "success")
        return redirect(url_for("participants.my_contact_info"))

    return render_template("participants/contact.html", lab=lab)


# ---------- Submission tracker (qui a soumis / manque / en retard) ----------

@participants_bp.route("/tracker")
@participants_bp.route("/tracker/campaign/<int:campaign_id>")
@login_required
@roles_required("admin", "chef_technique", "auditeur")
def tracker(campaign_id=None):
    campaigns = Campaign.query.order_by(Campaign.created_at.desc()).all()
    selected_campaign = None
    rows = []
    today = date.today()

    if campaign_id:
        selected_campaign = Campaign.query.get_or_404(campaign_id)
    elif campaigns:
        selected_campaign = campaigns[0]

    if selected_campaign:
        forms = (ParticipantForm.query
                 .join(Trial)
                 .filter(Trial.campaign_id == selected_campaign.id)
                 .all())
        for f in forms:
            is_late = f.status == "pending" and f.trial.due_date and f.trial.due_date < today
            rows.append({
                "form": f,
                "lab": f.laboratory.code,
                "trial": f.trial.name,
                "status": f.status,
                "late": is_late,
                "due_date": f.trial.due_date,
                "form_id": f.id,
            })

    return render_template("participants/tracker.html", campaigns=campaigns,
                            selected_campaign=selected_campaign, rows=rows)


# ---------- Validation Center ----------

@participants_bp.route("/validation")
@login_required
@roles_required("admin", "chef_technique", "auditeur")
def validation_center():
    status_filter  = request.args.get("status", "")
    campaign_id    = request.args.get("campaign_id", type=int)
    trial_id       = request.args.get("trial_id", type=int)

    # Liste de toutes les campagnes pour le dropdown
    all_campaigns = Campaign.query.order_by(Campaign.created_at.desc()).all()

    # Si aucune campagne sélectionnée, prendre la première active
    selected_campaign = None
    if campaign_id:
        selected_campaign = Campaign.query.get(campaign_id)
    elif all_campaigns:
        selected_campaign = next((c for c in all_campaigns if c.status == "active"), all_campaigns[0])

    query = (ParticipantForm.query
             .join(Submission)
             .join(Trial)
             .filter(ParticipantForm.status.in_(["submitted", "anomaly", "validated"])))

    if selected_campaign:
        query = query.filter(Trial.campaign_id == selected_campaign.id)

    if trial_id:
        query = query.filter(Trial.id == trial_id)

    if status_filter:
        query = query.filter(ParticipantForm.status == status_filter)

    forms = query.all()

    # Moyenne des résultats quantitatifs déjà soumis, par essai
    trial_averages = {}
    for f in forms:
        if f.trial.trial_type == "quantitative" and f.trial_id not in trial_averages:
            all_forms_for_trial = ParticipantForm.query.filter_by(trial_id=f.trial_id).join(Submission).all()
            values = [af.submission.value for af in all_forms_for_trial if af.submission and af.submission.value is not None]
            trial_averages[f.trial_id] = round(sum(values) / len(values), 4) if values else None

    items = []
    for f in forms:
        items.append({
            "form": f,
            "average": trial_averages.get(f.trial_id),
        })

    return render_template("participants/validation.html",
                           items=items,
                           status_filter=status_filter,
                           trial_id=trial_id,
                           all_campaigns=all_campaigns,
                           selected_campaign=selected_campaign)


@participants_bp.route("/validation/<int:form_id>/validate", methods=["POST"])
@login_required
@roles_required("admin", "chef_technique")
def validate_submission(form_id):
    form = ParticipantForm.query.get_or_404(form_id)
    campaign_id = request.form.get("campaign_id", type=int)
    if not form.submission:
        flash("Aucune soumission à valider pour ce formulaire.", "warning")
        return redirect(url_for("participants.validation_center", campaign_id=campaign_id))

    form.submission.validated = True
    form.submission.validated_by = current_user.id
    form.submission.validated_at = datetime.utcnow()
    form.status = "validated"
    db.session.commit()

    log_action("validate", "Submission", form.submission.id,
               f"Validation de la soumission pour {form.trial.name} / {form.laboratory.code}")
    flash("Soumission validée.", "success")
    return redirect(url_for("participants.validation_center", campaign_id=campaign_id))


@participants_bp.route("/validation/<int:form_id>/reject", methods=["POST"])
@login_required
@roles_required("admin", "chef_technique")
def reject_submission(form_id):
    form = ParticipantForm.query.get_or_404(form_id)
    campaign_id = request.args.get("campaign_id", type=int)
    reason = request.form.get("reason", "").strip()
    form.status = "anomaly"
    if form.submission:
        form.submission.validated = False
    db.session.commit()

    log_action("reject", "Submission", form.id,
               f"Rejet de la soumission pour {form.trial.name} / {form.laboratory.code} : {reason}")
    # Notifier uniquement le(s) compte(s) du laboratoire concerné (et non tous les participants)
    for lab_user in User.query.filter_by(laboratory_id=form.laboratory_id, role="participant").all():
        notify(lab_user.id, f"Votre soumission pour {form.trial.name} nécessite une correction.",
               link=url_for("participants.portal"))
    flash("Soumission rejetée, le laboratoire a été notifié.", "info")
    return redirect(url_for("participants.validation_center", campaign_id=campaign_id))


@participants_bp.route("/validation/mass-validate", methods=["POST"])
@login_required
@roles_required("admin", "chef_technique")
def mass_validate():
    form_ids_str = request.form.get("form_ids", "")
    if not form_ids_str:
        flash("Aucune soumission sélectionnée.", "warning")
        return redirect(url_for("participants.validation_center"))
        
    ids = [int(x) for x in form_ids_str.split(",") if x.strip().isdigit()]
    if not ids:
        flash("Aucune soumission valide sélectionnée.", "warning")
        return redirect(url_for("participants.validation_center"))
        
    forms = ParticipantForm.query.filter(ParticipantForm.id.in_(ids)).all()
    count = 0
    for form in forms:
        if form.submission and form.status != "validated":
            form.submission.validated = True
            form.submission.validated_by = current_user.id
            form.submission.validated_at = datetime.utcnow()
            form.status = "validated"
            count += 1
            log_action("validate", "Submission", form.submission.id,
                       f"Validation en masse pour {form.trial.name} / {form.laboratory.code}")
                       
    db.session.commit()
    flash(f"{count} soumissions validées avec succès.", "success")
    return redirect(url_for("participants.validation_center"))


@participants_bp.route("/validation/export")
@login_required
@roles_required("admin", "chef_technique", "auditeur")
def export_validation():
    import csv
    from flask import Response
    
    status_filter = request.args.get("status", "")
    query = (ParticipantForm.query
             .join(Submission)
             .filter(ParticipantForm.status.in_(["submitted", "anomaly", "validated"])))
    if status_filter:
        query = query.filter(ParticipantForm.status == status_filter)
        
    forms = query.all()
    
    def generate():
        yield "Campagne;Essai;Laboratoire;Code Labo;Resultat;Replicat 2;Unite;Statut Validation;Date Soumission;Commentaires\n"
        for f in forms:
            s = f.submission
            if not s:
                continue
            
            val = str(s.value) if s.value is not None else (s.qualitative_result or "")
            repl = str(s.replicate_2) if s.replicate_2 is not None else ""
            unit = f.trial.unit or ""
            date_soum = s.submitted_at.strftime('%d/%m/%Y %H:%M') if s.submitted_at else ""
            
            row = [
                f.trial.campaign.reference,
                f.trial.name,
                f.laboratory.name,
                f.laboratory.code,
                val,
                repl,
                unit,
                f.status,
                date_soum,
                s.comments or ""
            ]
            # Handle possible semicolons in data
            row_clean = [str(x).replace(";", ",") for x in row]
            yield ";".join(row_clean) + "\n"
            
    response = Response(generate(), mimetype="text/csv")
    response.headers["Content-Disposition"] = "attachment; filename=export_validation.csv"
    return response



# ---------- LOGISTIQUE COLIS & SCAN QR CODE ----------

@participants_bp.route("/scan-qr/<qr_token>", methods=["GET", "POST"])
def scan_qr(qr_token):
    """Page publique (QR du colis) : informations du colis et accusé de réception horodaté."""
    from blueprints.parcels import find_parcel_by_token, receive_parcel, trial_groups, lab_forms
    parcel = find_parcel_by_token(qr_token)
    if parcel is None:
        abort(404)
    if request.method == "POST":
        if not parcel.received:
            receive_parcel(parcel, f"QR code ({parcel.laboratory.code})")
        flash(f"✅ Réception du colis confirmée le {parcel.reception_date.strftime('%d/%m/%Y à %H:%M:%S')}.", "success")
        return redirect(url_for("participants.scan_qr", qr_token=qr_token))
    groups = trial_groups(lab_forms(parcel.campaign_id, parcel.laboratory_id))
    dues = [g["forms"][0].trial.due_date for g in groups if g["forms"][0].trial.due_date]
    return render_template("parcels/scan.html", parcel=parcel, groups=groups, due=min(dues) if dues else None)


@participants_bp.route("/form/<int:form_id>/confirm-reception", methods=["POST"])
@login_required
def confirm_reception(form_id):
    """Validation manuelle de réception du colis."""
    form = ParticipantForm.query.get_or_404(form_id)

    # Vérification accès (admin, chef_technique, ou le labo concerné)
    if not current_user.is_staff() and current_user.laboratory_id != form.laboratory_id:
        flash("Accès non autorisé.", "danger")
        return redirect(url_for("participants.portal"))

    form.received = True
    form.reception_date = datetime.utcnow()
    db.session.commit()
    from blueprints.parcels import sync_from_form
    sync_from_form(form)

    log_action("confirm_reception", entity_type="ParticipantForm", entity_id=form.id,
               details=f"Réception du colis pour {form.trial.name} confirmée par {current_user.full_name}")

    flash(f"Réception du colis enregistrée le {form.reception_date.strftime('%d/%m/%Y à %H:%M')}.", "success")
    return redirect(request.referrer or url_for("participants.portal"))


@participants_bp.route("/form/<int:form_id>/update-shipping", methods=["POST"])
@login_required
@roles_required("admin", "chef_technique")
def update_shipping(form_id):
    """Mise à jour des infos d'expédition : code échantillon, transporteur, tracking, photo, quantité, confirmation."""
    form = ParticipantForm.query.get_or_404(form_id)

    form.sample_code = request.form.get("sample_code", "").strip() or None
    form.tracking_number = request.form.get("tracking_number", "").strip() or None
    form.carrier = request.form.get("carrier", "").strip() or None
    sample_qty_str = request.form.get("sample_quantity", "")
    form.sample_quantity = int(sample_qty_str) if sample_qty_str.strip().isdigit() else None

    dispatch_str = request.form.get("dispatch_date", "")
    form.dispatch_date = datetime.strptime(dispatch_str, "%Y-%m-%d").date() if dispatch_str else None

    # Confirmation d'envoi
    if request.form.get("dispatch_confirmed") == "1":
        form.dispatch_confirmed = True

    # Photo des échantillons
    photo_file = request.files.get("sample_photo")
    if photo_file and photo_file.filename:
        ext = photo_file.filename.rsplit(".", 1)[-1].lower() if "." in photo_file.filename else ""
        if ext in {"jpg", "jpeg", "png", "gif", "webp"}:
            subdir = os.path.join(current_app.config["UPLOAD_FOLDER"], "parcel_photos", str(form.id))
            os.makedirs(subdir, exist_ok=True)
            stored_name = f"{uuid.uuid4().hex}.{ext}"
            photo_file.save(os.path.join(subdir, stored_name))
            form.sample_photo = os.path.join("parcel_photos", str(form.id), stored_name)

    # Générer le token QR si inexistant
    form.ensure_qr_token()

    db.session.commit()
    from blueprints.parcels import sync_from_form
    sync_from_form(form)

    log_action("update_shipping", entity_type="ParticipantForm", entity_id=form.id,
               details=f"Infos colis mises à jour pour {form.laboratory.code} (Tracking: {form.tracking_number})")

    flash(f"Informations d'expédition mises à jour pour le laboratoire {form.laboratory.code}.", "success")
    return redirect(request.referrer or url_for("participants.tracker"))


@participants_bp.route("/update-mass-shipping", methods=["POST"])
@login_required
@roles_required("admin", "chef_technique")
def update_mass_shipping():
    """Mise à jour en masse des infos d'expédition (transporteur, numéro de suivi)."""
    form_ids = request.form.getlist("form_ids")
    campaign_id = request.form.get("campaign_id")
    count = 0
    touched = []
    for fid_str in form_ids:
        fid = int(fid_str)
        form = ParticipantForm.query.get(fid)
        if form:
            carrier = request.form.get(f"carrier_{fid}", "").strip()
            tracking = request.form.get(f"tracking_number_{fid}", "").strip()
            
            updated = False
            if carrier != (form.carrier or ""):
                form.carrier = carrier or None
                updated = True
            if tracking != (form.tracking_number or ""):
                form.tracking_number = tracking or None
                updated = True
                
            if updated:
                count += 1
                touched.append(form)
                form.ensure_qr_token()

    if count > 0:
        db.session.commit()
        from blueprints.parcels import sync_from_form
        for form in touched:
            sync_from_form(form)
        log_action("update_mass_shipping", entity_type="Campaign", entity_id=campaign_id,
                   details=f"Mise à jour logistique en masse pour {count} formulaires")
        flash(f"{count} expéditions mises à jour.", "success")
    else:
        flash("Aucune modification détectée.", "info")
        
    return redirect(request.referrer or url_for("participants.tracker"))


@participants_bp.route("/form/<int:form_id>/parcel-status")
@login_required
@roles_required("admin", "chef_technique", "auditeur")
def parcel_status(form_id):
    """Aperçu du statut complet du colis pour un formulaire donné."""
    form = ParticipantForm.query.get_or_404(form_id)
    return render_template("participants/parcel_status.html", form=form)


@participants_bp.route("/form/<int:form_id>/qr-label")
@login_required
def qr_label(form_id):
    """Fiche/Étiquette d'expédition imprimable avec QR Code scannable."""
    form = ParticipantForm.query.get_or_404(form_id)
    form.ensure_qr_token()
    db.session.commit()

    scan_url = url_for("participants.scan_qr", qr_token=form.qr_token, _external=True)

    return render_template("participants/qr_label.html", form=form, scan_url=scan_url)


@participants_bp.route("/form/<int:form_id>/photo")
@login_required
def serve_parcel_photo(form_id):
    """Servir la photo du colis d'un formulaire donné."""
    form = ParticipantForm.query.get_or_404(form_id)
    if not form.sample_photo:
        from flask import abort
        abort(404)
    directory = os.path.join(current_app.config["UPLOAD_FOLDER"], os.path.dirname(form.sample_photo))
    filename = os.path.basename(form.sample_photo)
    return send_from_directory(directory, filename)

