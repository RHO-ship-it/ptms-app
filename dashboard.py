from flask import Blueprint, render_template
from flask_login import login_required, current_user

from models import Campaign, Laboratory, ParticipantForm, Submission, User, Notification
from datetime import date

dashboard_bp = Blueprint("dashboard", __name__, template_folder="../templates/dashboard")


@dashboard_bp.route("/")
@login_required
def index():
    if current_user.is_participant():
        return _participant_dashboard()
    return _staff_dashboard()


def _staff_dashboard():
    kpis = {
        "nb_campaigns_active": Campaign.query.filter_by(status="active").count(),
        "nb_campaigns_total": Campaign.query.count(),
        "nb_labs": Laboratory.query.filter_by(active=True).count(),
        "nb_forms_pending": ParticipantForm.query.filter_by(status="pending").count(),
        "nb_forms_submitted": ParticipantForm.query.filter_by(status="submitted").count(),
        "nb_anomalies": ParticipantForm.query.filter_by(status="anomaly").count(),
        "nb_validated": ParticipantForm.query.filter_by(status="validated").count(),
        "nb_users": User.query.filter_by(active=True).count(),
    }
    recent_campaigns = Campaign.query.order_by(Campaign.created_at.desc()).limit(5).all()
    return render_template("dashboard/staff.html", kpis=kpis, recent_campaigns=recent_campaigns)


def _participant_dashboard():
    lab_id = current_user.laboratory_id
    forms = ParticipantForm.query.filter_by(laboratory_id=lab_id).all() if lab_id else []
    today = date.today()

    kpis = {
        "nb_pending": sum(1 for f in forms if f.status == "pending"),
        "nb_submitted": sum(1 for f in forms if f.status == "submitted"),
        "nb_validated": sum(1 for f in forms if f.status == "validated"),
        "nb_anomaly": sum(1 for f in forms if f.status == "anomaly"),
        "nb_late": sum(1 for f in forms if f.status == "pending" and f.trial.due_date and f.trial.due_date < today),
    }
    notifications = (Notification.query
                      .filter_by(user_id=current_user.id)
                      .order_by(Notification.created_at.desc())
                      .limit(10).all())
    return render_template("dashboard/participant.html", kpis=kpis, notifications=notifications)
