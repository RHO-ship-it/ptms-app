import os
import random
from datetime import datetime
from app import create_app, db
from models import (
    Campaign, Trial, Laboratory, User, CampaignLaboratory,
    ParticipantForm, Submission
)
from werkzeug.security import generate_password_hash
import json

app = create_app()

def run():
    with app.app_context():
        print("Starting data generation for TTS-T2603...")

        # 1. Create or get 15 Laboratories and Users
        labs = []
        for i in range(1, 16):
            lab_code = f"LAB-{i:03d}"
            lab = Laboratory.query.filter_by(code=lab_code).first()
            if not lab:
                lab = Laboratory(
                    code=lab_code,
                    name=f"Laboratoire Test {i}",
                    contact_name=f"Contact {i}",
                    email=f"lab{i}@test.com",
                    commercial_status="confirme",
                    iso17025_accredited=random.choice([True, False])
                )
                db.session.add(lab)
                db.session.commit()
            
            labs.append(lab)

            # Check if user exists
            username = f"lab{i}.tts-t2603"
            user = User.query.filter_by(username=username).first()
            if not user:
                user = User(
                    username=username,
                    email=f"{username}@test.com",
                    password_hash=generate_password_hash("123456"),
                    full_name=f"Participant {i}",
                    role="participant",
                    laboratory_id=lab.id
                )
                db.session.add(user)
                db.session.commit()

        # 2. Create Campaign TTS-T2603
        campaign = Campaign.query.filter_by(reference="TTS-T2603").first()
        if not campaign:
            campaign = Campaign(
                reference="TTS-T2603",
                name="Campagne Essais Textiles 2026",
                description="Test complet pour 5 méthodes ISO (Traction, Pilling, Solidité).",
                status="active",
                start_date=datetime(2026, 6, 1).date(),
                end_date=datetime(2026, 7, 22).date(),
                created_by=1 # Assuming admin is 1
            )
            db.session.add(campaign)
            db.session.commit()
            print(f"Created Campaign: {campaign.reference}")
        else:
            print("Campaign TTS-T2603 already exists. Reusing it.")

        # Assign labs to campaign
        for lab in labs:
            link = CampaignLaboratory.query.filter_by(campaign_id=campaign.id, laboratory_id=lab.id).first()
            if not link:
                link = CampaignLaboratory(campaign_id=campaign.id, laboratory_id=lab.id)
                db.session.add(link)
        db.session.commit()

        # 3. Create Trials
        trials_data = [
            {"name": "Pilling", "parameter": "Boulochage", "method": "ISO 12945-2", "type": "qualitative", "expected": "Grade 4"},
            {"name": "Tensile Strength", "parameter": "Force de rupture", "method": "ISO 13934-1", "type": "quantitative", "unit": "N", "target": 500.0, "sd": 25.0},
            {"name": "Color fastness to organic solvents", "parameter": "Solidité aux solvants", "method": "ISO 105-X05", "type": "solidite"},
            {"name": "Color fastness to washing", "parameter": "Solidité au lavage", "method": "ISO 105-C10", "type": "solidite"},
            {"name": "Color fastness to water", "parameter": "Solidité à l'eau", "method": "ISO 105-E01", "type": "solidite"},
        ]

        trials = []
        for td in trials_data:
            trial = Trial.query.filter_by(campaign_id=campaign.id, method=td["method"]).first()
            if not trial:
                trial = Trial(
                    campaign_id=campaign.id,
                    name=td["name"],
                    trial_type=td["type"],
                    parameter=td["parameter"],
                    method=td["method"],
                    due_date=datetime(2026, 7, 22).date()
                )
                if td["type"] == "quantitative":
                    trial.unit = td.get("unit")
                    trial.target_value = td.get("target")
                    trial.target_sd = td.get("sd")
                    trial.sigma_pt_method = "fixe"
                if td["type"] == "qualitative":
                    trial.expected_qualitative_result = td.get("expected")
                
                db.session.add(trial)
                db.session.commit()
            trials.append(trial)

        # 4. Generate ParticipantForms and Submissions
        for lab in labs:
            for trial in trials:
                form = ParticipantForm.query.filter_by(trial_id=trial.id, laboratory_id=lab.id).first()
                if not form:
                    form = ParticipantForm(
                        trial_id=trial.id,
                        laboratory_id=lab.id,
                        status="pending",
                        sample_code=f"TTS-T2603-{lab.code}",
                        tracking_number=f"DHL-{random.randint(100000, 999999)}",
                        carrier="DHL",
                        dispatch_date=datetime(2026, 6, 1).date(),
                        received=True,
                        reception_date=datetime(2026, 6, 5)
                    )
                    form.ensure_qr_token()
                    db.session.add(form)
                    db.session.commit()

                # Generate submissions for 12 out of 15 labs (3 pending)
                if int(lab.code.split('-')[1]) <= 12 and not form.submission:
                    submission = Submission(
                        form_id=form.id,
                        submitted_by=1, # Admin/system
                        submitted_at=datetime.utcnow(),
                        confirmed=True,
                        confirmed_at=datetime.utcnow(),
                        iso17025_accredited=lab.iso17025_accredited,
                        method_used=trial.method
                    )
                    
                    if trial.trial_type == "quantitative":
                        # generate value around 500
                        val = random.gauss(500, 25)
                        submission.value = round(val, 1)
                        submission.replicate_2 = round(val + random.uniform(-10, 10), 1)
                        
                    elif trial.trial_type == "qualitative":
                        submission.qualitative_result = random.choice(["Grade 3", "Grade 4", "Grade 4-5"])
                        
                    elif trial.trial_type == "solidite":
                        # Generate solidite data
                        params = ["degradation", "acetate", "coton", "polyamide", "polyester", "acrylique", "laine"]
                        sol_data = {}
                        for p in params:
                            sol_data[p] = random.choice([3, 3.5, 4, 4.5, 5])
                        submission.solidite_data = json.dumps(sol_data)
                    
                    form.status = "submitted"
                    db.session.add(submission)
                    
        db.session.commit()
        print("Data generation completed successfully!")

if __name__ == "__main__":
    run()
