from datetime import datetime
from functools import wraps

from flask import Blueprint, render_template, redirect, url_for, request, flash
from flask_login import login_user, logout_user, login_required, current_user

from extensions import db
from models import User
from utils import log_action

auth_bp = Blueprint("auth", __name__, template_folder="../templates/auth")


def roles_required(*roles):
    def decorator(f):
        @wraps(f)
        def wrapped(*args, **kwargs):
            if not current_user.is_authenticated:
                return redirect(url_for("auth.login"))
            if current_user.role not in roles:
                flash("Vous n'avez pas les droits pour accéder à cette page.", "danger")
                return redirect(url_for("dashboard.index"))
            return f(*args, **kwargs)
        return wrapped
    return decorator


@auth_bp.route("/login", methods=["GET", "POST"])
def login():
    if current_user.is_authenticated:
        return redirect(url_for("dashboard.index"))

    if request.method == "POST":
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "")
        user = User.query.filter_by(username=username).first()

        if user and user.check_password(password):
            if not user.active:
                flash("Ce compte est désactivé. Contactez un administrateur.", "danger")
                return redirect(url_for("auth.login"))
            login_user(user)
            user.last_login = datetime.utcnow()
            db.session.commit()
            log_action("login", "User", user.id, f"Connexion de {user.username}")
            next_page = request.args.get("next")
            return redirect(next_page or url_for("dashboard.index"))
        else:
            flash("Nom d'utilisateur ou mot de passe incorrect.", "danger")

    return render_template("auth/login.html")


@auth_bp.route("/logout")
@login_required
def logout():
    log_action("logout", "User", current_user.id, f"Déconnexion de {current_user.username}")
    logout_user()
    flash("Vous avez été déconnecté.", "info")
    return redirect(url_for("auth.login"))


@auth_bp.route("/profile", methods=["GET", "POST"])
@login_required
def profile():
    if request.method == "POST":
        current_user.full_name = request.form.get("full_name", current_user.full_name)
        current_user.email = request.form.get("email", current_user.email)
        db.session.commit()
        log_action("update_profile", "User", current_user.id)
        flash("Profil mis à jour.", "success")
        return redirect(url_for("auth.profile"))
    return render_template("auth/profile.html")


@auth_bp.route("/change-password", methods=["GET", "POST"])
@login_required
def change_password():
    if request.method == "POST":
        current_pwd = request.form.get("current_password", "")
        new_pwd = request.form.get("new_password", "")
        confirm_pwd = request.form.get("confirm_password", "")

        if not current_user.check_password(current_pwd):
            flash("Mot de passe actuel incorrect.", "danger")
        elif new_pwd != confirm_pwd:
            flash("Les nouveaux mots de passe ne correspondent pas.", "danger")
        elif len(new_pwd) < 6:
            flash("Le nouveau mot de passe doit contenir au moins 6 caractères.", "danger")
        else:
            current_user.set_password(new_pwd)
            db.session.commit()
            log_action("change_password", "User", current_user.id)
            flash("Mot de passe modifié avec succès.", "success")
            return redirect(url_for("auth.profile"))

    return render_template("auth/change_password.html")
