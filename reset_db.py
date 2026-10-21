import os
from extensions import db

def reset_database():
    # Drop all tables and recreate the schema
    db.drop_all()
    db.create_all()
    print('Database schema reset: all tables dropped and recreated.')

if __name__ == '__main__':
    # Ensure the Flask app context is available
    try:
        # Import the Flask app to push an application context
        from app import app
        with app.app_context():
            reset_database()
    except Exception as e:
        print(f'Error resetting database: {e}')
