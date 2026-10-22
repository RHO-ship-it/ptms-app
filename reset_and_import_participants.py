#!/usr/bin/env python
"""
Reset the PTMS database (drop all tables, recreate the schema) and
import ONLY the participant data from the Excel workbook you uploaded.

The workbook is stored outside the workspace at:
C:\Users\rho\.gemini\antigravity\brain\972e5e67-08dd-477b-9890-1e426173a833\.user_uploaded\media_1792594287508_3b5a1302.xlsx

The script expects a sheet named “ParticipantForms” (case‑insensitive)
or, if the workbook has only one sheet, that sheet will be used.
Required columns (case‑insensitive):

- campaign_reference   – reference of the campaign the participant belongs to
- trial_name           – name of the trial (within the campaign)
- laboratory_id        – integer ID of the laboratory **OR** `laboratory_code`
- status               – e.g. “draft”, “pending”, “submitted”
- submitted_at         – optional ISO‑8601 datetime (e.g. 2023-04-01T12:30:00)

Any extra columns are ignored; missing optional columns are left as NULL.
"""

import os
import sys
import pandas as pd
from flask import Flask
from config import Config
from extensions import db
from app import create_app
from models import Campaign, Trial, ParticipantForm, Laboratory

# ----------------------------------------------------------------------
# 1️⃣  Absolute path to the uploaded Excel file (do NOT move it)
# ----------------------------------------------------------------------
EXCEL_PATH = (
    r"C:\\Users\\rho\.gemini\antigravity\brain"
    r"\\972e5e67-08dd-477b-9890-1e426173a833"
    r"\\.user_uploaded\\media_1792594287508_3b5a1302.xlsx"
)

if not os.path.isfile(EXCEL_PATH):
    sys.stderr.write(f"[ERROR] Excel file not found at {EXCEL_PATH}\n")
    sys.exit(1)

# ----------------------------------------------------------------------
# Helper: get or create a row
# ----------------------------------------------------------------------
def get_or_create(model, defaults=None, **kwargs):
    instance = db.session.query(model).filter_by(**kwargs).first()
    if instance:
        return instance, False
    params = dict(kwargs)
    if defaults:
        params.update(defaults)
    instance = model(**params)
    db.session.add(instance)
    db.session.commit()
    return instance, True

# ----------------------------------------------------------------------
# 2️⃣  Reset the DB (drop + create)
# ----------------------------------------------------------------------
def reset_database(app: Flask):
    """Drop all tables and recreate the schema."""
    with app.app_context():
        db.drop_all()
        db.create_all()
        print('[INFO] Database schema reset – all tables dropped & recreated.')

# ----------------------------------------------------------------------
# 3️⃣  Load participants from the Excel sheet
# ----------------------------------------------------------------------
def import_participants(app: Flask):
    """Read the Excel workbook and insert ParticipantForm rows."""
    with app.app_context():
        xls = pd.ExcelFile(EXCEL_PATH)
        # Choose the sheet that contains participant forms
        sheet_name = None
        for name in xls.sheet_names:
            if "participant" in name.lower() or "form" in name.lower():
                sheet_name = name
                break
        if sheet_name is None:
            sheet_name = xls.sheet_names[0]
        df = pd.read_excel(xls, sheet_name=sheet_name)
        print(f"[INFO] Loading sheet '{sheet_name}' ({len(df)} rows)")
        rows_added = 0
        for _, row in df.iterrows():
            # Resolve foreign keys
            campaign = db.session.query(Campaign).filter_by(reference=row.get('campaign_reference')).first()
            if not campaign:
                print(f"[WARN] Campaign reference '{row.get('campaign_reference')}' not found – skipping row.")
                continue
            trial = db.session.query(Trial).filter_by(name=row.get('trial_name'), campaign_id=campaign.id).first()
            if not trial:
                print(f"[WARN] Trial '{row.get('trial_name')}' (campaign {campaign.reference}) not found – skipping row.")
                continue
            # Laboratory: allow either an integer ID or a code
            lab = None
            if pd.notna(row.get('laboratory_id')):
                lab = db.session.query(Laboratory).get(int(row['laboratory_id']))
            elif pd.notna(row.get('laboratory_code')):
                lab = db.session.query(Laboratory).filter_by(code=row['laboratory_code']).first()
            # Create ParticipantForm
            form = ParticipantForm(
                trial_id=trial.id,
                laboratory_id=lab.id if lab else None,
                status=row.get('status') or 'draft',
                submitted_at=row.get('submitted_at'),
            )
            db.session.add(form)
            rows_added += 1
        db.session.commit()
        print(f"[INFO] Imported {rows_added} participant form(s).")

# ----------------------------------------------------------------------
# 4️⃣  Main entry point
# ----------------------------------------------------------------------
if __name__ == '__main__':
    flask_app = create_app(Config)
    reset_database(flask_app)
    import_participants(flask_app)
    print('[DONE] Reset + participant import completed.')
