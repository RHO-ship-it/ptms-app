import os
import pandas as pd
from extensions import db
from models import User, Laboratory, CommercialTracking, Campaign, CampaignLaboratory, Trial, ParticipantForm, HomogeneityStabilityAssessment

# Path to the uploaded Excel file (must be placed in the same folder for simplicity)
EXCEL_PATH = os.path.join(os.path.dirname(__file__), '..', 'media_1792593248330_76a82f54.xlsx')

# Helper to create objects safely
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

# Mapping of sheet name (case‑insensitive) to a loader function
def load_users(df):
    for _, row in df.iterrows():
        # Expected columns: username, email, password, full_name, role, laboratory_code, active
        lab = None
        if pd.notna(row.get('laboratory_code')):
            lab, _ = get_or_create(Laboratory, code=row['laboratory_code'], defaults={'name': row.get('laboratory_name', ''), 'email': row.get('laboratory_email')})
        user, _ = get_or_create(User,
                                 username=row['username'],
                                 email=row['email'],
                                 full_name=row.get('full_name') or row['username'],
                                 role=row.get('role') or 'participant',
                                 laboratory_id=lab.id if lab else None,
                                 active=bool(row.get('active', True)))
        # Set password if provided (plain text in Excel – for demo only)
        if pd.notna(row.get('password')):
            user.set_password(str(row['password']))
        db.session.add(user)
    db.session.commit()

def load_laboratories(df):
    for _, row in df.iterrows():
        lab, _ = get_or_create(Laboratory,
                               code=row['code'],
                               name=row.get('name'),
                               contact_name=row.get('contact_name'),
                               email=row.get('email'),
                               phone=row.get('phone'),
                               address=row.get('address'),
                               commercial_status=row.get('commercial_status', 'prospect'),
                               iso17025_accredited=bool(row.get('iso17025_accredited', False)),
                               active=bool(row.get('active', True)))
    db.session.commit()

def load_campaigns(df):
    for _, row in df.iterrows():
        campaign, _ = get_or_create(Campaign,
                                    reference=row['reference'],
                                    name=row.get('name'),
                                    description=row.get('description'),
                                    status=row.get('status', 'draft'),
                                    start_date=row.get('start_date'),
                                    end_date=row.get('end_date'),
                                    report_published=bool(row.get('report_published', False)),
                                    created_by=row.get('created_by'))
    db.session.commit()

def load_campaign_labs(df):
    for _, row in df.iterrows():
        campaign = db.session.query(Campaign).filter_by(reference=row['campaign_reference']).first()
        lab = db.session.query(Laboratory).filter_by(code=row['laboratory_code']).first()
        if campaign and lab:
            get_or_create(CampaignLaboratory, campaign_id=campaign.id, laboratory_id=lab.id)
    db.session.commit()

def load_trials(df):
    for _, row in df.iterrows():
        campaign = db.session.query(Campaign).filter_by(reference=row['campaign_reference']).first()
        if not campaign:
            continue
        trial, _ = get_or_create(Trial,
                                 campaign_id=campaign.id,
                                 name=row['name'],
                                 trial_type=row.get('trial_type', 'quantitative'),
                                 parameter=row.get('parameter'),
                                 unit=row.get('unit'),
                                 method=row.get('method'),
                                 sample_reference=row.get('sample_reference'),
                                 target_value=row.get('target_value'),
                                 target_sd=row.get('target_sd'),
                                 expected_qualitative_result=row.get('expected_qualitative_result'),
                                 due_date=row.get('due_date'),
                                 sigma_pt_method=row.get('sigma_pt_method', 'fixe'),
                                 min_participants=row.get('min_participants'))
    db.session.commit()

def load_participant_forms(df):
    # This assumes a foreign key to trial by trial_name and campaign_reference
    for _, row in df.iterrows():
        campaign = db.session.query(Campaign).filter_by(reference=row['campaign_reference']).first()
        trial = db.session.query(Trial).filter_by(name=row['trial_name'], campaign_id=campaign.id if campaign else None).first()
        if not trial:
            continue
        form = ParticipantForm(
            trial_id=trial.id,
            laboratory_id=row.get('laboratory_id'),
            status=row.get('status', 'draft'),
            submitted_at=row.get('submitted_at')
        )
        db.session.add(form)
    db.session.commit()

# Main dispatcher
def main():
    if not os.path.isfile(EXCEL_PATH):
        print(f"Excel file not found at {EXCEL_PATH}")
        return
    xls = pd.ExcelFile(EXCEL_PATH)
    for sheet_name in xls.sheet_names:
        df = pd.read_excel(xls, sheet_name=sheet_name)
        lname = sheet_name.strip().lower()
        print(f"Loading sheet: {sheet_name} ({df.shape[0]} rows)")
        if 'user' in lname:
            load_users(df)
        elif 'laboratory' in lname:
            load_laboratories(df)
        elif 'campaign' in lname and 'lab' not in lname:
            load_campaigns(df)
        elif 'campaign_lab' in lname or ('campaign' in lname and 'laboratory' in lname):
            load_campaign_labs(df)
        elif 'trial' in lname:
            load_trials(df)
        elif 'form' in lname:
            load_participant_forms(df)
        else:
            print(f"No loader for sheet {sheet_name}, skipping.")
    print('Import completed.')

if __name__ == '__main__':
    main()
