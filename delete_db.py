import os, sys

DB_PATH = os.path.join(os.path.dirname(__file__), 'ptms', 'ptms.db')

if os.path.isfile(DB_PATH):
    try:
        os.remove(DB_PATH)
        print(f'Deleted SQLite DB at {DB_PATH}')
    except Exception as e:
        print(f'Failed to delete DB: {e}')
        sys.exit(1)
else:
    print('DB file not found, nothing to delete.')
