from flask import Blueprint, render_template, redirect, url_for, request, flash
from flask_login import login_required, current_user

from extensions import db
from models import User, Laboratory, AuditLog, ROLES
from utils import log_action
from blueprints.auth import roles_required

admin_bp = Blueprint("admin", __name__, template_folder="../templates/admin")


@admin_bp.route("/users")
@login_required
@roles_required("admin")
def list_users():
    users = User.query.order_by(User.username).all()
    return render_template("admin/users.html", users=users)


@admin_bp.route("/users/new", methods=["GET", "POST"])
@login_required
@roles_required("admin")
def new_user():
    labs = Laboratory.query.filter_by(active=True).order_by(Laboratory.code).all()
    if request.method == "POST":
        username = request.form["username"].strip()
        if User.query.filter_by(username=username).first():
            flash("Ce nom d'utilisateur existe déjà.", "danger")
            return render_template("admin/user_form.html", user=None, roles=ROLES, labs=labs)

        user = User(
            username=username,
            email=request.form["email"].strip(),
            full_name=request.form["full_name"].strip(),
            role=request.form["role"],
            laboratory_id=request.form.get("laboratory_id") or None,
            active=True,
        )
        user.set_password(request.form.get("password") or "changeme123")
        db.session.add(user)
        db.session.commit()
        log_action("create", "User", user.id, f"Création utilisateur {user.username} ({user.role})")
        flash("Utilisateur créé avec succès.", "success")
        return redirect(url_for("admin.list_users"))

    return render_template("admin/user_form.html", user=None, roles=ROLES, labs=labs)


@admin_bp.route("/users/<int:user_id>/edit", methods=["GET", "POST"])
@login_required
@roles_required("admin")
def edit_user(user_id):
    user = User.query.get_or_404(user_id)
    labs = Laboratory.query.filter_by(active=True).order_by(Laboratory.code).all()

    if request.method == "POST":
        user.email = request.form["email"].strip()
        user.full_name = request.form["full_name"].strip()
        user.role = request.form["role"]
        user.laboratory_id = request.form.get("laboratory_id") or None
        user.active = "active" in request.form
        new_password = request.form.get("password", "").strip()
        if new_password:
            user.set_password(new_password)
        db.session.commit()
        log_action("update", "User", user.id, f"Modification utilisateur {user.username}")
        flash("Utilisateur mis à jour.", "success")
        return redirect(url_for("admin.list_users"))

    return render_template("admin/user_form.html", user=user, roles=ROLES, labs=labs)


@admin_bp.route("/users/<int:user_id>/delete", methods=["POST"])
@login_required
@roles_required("admin")
def delete_user(user_id):
    if user_id == current_user.id:
        flash("Vous ne pouvez pas supprimer votre propre compte.", "danger")
        return redirect(url_for("admin.list_users"))
    user = User.query.get_or_404(user_id)
    username = user.username
    db.session.delete(user)
    db.session.commit()
    log_action("delete", "User", user_id, f"Suppression utilisateur {username}")
    flash("Utilisateur supprimé.", "info")
    return redirect(url_for("admin.list_users"))


@admin_bp.route("/users/<int:user_id>/reset-password", methods=["POST"])
@login_required
@roles_required("admin")
def reset_password(user_id):
    import random
    import string
    user = User.query.get_or_404(user_id)
    chars = string.ascii_letters + string.digits
    temp_password = "".join(random.choice(chars) for _ in range(10))
    user.set_password(temp_password)
    db.session.commit()
    log_action("update", "User", user_id, f"Réinitialisation mot de passe pour {user.username}")
    flash(f"Mot de passe réinitialisé. Le nouveau mot de passe temporaire pour {user.username} est : {temp_password}", "success")
    return redirect(url_for("admin.list_users"))


@admin_bp.route("/audit-trail")
@login_required
@roles_required("admin", "auditeur")
def audit_trail():
    page = request.args.get("page", 1, type=int)
    action_filter = request.args.get("action", "")
    query = AuditLog.query.order_by(AuditLog.timestamp.desc())
    if action_filter:
        query = query.filter_by(action=action_filter)
    logs = query.limit(300).all()
    actions = [row[0] for row in db.session.query(AuditLog.action).distinct().all()]
    return render_template("admin/audit.html", logs=logs, actions=actions, action_filter=action_filter)
