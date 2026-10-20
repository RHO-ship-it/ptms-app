import os
from flask import Flask, render_template
from flask_login import current_user

from config import Config
from extensions import db, login_manager


def create_app(config_class=Config):
    app = Flask(__name__)
    app.config.from_object(config_class)

    os.makedirs(app.config["UPLOAD_FOLDER"], exist_ok=True)
    os.makedirs(app.config["EXPORT_FOLDER"], exist_ok=True)

    db.init_app(app)
    login_manager.init_app(app)

    import models  # noqa: F401  (enregistre toutes les tables avant create_all)

    with app.app_context():
        db.create_all()
        # Base déjà existante : ajoute les colonnes récentes si elles manquent.
        from sqlalchemy import inspect as _sa_inspect, text as _sa_text
        _insp = _sa_inspect(db.engine)
        for _table, _col, _ddl in (("portal_documents", "year", "INTEGER"),
                                   ("campaigns", "sheet_config", "TEXT"),
                                   ("trials", "sheet_key", "VARCHAR(40)"),
                                   ("trials", "scale", "TEXT"),
                                   ("submissions", "extra_data", "TEXT")):
            if _col not in {c["name"] for c in _insp.get_columns(_table)}:
                db.session.execute(_sa_text(f"ALTER TABLE {_table} ADD COLUMN {_col} {_ddl}"))
        db.session.commit()

    from models import User

    @login_manager.user_loader
    def load_user(user_id):
        return User.query.get(int(user_id))

    # Blueprints
    from blueprints.auth import auth_bp
    from blueprints.dashboard import dashboard_bp
    from blueprints.campaigns import campaigns_bp
    from blueprints.wizard import wizard_bp
    from blueprints.laboratories import laboratories_bp
    from blueprints.trials import trials_bp
    from blueprints.participants import participants_bp
    from blueprints.statistics import statistics_bp
    from blueprints.reports import reports_bp
    from blueprints.admin import admin_bp
    from blueprints.traitement import traitement_bp
    from blueprints.homogeneity import homogeneity_bp
    from blueprints.commercial import commercial_bp
    from blueprints.catalogue import catalogue_bp

    app.register_blueprint(auth_bp, url_prefix="/auth")
    app.register_blueprint(dashboard_bp, url_prefix="/")
    app.register_blueprint(campaigns_bp, url_prefix="/campaigns")
    app.register_blueprint(wizard_bp, url_prefix="/wizard")
    app.register_blueprint(laboratories_bp, url_prefix="/laboratories")
    app.register_blueprint(trials_bp, url_prefix="/trials")
    app.register_blueprint(participants_bp, url_prefix="/participants")
    app.register_blueprint(statistics_bp, url_prefix="/statistics")
    app.register_blueprint(reports_bp, url_prefix="/reports")
    app.register_blueprint(admin_bp, url_prefix="/admin")
    app.register_blueprint(traitement_bp, url_prefix="/traitement")
    app.register_blueprint(homogeneity_bp, url_prefix="/homogeneity")
    app.register_blueprint(commercial_bp, url_prefix="/commercial")
    app.register_blueprint(catalogue_bp, url_prefix="/catalogue")
    from blueprints.sheets import sheets_bp
    app.register_blueprint(sheets_bp, url_prefix="/sheets")
    from blueprints.parcels import parcels_bp
    app.register_blueprint(parcels_bp, url_prefix="/parcels")

    # Recharge les PDF déposés dans documents/ (la base SQLite de Render est remise à zéro à chaque déploiement).
    from blueprints.catalogue import seed_documents
    with app.app_context():
        try:
            seed_documents()
        except Exception as exc:  # ne doit jamais empêcher le démarrage
            db.session.rollback()
            app.logger.warning("Chargement des documents ignoré : %s", exc)

    @app.cli.command("send-reminders")
    def send_reminders_cli():
        """Commande CLI pour envoyer les relances automatiques de deadline par e-mail."""
        from utils import send_deadline_reminders
        res = send_deadline_reminders(days_threshold=5)
        print(f"Rappels envoyés: {res['reminders_sent']} notifications à {res['labs_count']} laboratoire(s).")


    @app.context_processor
    def inject_globals():
        from models import Notification
        unread_count = 0
        if current_user.is_authenticated:
            unread_count = Notification.query.filter_by(user_id=current_user.id, read=False).count()
        return {"unread_notifications": unread_count}

    @app.errorhandler(404)
    def not_found(e):
        return render_template("errors/404.html"), 404

    @app.errorhandler(500)
    def server_error(e):
        return render_template("errors/500.html"), 500

    return app


if __name__ == "__main__":
    app = create_app()
    app.run(debug=True, host="0.0.0.0", port=5000)
