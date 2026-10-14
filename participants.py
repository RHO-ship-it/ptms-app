import io
import json
import os
import uuid
from datetime import date, datetime

from flask import (Blueprint, render_template, redirect, url_for, request, flash,
                    send_file, current_app, send_from_directory)
from flask_login import login_required, current_user
from werkzeug.utils import secure_filename

from extensions import db
from models import ParticipantForm, Submission, Trial, Campaign, Laboratory
from utils import (log_action, notify_role, compute_z_score, z_score_interpretation,
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

    forms = (ParticipantForm.query
             .filter_by(laboratory_id=current_user.laboratory_id)
             .join(Trial)
             .join(Campaign)
             .filter(Campaign.status == "active")
             .all())
    today = date.today()
    return render_template("participants/portal.html", forms=forms, today=today)


@participants_bp.route("/form/<int:form_id>/submit", methods=["GET", "POST"])
@login_required
@roles_required("participant")
def submit_form(form_id):
    form = ParticipantForm.query.get_or_404(form_id)
    if form.laboratory_id != current_user.laboratory_id:
        flash("Accès non autorisé à ce formulaire.", "danger")
        return redirect(url_for("participants.portal"))

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
            z, anomaly = compute_z_score(value, form.trial.target_value, form.trial.target_sd)
            uncertainty_str = request.form.get("uncertainty", "")
            lab_uncertainty = float(uncertainty_str) if uncertainty_str else None
            en, _en_conform = compute_en(value, lab_uncertainty, form.trial.target_value, form.trial.reference_uncertainty)
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
    status_filter = request.args.get("status", "")
    query = (ParticipantForm.query
             .join(Submission)
             .filter(ParticipantForm.status.in_(["submitted", "anomaly", "validated"])))
    if status_filter:
        query = query.filter(ParticipantForm.status == status_filter)

    forms = query.all()

    # Moyenne des résultats quantitatifs déjà soumis, par essai (vue de synthèse pour l'admin)
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

    return render_template("participants/validation.html", items=items, status_filter=status_filter)


@participants_bp.route("/validation/<int:form_id>/validate", methods=["POST"])
@login_required
@roles_required("admin", "chef_technique")
def validate_submission(form_id):
    form = ParticipantForm.query.get_or_404(form_id)
    if not form.submission:
        flash("Aucune soumission à valider pour ce formulaire.", "warning")
        return redirect(url_for("participants.validation_center"))

    form.submission.validated = True
    form.submission.validated_by = current_user.id
    form.submission.validated_at = datetime.utcnow()
    form.status = "validated"
    db.session.commit()

    log_action("validate", "Submission", form.submission.id,
               f"Validation de la soumission pour {form.trial.name} / {form.laboratory.code}")
    flash("Soumission validée.", "success")
    return redirect(url_for("participants.validation_center"))


@participants_bp.route("/validation/<int:form_id>/reject", methods=["POST"])
@login_required
@roles_required("admin", "chef_technique")
def reject_submission(form_id):
    form = ParticipantForm.query.get_or_404(form_id)
    reason = request.form.get("reason", "").strip()
    form.status = "anomaly"
    if form.submission:
        form.submission.validated = False
    db.session.commit()

    log_action("reject", "Submission", form.id,
               f"Rejet de la soumission pour {form.trial.name} / {form.laboratory.code} : {reason}")
    notify_role("participant", f"Votre soumission pour {form.trial.name} nécessite une correction.")
    flash("Soumission rejetée, le laboratoire a été notifié.", "info")
    return redirect(url_for("participants.validation_center"))
