from datetime import datetime
from flask_login import UserMixin
from werkzeug.security import generate_password_hash, check_password_hash
from extensions import db

ROLES = ["admin", "chef_technique", "participant", "auditeur"]

# Types d'essai (cahier des charges §4.1 / §8 — distinction qualitatif / quantitatif)
TRIAL_TYPES = ["quantitative", "qualitative", "solidite"]
TRIAL_TYPE_LABELS = {
    "quantitative": "Quantitatif",
    "qualitative": "Qualitatif",
    "solidite": "Solidité",
}

SIGMA_PT_METHODS = ["fixe", "horwitz", "robuste"]
SIGMA_PT_METHOD_LABELS = {
    "fixe": "Valeur fixe (expérience / fitness for purpose)",
    "horwitz": "Équation de Horwitz",
    "robuste": "Écart-type robuste des résultats (Algorithme A / MADe)",
}


class User(UserMixin, db.Model):
    __tablename__ = "users"

    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(80), unique=True, nullable=False)
    email = db.Column(db.String(150), unique=True, nullable=False)
    password_hash = db.Column(db.String(255), nullable=False)
    full_name = db.Column(db.String(150), nullable=False)
    role = db.Column(db.String(30), nullable=False, default="participant")
    laboratory_id = db.Column(db.Integer, db.ForeignKey("laboratories.id"), nullable=True)
    active = db.Column(db.Boolean, default=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    last_login = db.Column(db.DateTime, nullable=True)

    laboratory = db.relationship("Laboratory", back_populates="users")

    def set_password(self, password):
        self.password_hash = generate_password_hash(password)

    def check_password(self, password):
        return check_password_hash(self.password_hash, password)

    def is_admin(self):
        return self.role == "admin"

    def is_chef_technique(self):
        return self.role == "chef_technique"

    def is_participant(self):
        return self.role == "participant"

    def is_auditeur(self):
        return self.role == "auditeur"

    def is_staff(self):
        return self.role in ("admin", "chef_technique", "auditeur")

    def __repr__(self):
        return f"<User {self.username} ({self.role})>"


COMMERCIAL_STATUSES = ["prospect", "confirme", "retenu", "fidele"]
COMMERCIAL_STATUS_LABELS = {
    "prospect": "Client prospecté",
    "confirme": "Client confirmé",
    "retenu": "Client retenu",
    "fidele": "Client fidèle",
}


class Laboratory(db.Model):
    __tablename__ = "laboratories"

    id = db.Column(db.Integer, primary_key=True)
    code = db.Column(db.String(30), unique=True, nullable=False)
    name = db.Column(db.String(200), nullable=False)
    contact_name = db.Column(db.String(150))
    email = db.Column(db.String(150))
    phone = db.Column(db.String(50))
    address = db.Column(db.String(300))
    commercial_status = db.Column(db.String(30), default="prospect", nullable=False)  # prospect, confirme, retenu, fidele
    iso17025_accredited = db.Column(db.Boolean, default=False, nullable=False)
    active = db.Column(db.Boolean, default=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    users = db.relationship("User", back_populates="laboratory")
    campaign_links = db.relationship("CampaignLaboratory", back_populates="laboratory", cascade="all, delete-orphan")
    tracking_entries = db.relationship("CommercialTracking", back_populates="laboratory", cascade="all, delete-orphan", order_by="CommercialTracking.created_at.desc()")

    def get_commercial_status_label(self):
        return COMMERCIAL_STATUS_LABELS.get(self.commercial_status, self.commercial_status)

    def __repr__(self):
        return f"<Laboratory {self.code}>"


class CommercialTracking(db.Model):
    __tablename__ = "commercial_tracking"

    id = db.Column(db.Integer, primary_key=True)
    laboratory_id = db.Column(db.Integer, db.ForeignKey("laboratories.id"), nullable=False)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
    interaction_type = db.Column(db.String(30), nullable=False, default="note")
    # Types: prospect, call, email, meeting, quotation, status_change, note
    subject = db.Column(db.String(200), nullable=False)
    notes = db.Column(db.Text, nullable=True)
    next_followup_date = db.Column(db.Date, nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    laboratory = db.relationship("Laboratory", back_populates="tracking_entries")
    user = db.relationship("User")

    def __repr__(self):
        return f"<CommercialTracking {self.id} - Lab:{self.laboratory_id}>"



class Campaign(db.Model):
    __tablename__ = "campaigns"

    id = db.Column(db.Integer, primary_key=True)
    reference = db.Column(db.String(50), unique=True, nullable=False)
    name = db.Column(db.String(200), nullable=False)
    description = db.Column(db.Text)
    status = db.Column(db.String(30), default="draft")  # draft, active, closed, archived
    start_date = db.Column(db.Date)
    end_date = db.Column(db.Date)
    report_published = db.Column(db.Boolean, default=False)
    sheet_config = db.Column(db.Text, nullable=True)  # JSON : structure de la feuille de saisie (voir blueprints/sheets.py)
    created_by = db.Column(db.Integer, db.ForeignKey("users.id"))
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    trials = db.relationship("Trial", back_populates="campaign", cascade="all, delete-orphan")
    laboratories = db.relationship("CampaignLaboratory", back_populates="campaign", cascade="all, delete-orphan")

    def __repr__(self):
        return f"<Campaign {self.reference}>"


class CampaignLaboratory(db.Model):
    __tablename__ = "campaign_laboratories"

    id = db.Column(db.Integer, primary_key=True)
    campaign_id = db.Column(db.Integer, db.ForeignKey("campaigns.id"), nullable=False)
    laboratory_id = db.Column(db.Integer, db.ForeignKey("laboratories.id"), nullable=False)

    campaign = db.relationship("Campaign", back_populates="laboratories")
    laboratory = db.relationship("Laboratory", back_populates="campaign_links")


class Trial(db.Model):
    __tablename__ = "trials"

    id = db.Column(db.Integer, primary_key=True)
    campaign_id = db.Column(db.Integer, db.ForeignKey("campaigns.id"), nullable=False)
    name = db.Column(db.String(200), nullable=False)
    trial_type = db.Column(db.String(20), nullable=False, default="quantitative")  # quantitative / qualitative / solidite
    parameter = db.Column(db.String(150))
    unit = db.Column(db.String(30))
    method = db.Column(db.String(150))
    sample_reference = db.Column(db.String(150))  # référence de l'échantillon testé (FTE 004/008)
    target_value = db.Column(db.Float, nullable=True)   # valeur assignée (X) — essais quantitatifs
    target_sd = db.Column(db.Float, nullable=True)       # écart-type pour évaluation (sigma) — essais quantitatifs
    expected_qualitative_result = db.Column(db.String(150), nullable=True)  # résultat attendu — essais qualitatifs/solidité
    due_date = db.Column(db.Date, nullable=True)
    sheet_key = db.Column(db.String(40), nullable=True)   # feuille de saisie (onglet) à laquelle appartient cette ligne
    scale = db.Column(db.Text, nullable=True)             # liste de choix propre à cette ligne (valeurs séparées par des virgules)

    # --- Traçabilité de sigma_pt (ISO 13528 §6-7 / cahier des charges §8) ---
    sigma_pt_method = db.Column(db.String(20), nullable=False, default="fixe")
    # 'fixe' (saisie directe/expérience), 'horwitz' (équation de Horwitz), 'robuste' (Algorithme A / MADe des résultats)
    sigma_pt_traceability = db.Column(db.Text, nullable=True)  # justification/texte de traçabilité, auto-rempli si Horwitz
    min_participants = db.Column(db.Integer, nullable=True)  # seuil minimal de participants (ISO 17043 §7.4)

    # --- Incertitude de la valeur assignée, utilisée pour le calcul du En (ISO 13528 §9.3 / ISO 17043) ---
    reference_uncertainty = db.Column(db.Float, nullable=True)  # U(X_ref), incertitude élargie de la valeur assignée

    def type_label(self):
        return TRIAL_TYPE_LABELS.get(self.trial_type, self.trial_type)

    def sigma_pt_method_label(self):
        return SIGMA_PT_METHOD_LABELS.get(self.sigma_pt_method, self.sigma_pt_method)

    campaign = db.relationship("Campaign", back_populates="trials")
    forms = db.relationship("ParticipantForm", back_populates="trial", cascade="all, delete-orphan")
    homogeneity_assessment = db.relationship(
        "HomogeneityStabilityAssessment", back_populates="trial",
        uselist=False, cascade="all, delete-orphan",
    )

    def __repr__(self):
        return f"<Trial {self.name}>"


class HomogeneityStabilityAssessment(db.Model):
    """Évaluation d'homogénéité et de stabilité de l'échantillon d'essai (ISO 13528 Annexe B).

    Une évaluation par essai : g échantillons x 2 réplicats pour l'homogénéité,
    et une comparaison valeur de début / fin de tour pour la stabilité.
    """
    __tablename__ = "homogeneity_stability_assessments"

    id = db.Column(db.Integer, primary_key=True)
    trial_id = db.Column(db.Integer, db.ForeignKey("trials.id"), nullable=False, unique=True)

    # Homogénéité : liste JSON de paires [x1, x2] (un couple de réplicats par échantillon prélevé)
    sample_data = db.Column(db.Text, nullable=True)
    s_within = db.Column(db.Float, nullable=True)   # s_w — écart-type intra-échantillon (répétabilité analytique)
    s_between = db.Column(db.Float, nullable=True)  # s_s — écart-type inter-échantillon (dispersion due à l'échantillon)
    homogeneity_criterion = db.Column(db.Float, nullable=True)  # 0.3 * sigma_pt au moment de l'évaluation
    homogeneity_conform = db.Column(db.Boolean, nullable=True)

    # Stabilité : moyenne des résultats de référence en début et fin de tour d'essai
    stability_first_value = db.Column(db.Float, nullable=True)
    stability_last_value = db.Column(db.Float, nullable=True)
    stability_criterion = db.Column(db.Float, nullable=True)  # 0.3 * sigma_pt
    stability_conform = db.Column(db.Boolean, nullable=True)

    sigma_pt_used = db.Column(db.Float, nullable=True)  # sigma_pt utilisé pour les deux critères, conservé pour traçabilité
    notes = db.Column(db.Text, nullable=True)
    assessed_by = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=True)
    assessed_at = db.Column(db.DateTime, nullable=True)

    trial = db.relationship("Trial", back_populates="homogeneity_assessment")
    assessor = db.relationship("User")


class ParticipantForm(db.Model):
    """Formulaire à remplir par un laboratoire participant pour un essai donné."""
    __tablename__ = "participant_forms"

    id = db.Column(db.Integer, primary_key=True)
    trial_id = db.Column(db.Integer, db.ForeignKey("trials.id"), nullable=False)
    laboratory_id = db.Column(db.Integer, db.ForeignKey("laboratories.id"), nullable=False)
    status = db.Column(db.String(30), default="pending")  # pending, submitted, validated, anomaly
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    # --- Logistics, Parcel Tracking & QR Code Receipt (ISO 17043 §7.3) ---
    sample_code = db.Column(db.String(100), nullable=True)
    tracking_number = db.Column(db.String(100), nullable=True)
    carrier = db.Column(db.String(50), nullable=True)
    dispatch_date = db.Column(db.Date, nullable=True)
    dispatch_confirmed = db.Column(db.Boolean, default=False, nullable=False)  # Confirmation manuelle d'envoi
    sample_quantity = db.Column(db.Integer, nullable=True)      # Nombre d'échantillons envoyés
    sample_photo = db.Column(db.String(255), nullable=True)     # Chemin photo des échantillons
    received = db.Column(db.Boolean, default=False, nullable=False)
    reception_date = db.Column(db.DateTime, nullable=True)
    qr_token = db.Column(db.String(64), unique=True, nullable=True)

    trial = db.relationship("Trial", back_populates="forms")
    laboratory = db.relationship("Laboratory")
    submission = db.relationship("Submission", back_populates="form", uselist=False, cascade="all, delete-orphan")

    def ensure_qr_token(self):
        import secrets
        if not self.qr_token:
            self.qr_token = secrets.token_hex(20)
        return self.qr_token



class Submission(db.Model):
    __tablename__ = "submissions"

    id = db.Column(db.Integer, primary_key=True)
    form_id = db.Column(db.Integer, db.ForeignKey("participant_forms.id"), nullable=False)
    submitted_by = db.Column(db.Integer, db.ForeignKey("users.id"))

    # Résultat — quantitatif (value / replicate_2) OU qualitatif-solidité (qualitative_result)
    value = db.Column(db.Float, nullable=True)                 # 1er résultat / réplicat 1 — essais quantitatifs
    replicate_2 = db.Column(db.Float, nullable=True)           # réplicat 2 (répétabilité) — essais quantitatifs
    qualitative_result = db.Column(db.String(150), nullable=True)  # résultat/grade — essais qualitatifs & solidité
    extra_data = db.Column(db.Text, nullable=True)     # JSON : cellules brutes de la feuille de saisie
    solidite_data = db.Column(db.Text, nullable=True)  # JSON des 9 paramètres de solidité des couleurs (voir utils.SOLIDITE_PARAMS)
    uncertainty = db.Column(db.Float, nullable=True)           # incertitude élargie U(x) déclarée par le labo

    method_used = db.Column(db.String(150))
    instrument = db.Column(db.String(150))                     # instrument utilisé (FTE_004)
    iso17025_accredited = db.Column(db.Boolean, default=False) # accréditation ISO 17025 pour ce paramètre
    sample_reception_date = db.Column(db.Date, nullable=True)
    test_date = db.Column(db.Date, nullable=True)
    temperature = db.Column(db.Float, nullable=True)           # °C — conditions d'essai (FTE_008)
    humidity = db.Column(db.Float, nullable=True)              # % HR — conditions d'essai (FTE_008)

    comments = db.Column(db.Text)
    attachment_path = db.Column(db.String(400), nullable=True)      # chemin relatif dans UPLOAD_FOLDER
    attachment_filename = db.Column(db.String(255), nullable=True)  # nom original du fichier (affichage)
    submitted_at = db.Column(db.DateTime, default=datetime.utcnow)

    validated = db.Column(db.Boolean, default=False)
    validated_by = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=True)
    validated_at = db.Column(db.DateTime, nullable=True)

    confirmed = db.Column(db.Boolean, default=False)   # confirmation finale par le participant
    confirmed_at = db.Column(db.DateTime, nullable=True)

    z_score = db.Column(db.Float, nullable=True)                # essais quantitatifs
    en_score = db.Column(db.Float, nullable=True)               # En (ISO 13528 §9.3), si incertitudes déclarées
    anomaly_flag = db.Column(db.Boolean, default=False)
    grubbs_outlier = db.Column(db.Boolean, default=False)        # signalé par le test de Grubbs (quantitatif)
    qualitative_conform = db.Column(db.Boolean, nullable=True)   # conforme au consensus — essais qualitatifs/solidité

    def display_result(self):
        """Résultat lisible, quel que soit le type d'essai."""
        if self.qualitative_result:
            return self.qualitative_result
        return self.value

    form = db.relationship("ParticipantForm", back_populates="submission")
    submitter = db.relationship("User", foreign_keys=[submitted_by])
    validator = db.relationship("User", foreign_keys=[validated_by])


class AuditLog(db.Model):
    __tablename__ = "audit_logs"

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=True)
    action = db.Column(db.String(100), nullable=False)
    entity_type = db.Column(db.String(80))
    entity_id = db.Column(db.Integer, nullable=True)
    details = db.Column(db.Text)
    timestamp = db.Column(db.DateTime, default=datetime.utcnow)

    user = db.relationship("User")


class Notification(db.Model):
    __tablename__ = "notifications"

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
    message = db.Column(db.String(300), nullable=False)
    link = db.Column(db.String(300), nullable=True)
    read = db.Column(db.Boolean, default=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    user = db.relationship("User")


class EnrollmentRequest(db.Model):
    """Demande de réservation d'un laboratoire pour une campagne du catalogue."""
    __tablename__ = "enrollment_requests"

    id = db.Column(db.Integer, primary_key=True)
    campaign_id = db.Column(db.Integer, db.ForeignKey("campaigns.id"), nullable=False)
    laboratory_id = db.Column(db.Integer, db.ForeignKey("laboratories.id"), nullable=False)
    requested_by = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=True)
    status = db.Column(db.String(20), default="pending", nullable=False)  # pending, approved, rejected
    message = db.Column(db.String(300), nullable=True)
    decision_note = db.Column(db.String(300), nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    decided_at = db.Column(db.DateTime, nullable=True)
    decided_by = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=True)

    campaign = db.relationship("Campaign", backref=db.backref("enrollment_requests", cascade="all, delete-orphan"))
    laboratory = db.relationship("Laboratory", backref=db.backref("enrollment_requests", cascade="all, delete-orphan"))


class PortalDocument(db.Model):
    """PDF joint par l'équipe au catalogue ou au protocole statistique.
    Le contenu est stocké en base pour survivre aux redéploiements."""
    __tablename__ = "portal_documents"

    id = db.Column(db.Integer, primary_key=True)
    kind = db.Column(db.String(20), nullable=False)        # catalogue, protocol
    title = db.Column(db.String(200), nullable=False)
    filename = db.Column(db.String(255), nullable=False)
    size = db.Column(db.Integer, nullable=False, default=0)
    data = db.Column(db.LargeBinary, nullable=False)
    uploaded_by = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=True)
    uploaded_at = db.Column(db.DateTime, default=datetime.utcnow)
    year = db.Column(db.Integer, nullable=True)            # année du document (catalogue, registration, instruction)


class SheetIdentification(db.Model):
    """Feuille de saisie d'un laboratoire pour une campagne : identification + cellules brutes (brouillon)."""
    __tablename__ = "sheet_identifications"
    __table_args__ = (db.UniqueConstraint("campaign_id", "laboratory_id", name="uq_sheet_campaign_lab"),)

    id = db.Column(db.Integer, primary_key=True)
    campaign_id = db.Column(db.Integer, db.ForeignKey("campaigns.id"), nullable=False)
    laboratory_id = db.Column(db.Integer, db.ForeignKey("laboratories.id"), nullable=False)
    data = db.Column(db.Text, nullable=True)   # JSON : {"ident": {...}, "cells": {"<trial_id>": {...}}}
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class HomogeneityStudy(db.Model):
    """Étude d'homogénéité et de stabilité d'un essai (digitalisation de la fiche FTE 006).

    Les données saisies et les résultats calculés sont conservés en JSON : la structure de la
    fiche (20 échantillons x 6 prises d'essai, stabilité, transport, stockage) évolue avec la
    version du formulaire sans modifier la base."""
    __tablename__ = "homogeneity_studies"
    __table_args__ = (db.UniqueConstraint("trial_id", "kind", name="uq_homogeneity_study_trial_kind"),)

    id = db.Column(db.Integer, primary_key=True)
    trial_id = db.Column(db.Integer, db.ForeignKey("trials.id"), nullable=False)
    kind = db.Column(db.String(20), nullable=False, default="quantitative")  # quantitative (autres types : à venir)
    inputs = db.Column(db.Text, nullable=True)       # JSON : tout ce que l'opérateur a saisi
    result = db.Column(db.Text, nullable=True)       # JSON : résultats calculés (figés à la validation)
    status = db.Column(db.String(20), nullable=False, default="draft")  # draft, validated
    conclusion = db.Column(db.String(300), nullable=True)
    updated_by = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=True)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow)
    validated_by = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=True)
    validated_at = db.Column(db.DateTime, nullable=True)

    trial = db.relationship("Trial", backref=db.backref("homogeneity_studies", cascade="all, delete-orphan"))


class Parcel(db.Model):
    """Colis envoyé à un laboratoire pour une campagne : suivi, réception et échantillons reçus (par essai)."""
    __tablename__ = "parcels"
    __table_args__ = (db.UniqueConstraint("campaign_id", "laboratory_id", name="uq_parcel_campaign_lab"),)

    id = db.Column(db.Integer, primary_key=True)
    campaign_id = db.Column(db.Integer, db.ForeignKey("campaigns.id"), nullable=False)
    laboratory_id = db.Column(db.Integer, db.ForeignKey("laboratories.id"), nullable=False)
    carrier = db.Column(db.String(50), nullable=True)
    tracking_number = db.Column(db.String(100), nullable=True)
    dispatch_date = db.Column(db.Date, nullable=True)
    dispatch_confirmed = db.Column(db.Boolean, default=False, nullable=False)
    sample_quantity = db.Column(db.Integer, nullable=True)       # nombre total d'échantillons dans le colis
    received = db.Column(db.Boolean, default=False, nullable=False)
    reception_date = db.Column(db.DateTime, nullable=True)
    received_by = db.Column(db.String(120), nullable=True)
    qr_token = db.Column(db.String(64), unique=True, nullable=True)
    items = db.Column(db.Text, nullable=True)                    # JSON {clé d'essai: {"received": true, "at": "..."}}
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    campaign = db.relationship("Campaign")
    laboratory = db.relationship("Laboratory")

    def items_dict(self):
        import json
        try:
            return json.loads(self.items) if self.items else {}
        except ValueError:
            return {}
