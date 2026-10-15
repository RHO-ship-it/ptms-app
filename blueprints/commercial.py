from datetime import datetime
from flask import Blueprint, render_template, redirect, url_for, request, flash, jsonify
from flask_login import login_required, current_user

from extensions import db
from models import Laboratory, CommercialTracking, ParticipantForm, Trial, Campaign, Submission, COMMERCIAL_STATUSES, COMMERCIAL_STATUS_LABELS
from utils import log_action, notify, send_deadline_reminders
from blueprints.auth import roles_required

commercial_bp = Blueprint("commercial", __name__, template_folder="../templates/commercial")


@commercial_bp.route("/")
@login_required
@roles_required("admin", "chef_technique", "auditeur")
def index():
    status_filter = request.args.get("status", "").strip()
    search_query = request.args.get("q", "").strip()

    query = Laboratory.query.filter_by(active=True)

    if status_filter in COMMERCIAL_STATUSES:
        query = query.filter_by(commercial_status=status_filter)

    if search_query:
        query = query.filter(
            (Laboratory.name.ilike(f"%{search_query}%")) |
            (Laboratory.code.ilike(f"%{search_query}%")) |
            (Laboratory.contact_name.ilike(f"%{search_query}%")) |
            (Laboratory.email.ilike(f"%{search_query}%"))
        )

    labs = query.order_by(Laboratory.code.asc()).all()

    # Statistiques KPI Commerciales
    all_active_labs = Laboratory.query.filter_by(active=True).all()
    count_prospect = sum(1 for l in all_active_labs if l.commercial_status == "prospect")
    count_confirme = sum(1 for l in all_active_labs if l.commercial_status == "confirme")
    count_retenu   = sum(1 for l in all_active_labs if l.commercial_status == "retenu")
    count_fidele   = sum(1 for l in all_active_labs if l.commercial_status == "fidele")

    # Suivi des deadlines proches
    pending_forms = ParticipantForm.query.filter(
        ParticipantForm.status.in_(["pending", "draft"])
    ).all()

    upcoming_deadlines = []
    today = datetime.utcnow().date()
    for pf in pending_forms:
        if pf.trial and pf.trial.due_date:
            days_left = (pf.trial.due_date - today).days
            upcoming_deadlines.append({
                "form": pf,
                "trial": pf.trial,
                "campaign": pf.trial.campaign,
                "lab": pf.laboratory,
                "due_date": pf.trial.due_date,
                "days_left": days_left,
            })

    upcoming_deadlines.sort(key=lambda x: x["days_left"])

    # Derniers événements de tracking
    recent_trackings = CommercialTracking.query.order_by(CommercialTracking.created_at.desc()).limit(15).all()

    return render_template(
        "commercial/index.html",
        labs=labs,
        status_filter=status_filter,
        search_query=search_query,
        count_prospect=count_prospect,
        count_confirme=count_confirme,
        count_retenu=count_retenu,
        count_fidele=count_fidele,
        total_labs=len(all_active_labs),
        upcoming_deadlines=upcoming_deadlines[:10],
        recent_trackings=recent_trackings,
        COMMERCIAL_STATUS_LABELS=COMMERCIAL_STATUS_LABELS,
    )


@commercial_bp.route("/campaigns")
@login_required
@roles_required("admin", "chef_technique", "auditeur")
def campaign_analytics():
    campaigns = Campaign.query.order_by(Campaign.created_at.desc()).all()
    selected_id = request.args.get("campaign_id", type=int)

    selected_campaign = None
    if selected_id:
        selected_campaign = Campaign.query.get(selected_id)

    if not selected_campaign and campaigns:
        selected_campaign = campaigns[0]

    # Analyse spécifique pour la campagne sélectionnée
    campaign_labs = []
    fidele_labs = []
    iso17025_labs = []
    trial_stats = []
    fidele_count = 0
    fidele_pct = 0.0
    iso17025_count = 0
    iso17025_pct = 0.0

    if selected_campaign:
        campaign_labs = [cl.laboratory for cl in selected_campaign.laboratories if cl.laboratory and cl.laboratory.active]
        total_campaign_labs = len(campaign_labs)

        fidele_labs = [l for l in campaign_labs if l.commercial_status == "fidele"]
        fidele_count = len(fidele_labs)
        fidele_pct = round((fidele_count / total_campaign_labs * 100), 1) if total_campaign_labs > 0 else 0.0

        iso17025_labs = [l for l in campaign_labs if l.iso17025_accredited]
        iso17025_count = len(iso17025_labs)
        iso17025_pct = round((iso17025_count / total_campaign_labs * 100), 1) if total_campaign_labs > 0 else 0.0

        # Détail et pourcentage par essai (paramètre)
        for trial in selected_campaign.trials:
            forms = ParticipantForm.query.filter_by(trial_id=trial.id).all()
            total_forms = len(forms)

            # Nombre de soumissions / laboratoires accrédités ISO 17025 pour cet essai
            iso_accredited_count = 0
            for f in forms:
                is_acc = False
                if f.submission and f.submission.iso17025_accredited:
                    is_acc = True
                elif f.laboratory and f.laboratory.iso17025_accredited:
                    is_acc = True

                if is_acc:
                    iso_accredited_count += 1

            pct_acc = round((iso_accredited_count / total_forms * 100), 1) if total_forms > 0 else 0.0

            trial_stats.append({
                "trial": trial,
                "total_forms": total_forms,
                "iso_count": iso_accredited_count,
                "non_iso_count": total_forms - iso_accredited_count,
                "iso_pct": pct_acc,
            })

    return render_template(
        "commercial/campaign_analytics.html",
        campaigns=campaigns,
        selected_campaign=selected_campaign,
        campaign_labs=campaign_labs,
        fidele_labs=fidele_labs,
        fidele_count=fidele_count,
        fidele_pct=fidele_pct,
        iso17025_count=iso17025_count,
        iso17025_pct=iso17025_pct,
        trial_stats=trial_stats,
    )


@commercial_bp.route("/laboratory/<int:lab_id>/toggle-iso17025", methods=["POST"])
@login_required
@roles_required("admin", "chef_technique")
def toggle_iso17025(lab_id):
    lab = Laboratory.query.get_or_404(lab_id)
    lab.iso17025_accredited = not lab.iso17025_accredited
    db.session.commit()

    status_str = "Accrédité ISO 17025" if lab.iso17025_accredited else "Non accrédité ISO 17025"
    log_action("toggle_iso17025", entity_type="Laboratory", entity_id=lab.id, details=f"Statut ISO 17025 du laboratoire {lab.code}: {status_str}")

    flash(f"Statut d'accréditation ISO 17025 pour {lab.code} mis à jour : {status_str}.", "success")
    return redirect(request.referrer or url_for("commercial.index"))


@commercial_bp.route("/laboratory/<int:lab_id>/update-status", methods=["POST"])
@login_required
@roles_required("admin", "chef_technique")
def update_status(lab_id):
    lab = Laboratory.query.get_or_404(lab_id)
    new_status = request.form.get("commercial_status")

    if new_status not in COMMERCIAL_STATUSES:
        flash("Statut commercial non valide.", "danger")
        return redirect(url_for("commercial.index"))

    old_status_label = lab.get_commercial_status_label()
    lab.commercial_status = new_status
    new_status_label = lab.get_commercial_status_label()

    # Enregistrer automatiquement l'interaction de changement de statut
    tracking = CommercialTracking(
        laboratory_id=lab.id,
        user_id=current_user.id,
        interaction_type="status_change",
        subject=f"Changement de statut commercial : {old_status_label} ➔ {new_status_label}",
        notes=request.form.get("notes", "").strip() or f"Le statut commercial a été mis à jour par {current_user.full_name}.",
    )
    db.session.add(tracking)
    db.session.commit()

    log_action(
        "update_commercial_status",
        entity_type="Laboratory",
        entity_id=lab.id,
        details=f"Passage du laboratoire {lab.code} au statut '{new_status_label}'",
    )

    flash(f"Statut commercial du laboratoire {lab.code} mis à jour : {new_status_label}.", "success")
    return redirect(request.referrer or url_for("commercial.index"))


@commercial_bp.route("/laboratory/<int:lab_id>/add-tracking", methods=["POST"])
@login_required
@roles_required("admin", "chef_technique")
def add_tracking(lab_id):
    lab = Laboratory.query.get_or_404(lab_id)

    interaction_type = request.form.get("interaction_type", "note")
    subject = request.form.get("subject", "").strip()
    notes = request.form.get("notes", "").strip()
    next_followup = request.form.get("next_followup_date", "")

    if not subject:
        flash("Veuillez indiquer un objet pour l'interaction commerciale.", "warning")
        return redirect(url_for("commercial.index"))

    followup_date = datetime.strptime(next_followup, "%Y-%m-%d").date() if next_followup else None

    tracking = CommercialTracking(
        laboratory_id=lab.id,
        user_id=current_user.id,
        interaction_type=interaction_type,
        subject=subject,
        notes=notes,
        next_followup_date=followup_date,
    )
    db.session.add(tracking)
    db.session.commit()

    log_action(
        "add_commercial_tracking",
        entity_type="CommercialTracking",
        entity_id=tracking.id,
        details=f"Échange enregistré pour {lab.code} ({interaction_type}): {subject}",
    )

    flash(f"Échange commercial enregistré pour le laboratoire {lab.code}.", "success")
    return redirect(request.referrer or url_for("commercial.index"))


@commercial_bp.route("/laboratory/<int:lab_id>/history")
@login_required
@roles_required("admin", "chef_technique", "auditeur")
def tracking_history(lab_id):
    lab = Laboratory.query.get_or_404(lab_id)
    trackings = CommercialTracking.query.filter_by(laboratory_id=lab.id).order_by(CommercialTracking.created_at.desc()).all()

    return jsonify({
        "lab_code": lab.code,
        "lab_name": lab.name,
        "commercial_status": lab.get_commercial_status_label(),
        "entries": [{
            "id": t.id,
            "type": t.interaction_type,
            "subject": t.subject,
            "notes": t.notes or "",
            "user": t.user.full_name if t.user else "Système",
            "created_at": t.created_at.strftime("%d/%m/%Y à %H:%M"),
            "next_followup": t.next_followup_date.strftime("%d/%m/%Y") if t.next_followup_date else None,
        } for t in trackings]
    })


@commercial_bp.route("/reminders/send-deadlines", methods=["POST"])
@login_required
@roles_required("admin", "chef_technique")
def send_reminders():
    days_threshold = int(request.form.get("days_threshold", 5))
    res = send_deadline_reminders(days_threshold=days_threshold)

    if res["reminders_sent"] > 0:
        flash(
            f"✅ {res['reminders_sent']} notifications/e-mails de rappel envoyés à {res['labs_count']} laboratoire(s) pour les échéances à venir.",
            "success",
        )
    else:
        flash("Aucune échéance urgente ne nécessite de relance pour le moment.", "info")

    return redirect(request.referrer or url_for("commercial.index"))

