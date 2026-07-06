"""
Database Module for Anemia Classification System
-----------------------------------------------
This module handles database operations for storing user, admin, and classification data.
Supports both PostgreSQL (Railway) and SQLite (local development).
"""

import os
import shutil
import datetime
from zoneinfo import ZoneInfo
from werkzeug.security import generate_password_hash, check_password_hash

# Detect if PostgreSQL is available (Railway provides these env vars)
USE_POSTGRES = os.environ.get('DATABASE_URL') or all(os.environ.get(key) for key in ['PGHOST', 'PGDATABASE', 'PGUSER', 'PGPASSWORD'])

if USE_POSTGRES:
    import psycopg2
    from psycopg2.extras import RealDictCursor
    print("Using PostgreSQL database (Railway)")
    DB_PATH = None  # Not used for PostgreSQL
else:
    import sqlite3

    # Resolve SQLite path with strong preference for a mounted volume in Railway
    # Priority order:
    # 1) DATABASE_PATH (explicit)
    # 2) RAILWAY_VOLUME_MOUNT_PATH (official env when a volume is mounted)
    # 3) Heuristic for Railway default mount at /data
    # 4) Local file in project dir (development)
    default_local_db = 'anemia_classification.db'
    env_db_path = os.environ.get('DATABASE_PATH')
    volume_mount = os.environ.get('RAILWAY_VOLUME_MOUNT_PATH')
    running_on_railway = any(k in os.environ for k in [
        'RAILWAY_PROJECT_ID', 'RAILWAY_ENVIRONMENT', 'RAILWAY_STATIC_URL', 'RAILWAY_GIT_COMMIT_SHA'
    ])

    if env_db_path:
        DB_PATH = env_db_path
    elif volume_mount:
        DB_PATH = os.path.join(volume_mount, 'anemocheck', default_local_db)
    elif running_on_railway:
        # Common default mount path for Railway volumes
        DB_PATH = os.path.join('/data', 'anemocheck', default_local_db)
    else:
        DB_PATH = default_local_db

    # Ensure directory exists if using an absolute/volume path
    db_dir = os.path.dirname(DB_PATH)
    if db_dir:
        try:
            os.makedirs(db_dir, exist_ok=True)
        except Exception:
            # Directory creation might fail on read-only environments; ignore here
            pass

    # If we're targeting a volume path and it's empty, but a local dev DB exists,
    # copy it once to preserve data and schema without changing any columns.
    try:
        if DB_PATH != default_local_db and not os.path.exists(DB_PATH) and os.path.exists(default_local_db):
            shutil.copy2(default_local_db, DB_PATH)
    except Exception as _e:
        # Non-fatal: if copy fails we will initialize a fresh DB below
        pass

    print(f"Using SQLite database at: {DB_PATH}")

def convert_to_philippines_time(timestamp_str):
    """Convert timestamp to Philippines timezone (UTC+8)"""
    from timezone_utils import format_philippines_time_ampm
    return format_philippines_time_ampm(timestamp_str)

def get_db_connection():
    """Get database connection (PostgreSQL or SQLite)."""
    if USE_POSTGRES:
        # Try DATABASE_URL first (Railway's preferred method)
        if os.environ.get('DATABASE_URL'):
            conn = psycopg2.connect(os.environ.get('DATABASE_URL'))
        else:
            # Fallback to individual variables
            conn = psycopg2.connect(
                host=os.environ.get('PGHOST'),
                port=os.environ.get('PGPORT', 5432),
                database=os.environ.get('PGDATABASE'),
                user=os.environ.get('PGUSER'),
                password=os.environ.get('PGPASSWORD')
            )
        # Use RealDictCursor to return dictionaries instead of tuples
        conn.cursor_factory = RealDictCursor
        return conn
    else:
        conn = sqlite3.connect(DB_PATH)
        conn.row_factory = sqlite3.Row
        return conn

def get_id_type():
    """Get the correct ID type for the database."""
    return "SERIAL PRIMARY KEY" if USE_POSTGRES else "INTEGER PRIMARY KEY AUTOINCREMENT"

def get_text_type():
    """Get the correct text type for the database."""
    return "VARCHAR(255)" if USE_POSTGRES else "TEXT"

def get_real_type():
    """Get the correct real type for the database."""
    return "REAL" if USE_POSTGRES else "REAL"

def get_integer_type():
    """Get the correct integer type for the database."""
    return "INTEGER" if USE_POSTGRES else "INTEGER"

def execute_sql(cursor, query, params=None):
    """Execute SQL with proper parameter binding for the database type."""
    if USE_POSTGRES:
        # PostgreSQL uses %s placeholders
        if params:
            cursor.execute(query.replace('?', '%s'), params)
        else:
            cursor.execute(query.replace('?', '%s'))
    else:
        # SQLite uses ? placeholders
        if params:
            cursor.execute(query, params)
        else:
            cursor.execute(query)


def sql_now():
    """Current timestamp expression for the active database."""
    return "NOW()" if USE_POSTGRES else "datetime('now')"


def sql_age_years_from_dob(dob_column='date_of_birth'):
    """Expression that returns age in whole years from a date_of_birth column."""
    if USE_POSTGRES:
        return f"EXTRACT(YEAR FROM AGE(CURRENT_DATE, {dob_column}::date))"
    return f"(strftime('%Y', 'now') - strftime('%Y', {dob_column}))"


def fetch_last_insert_id(cursor):
    """Read inserted row id after INSERT (PostgreSQL requires RETURNING)."""
    if USE_POSTGRES:
        row = cursor.fetchone()
        return row['id'] if row else None
    return cursor.lastrowid


def table_exists(cursor, table_name):
    """Check whether a table exists in the current database."""
    if USE_POSTGRES:
        execute_sql(
            cursor,
            "SELECT 1 FROM information_schema.tables WHERE table_name = ?",
            (table_name,),
        )
    else:
        execute_sql(
            cursor,
            "SELECT name FROM sqlite_master WHERE type='table' AND name=?",
            (table_name,),
        )
    return cursor.fetchone() is not None


def column_exists(cursor, table_name, column_name):
    """Check whether a column exists on a table."""
    if USE_POSTGRES:
        execute_sql(
            cursor,
            """
            SELECT 1 FROM information_schema.columns
            WHERE table_name = ? AND column_name = ?
            """,
            (table_name, column_name),
        )
    else:
        execute_sql(cursor, f"PRAGMA table_info({table_name})")
        columns = [row[1] for row in cursor.fetchall()]
        return column_name in columns
    return cursor.fetchone() is not None

def execute_many(cursor, query, params_list):
    """Execute the same SQL for many parameter sets."""
    if not params_list:
        return
    if USE_POSTGRES:
        cursor.executemany(query.replace('?', '%s'), params_list)
    else:
        cursor.executemany(query, params_list)


def init_db():
    """Initialize the database with necessary tables."""
    try:
        print("Getting database connection...")
        conn = get_db_connection()
        cursor = conn.cursor()
        print("Database connection established")
    except Exception as e:
        print(f"Failed to get database connection: {e}")
        raise e
    
    # Create users table
    try:
        print("Creating users table...")
        cursor.execute(f'''
        CREATE TABLE IF NOT EXISTS users (
            id {get_id_type()},
            username {get_text_type()} UNIQUE NOT NULL,
            password_hash TEXT NOT NULL,
            email {get_text_type()} UNIQUE NOT NULL,
            first_name {get_text_type()},
            last_name {get_text_type()},
            gender {get_text_type()},
            date_of_birth {get_text_type()},
            medical_id {get_text_type()} UNIQUE,
            is_admin {get_integer_type()} DEFAULT 0,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            last_login TIMESTAMP
        )
        ''')
        print("Users table created successfully")
    except Exception as e:
        print(f"Error creating users table: {e}")
        raise e
    
    # Create classification_history table
    cursor.execute(f'''
    CREATE TABLE IF NOT EXISTS classification_history (
        id {get_id_type()},
        user_id {get_integer_type()},
        wbc {get_real_type()} NOT NULL,
        rbc {get_real_type()} NOT NULL,
        hgb {get_real_type()} NOT NULL,
        hct {get_real_type()} NOT NULL,
        mcv {get_real_type()} NOT NULL,
        mch {get_real_type()} NOT NULL,
        mchc {get_real_type()} NOT NULL,
        plt {get_real_type()} NOT NULL,
        predicted_class {get_text_type()} NOT NULL,
        confidence {get_real_type()} NOT NULL,
        recommendation {get_text_type()},
        notes {get_text_type()},
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY (user_id) REFERENCES users(id)
    )
''')
    
    # Ensure patient columns exist even on existing DBs
    try:
        _ensure_patient_columns(cursor)
        conn.commit()
    except Exception as _e:
        # Non-fatal; add during first insert too
        pass
    
    # Create medical_data table for additional patient information
    cursor.execute(f'''
    CREATE TABLE IF NOT EXISTS medical_data (
        id {get_id_type()},
        user_id {get_integer_type()} UNIQUE,
        height {get_real_type()},
        weight {get_real_type()},
        blood_type {get_text_type()},
        medical_conditions {get_text_type()},
        medications {get_text_type()},
        updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY (user_id) REFERENCES users(id)
    )
    ''')
    
    # Create a table for system settings
    cursor.execute(f'''
    CREATE TABLE IF NOT EXISTS system_settings (
        id {get_id_type()},
        setting_name {get_text_type()} UNIQUE NOT NULL,
        setting_value {get_text_type()},
        updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        updated_by {get_integer_type()},
        FOREIGN KEY (updated_by) REFERENCES users(id)
    )
    ''')
    
    # Create imported_files table to track imported files (MUST BE FIRST)
    try:
        print("Creating imported_files table...")
        cursor.execute(f'''
        CREATE TABLE IF NOT EXISTS imported_files (
            id {get_id_type()},
            filename {get_text_type()} NOT NULL,
            original_filename {get_text_type()} NOT NULL,
            total_records {get_integer_type()} NOT NULL,
            imported_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            is_applied {get_integer_type()} DEFAULT 1,
            imported_by {get_integer_type()},
            FOREIGN KEY (imported_by) REFERENCES users(id)
        )
        ''')
        print("imported_files table created successfully")
    except Exception as e:
        print(f"Error creating imported_files table: {e}")
        raise e
    
    # Create classification_import_data table for imported statistics (AFTER imported_files)
    try:
        print("Creating classification_import_data table...")
        cursor.execute(f'''
        CREATE TABLE IF NOT EXISTS classification_import_data (
            id {get_id_type()},
            age {get_integer_type()} NOT NULL,
            gender {get_text_type()} NOT NULL,
            category {get_text_type()} NOT NULL,
            imported_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            file_id {get_integer_type()},
            FOREIGN KEY (file_id) REFERENCES imported_files(id)
        )
        ''')
        print("classification_import_data table created successfully")
    except Exception as e:
        print(f"Error creating classification_import_data table: {e}")
        raise e
    
    # Create otp_verification table
    cursor.execute(f'''
    CREATE TABLE IF NOT EXISTS otp_verification (
        id {get_id_type()},
        email {get_text_type()} UNIQUE NOT NULL,
        otp_code {get_text_type()} NOT NULL,
        username {get_text_type()} NOT NULL,
        password_hash TEXT NOT NULL,
        first_name {get_text_type()} NOT NULL,
        last_name {get_text_type()} NOT NULL,
        gender {get_text_type()} NOT NULL,
        date_of_birth DATE NOT NULL,
        medical_id {get_text_type()} NOT NULL,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        expires_at TIMESTAMP NOT NULL,
        is_verified {get_integer_type()} DEFAULT 0
    )
    ''')
    
    # Create password_reset_otp table
    cursor.execute(f'''
    CREATE TABLE IF NOT EXISTS password_reset_otp (
        id {get_id_type()},
        email {get_text_type()} NOT NULL,
        otp_code {get_text_type()} NOT NULL,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        expires_at TIMESTAMP NOT NULL,
        is_verified {get_integer_type()} DEFAULT 0
    )
    ''')
    
    # Create chat_conversations table
    cursor.execute(f'''
    CREATE TABLE IF NOT EXISTS chat_conversations (
        id {get_id_type()},
        user_id {get_integer_type()} NOT NULL,
        admin_id {get_integer_type()},
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY (user_id) REFERENCES users(id),
        FOREIGN KEY (admin_id) REFERENCES users(id)
    )
    ''')
    
    # Create chat_messages table
    cursor.execute(f'''
    CREATE TABLE IF NOT EXISTS chat_messages (
        id {get_id_type()},
        conversation_id {get_integer_type()} NOT NULL,
        sender_id {get_integer_type()} NOT NULL,
        message_text TEXT NOT NULL,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY (conversation_id) REFERENCES chat_conversations(id),
        FOREIGN KEY (sender_id) REFERENCES users(id)
    )
    ''')

    try:
        _ensure_chat_columns(cursor)
        conn.commit()
    except Exception:
        pass
    
    # Insert default admin user if not exists
    try:
        print("Checking for admin user...")
        execute_sql(cursor, "SELECT * FROM users WHERE username = ?", ('admin',))
        admin_exists = cursor.fetchone()
        
        if not admin_exists:
            print("Creating default admin user...")
            try:
                # Create admin user directly with SQL to avoid function call issues
                admin_password_hash = generate_password_hash('admin123')
                execute_sql(cursor, """
                    INSERT INTO users 
                    (username, password_hash, email, first_name, last_name, is_admin)
                    VALUES (?, ?, ?, ?, ?, ?)
                """, ('admin', admin_password_hash, 'admin@anemocheck.com', 'System', 'Administrator', 1))
                
                # Get the admin user ID (PostgreSQL doesn't use lastrowid)
                if USE_POSTGRES:
                    # For PostgreSQL, get the ID from the inserted row
                    execute_sql(cursor, "SELECT id FROM users WHERE username = ?", ('admin',))
                    admin_user = cursor.fetchone()
                    admin_user_id = admin_user['id']
                else:
                    # For SQLite, use lastrowid
                    admin_user_id = cursor.lastrowid
                
                # Create medical_data entry for admin
                execute_sql(cursor, "INSERT INTO medical_data (user_id) VALUES (?)", (admin_user_id,))
                
                conn.commit()
                print("Default admin user created successfully!")
            except Exception as e:
                print(f"Error creating admin user: {e}")
                # Don't fail the entire initialization for this
        else:
            print("Admin user already exists.")
    except Exception as e:
        print(f"Error checking for admin user: {e}")
        # Don't fail the entire initialization for this
    
    # Insert default system settings
    default_settings = [
        ('model_type', 'decision_tree'),
        ('visualization_enabled', 'true'),
        ('recommendation_enabled', 'true'),
        ('threshold_normal', '12.0'),
        ('threshold_mild', '10.0'),
        ('threshold_moderate', '8.0')
    ]
    
    for name, value in default_settings:
        execute_sql(cursor, "SELECT * FROM system_settings WHERE setting_name = ?", (name,))
        if not cursor.fetchone():
            execute_sql(cursor,
                "INSERT INTO system_settings (setting_name, setting_value) VALUES (?, ?)",
                (name, value)
            )
    
    conn.commit()
    conn.close()
    
    print("Database initialized successfully.")


def create_user(username, password=None, email=None, first_name=None, last_name=None, 
                gender=None, date_of_birth=None, medical_id=None, is_admin=0, password_hash=None):
    """Create a new user in the database."""
    conn = get_db_connection()
    cursor = conn.cursor()
    
    try:
        # Normalize optional fields
        normalized_medical_id = None
        if medical_id is not None:
            mid = str(medical_id).strip()
            normalized_medical_id = mid if mid else None  # store NULL when empty
        
        # Use provided password_hash or generate from password
        if password_hash is None:
            password_hash = generate_password_hash(password)
        
        # Use Philippines time for created_at timestamp
        from timezone_utils import get_philippines_time_for_db
        ph_timestamp = get_philippines_time_for_db()
        
        if USE_POSTGRES:
            execute_sql(cursor,
                """
                INSERT INTO users 
                (username, password_hash, email, first_name, last_name, gender, 
                 date_of_birth, medical_id, is_admin, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                RETURNING id
                """,
                (username, password_hash, email, first_name, last_name, gender, 
                 date_of_birth, normalized_medical_id, is_admin, ph_timestamp)
            )
            user_id = fetch_last_insert_id(cursor)
        else:
            execute_sql(cursor,
                """
                INSERT INTO users 
                (username, password_hash, email, first_name, last_name, gender, 
                 date_of_birth, medical_id, is_admin, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (username, password_hash, email, first_name, last_name, gender, 
                 date_of_birth, normalized_medical_id, is_admin, ph_timestamp)
            )
            conn.commit()
            user_id = fetch_last_insert_id(cursor)
        
        # Create empty medical_data entry for the user
        execute_sql(cursor,
            "INSERT INTO medical_data (user_id) VALUES (?)",
            (user_id,)
        )
        conn.commit()
        return True, user_id
    except Exception as e:
        error_msg = str(e)
        if USE_POSTGRES:
            if "duplicate key value violates unique constraint" in error_msg and "users_username_key" in error_msg:
                return False, "Username already exists."
            elif "duplicate key value violates unique constraint" in error_msg and "users_email_key" in error_msg:
                return False, "Email already exists."
            elif "duplicate key value violates unique constraint" in error_msg and "users_medical_id_key" in error_msg:
                return False, "Medical ID already exists."
            else:
                return False, f"Database error: {error_msg}"
        else:
            if "UNIQUE constraint failed: users.username" in error_msg:
                return False, "Username already exists."
            elif "UNIQUE constraint failed: users.email" in error_msg:
                return False, "Email already exists."
            elif "UNIQUE constraint failed: users.medical_id" in error_msg:
                return False, "Medical ID already exists."
            else:
                return False, f"Database error: {error_msg}"
    finally:
        conn.close()


def verify_user(username, password):
    """Verify user credentials."""
    conn = get_db_connection()
    cursor = conn.cursor()
    
    execute_sql(cursor, "SELECT * FROM users WHERE username = ?", (username,))
    user = cursor.fetchone()
    
    if user and check_password_hash(user['password_hash'], password):
        # Update last login time
        from timezone_utils import get_philippines_time_for_db
        execute_sql(cursor,
            "UPDATE users SET last_login = ? WHERE id = ?",
            (get_philippines_time_for_db(), user['id'])
        )
        conn.commit()
        conn.close()
        return True, dict(user)
    
    conn.close()
    return False, "Invalid username or password."


def get_user(user_id):
    """Get user by ID."""
    conn = get_db_connection()
    cursor = conn.cursor()
    
    execute_sql(cursor, "SELECT * FROM users WHERE id = ?", (user_id,))
    user = cursor.fetchone()
    
    conn.close()
    return dict(user) if user else None


def update_user(user_id, **kwargs):
    """Update user information."""
    conn = get_db_connection()
    cursor = conn.cursor()
    
    # Build the SET part of the SQL query
    set_clauses = []
    values = []
    
    for key, value in kwargs.items():
        if key == 'password':
            set_clauses.append('password_hash = ?')
            values.append(generate_password_hash(value))
        elif key in ['username', 'email', 'first_name', 'last_name', 'gender', 
                     'date_of_birth', 'medical_id', 'is_admin']:
            set_clauses.append(f'{key} = ?')
            if key == 'medical_id':
                # Normalize to NULL when empty so UNIQUE does not block blanks
                if value is None:
                    values.append(None)
                else:
                    mid = str(value).strip()
                    values.append(mid if mid else None)
            else:
                values.append(value)
    
    if not set_clauses:
        conn.close()
        return False, "No valid fields to update."
    
    sql = f"UPDATE users SET {', '.join(set_clauses)} WHERE id = ?"
    values.append(user_id)
    
    try:
        execute_sql(cursor, sql, tuple(values))
        conn.commit()
        conn.close()
        return True, "User updated successfully."
    except Exception as e:
        conn.close()
        error_msg = str(e)
        if USE_POSTGRES:
            if "users_username_key" in error_msg:
                return False, "Username already exists."
            elif "users_email_key" in error_msg:
                return False, "Email already exists."
            elif "users_medical_id_key" in error_msg:
                return False, "Medical ID already exists."
        else:
            if "UNIQUE constraint failed: users.username" in error_msg:
                return False, "Username already exists."
            elif "UNIQUE constraint failed: users.email" in error_msg:
                return False, "Email already exists."
            elif "UNIQUE constraint failed: users.medical_id" in error_msg:
                return False, "Medical ID already exists."
        return False, error_msg


# def add_classification_record(user_id, hemoglobin, predicted_class, confidence, recommendation, notes=None):
#     """Add a classification record to history."""
#     conn = get_db_connection()
#     cursor = conn.cursor()
    
#     cursor.execute(
#         """
#         INSERT INTO classification_history 
#         (user_id, hemoglobin, predicted_class, confidence, recommendation, notes)
#         VALUES (?, ?, ?, ?, ?, ?)
#         """,
#         (user_id, hemoglobin, predicted_class, confidence, recommendation, notes)
#     )
    
#     conn.commit()
#     record_id = cursor.lastrowid
#     conn.close()
    
#     return record_id

def add_classification_record(*args, **kwargs):
    """Add a classification record. Supports two call styles:

    1) Minimal (legacy) form used by some routes:
       add_classification_record(user_id=..., hemoglobin=..., predicted_class=..., confidence=..., recommendation=..., notes=...)

    2) Full CBC form used by other routes:
       add_classification_record(user_id=..., wbc=..., rbc=..., hgb=..., hct=..., mcv=..., mch=..., mchc=..., plt=..., neutrophils=..., ..., predicted_class=..., confidence=..., recommendation=..., notes=...)

    This function always writes a created_at timestamp in Asia/Manila time.
    """
    # Accept both positional and keyword forms; normalize into kwargs
    if len(args) == 1 and not kwargs:
        # Called as add_classification_record(dict) - not expected, treat as error
        raise TypeError("add_classification_record requires keyword arguments")

    user_id = kwargs.get('user_id')
    if user_id is None:
        raise TypeError('user_id is required')

    # Philippines time string - store Philippines time directly in database
    ph_now = datetime.datetime.now(ZoneInfo('Asia/Manila')).strftime('%Y-%m-%d %H:%M:%S')

    conn = get_db_connection()
    cursor = conn.cursor()

    # Legacy simple API: 'hemoglobin' key may be used instead of 'hgb'
    if 'hemoglobin' in kwargs or ('hgb' in kwargs and len(kwargs) <= 6):
        hgb = kwargs.get('hemoglobin') if 'hemoglobin' in kwargs else kwargs.get('hgb')
        predicted_class = kwargs.get('predicted_class')
        confidence = kwargs.get('confidence')
        recommendation = kwargs.get('recommendation')
        notes = kwargs.get('notes')

        if USE_POSTGRES:
            execute_sql(cursor,
                """
                INSERT INTO classification_history
                (user_id, hgb, predicted_class, confidence, recommendation, notes, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                RETURNING id
                """,
                (user_id, hgb, predicted_class, confidence, recommendation, notes, ph_now)
            )
        else:
            execute_sql(cursor,
                """
                INSERT INTO classification_history
                (user_id, hgb, predicted_class, confidence, recommendation, notes, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (user_id, hgb, predicted_class, confidence, recommendation, notes, ph_now)
            )

    else:
        # Full CBC form - pick values from kwargs, defaulting to 0 or None where sensible
        # Ensure patient columns exist (SQLite safe)
        try:
            _ensure_patient_columns(cursor)
        except Exception:
            pass

        fields = [
            'wbc', 'rbc', 'hgb', 'hct', 'mcv', 'mch', 'mchc', 'plt',
            'neutrophils', 'lymphocytes', 'monocytes', 'eosinophils', 'basophil', 'immature_granulocytes',
            'patient_name', 'patient_age', 'patient_gender'
        ]
        # Use 0.8 for immature_granulocytes if not provided (matches training data median)
        # But explicitly preserve 0.0 values when provided
        values = []
        for f in fields:
            if f == 'immature_granulocytes':
                # Check if value is explicitly provided (including 0.0)
                # Use 'in' check to distinguish between not provided vs provided as 0.0
                if f in kwargs:
                    # Value was explicitly provided (could be 0.0, 0.8, or any other value)
                    values.append(float(kwargs[f]))
                else:
                    # Value was not provided, use default
                    values.append(0.8)
            elif f in ('patient_name', 'patient_age', 'patient_gender'):
                values.append(kwargs.get(f))
            else:
                values.append(kwargs.get(f, 0.0))
        predicted_class = kwargs.get('predicted_class')
        confidence = kwargs.get('confidence')
        recommendation = kwargs.get('recommendation')
        notes = kwargs.get('notes')

        insert_sql = """
            INSERT INTO classification_history 
            (user_id, wbc, rbc, hgb, hct, mcv, mch, mchc, plt,
             neutrophils, lymphocytes, monocytes, eosinophils, basophil, immature_granulocytes,
             patient_name, patient_age, patient_gender,
             predicted_class, confidence, recommendation, notes, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """
        insert_params = tuple([user_id] + values + [predicted_class, confidence, recommendation, notes, ph_now])
        if USE_POSTGRES:
            execute_sql(cursor, insert_sql + " RETURNING id", insert_params)
        else:
            execute_sql(cursor, insert_sql, insert_params)

    conn.commit()
    record_id = fetch_last_insert_id(cursor)
    conn.close()

    return record_id





def get_user_classification_history_paginated(user_id, page=1, per_page=5):
    """Get classification history for a specific user with pagination."""
    conn = get_db_connection()
    cursor = conn.cursor()
    
    # Calculate offset
    if page < 1:
        page = 1
    offset = (page - 1) * per_page
    
    # Get total count for this user
    if USE_POSTGRES:
        execute_sql(cursor, "SELECT COUNT(*) as total FROM classification_history WHERE user_id = %s", (user_id,))
    else:
        execute_sql(cursor, "SELECT COUNT(*) as total FROM classification_history WHERE user_id = ?", (user_id,))
    total = cursor.fetchone()['total']
    
    # Get paginated results for this user
    if USE_POSTGRES:
        execute_sql(cursor, """
            SELECT * FROM classification_history 
            WHERE user_id = %s
            ORDER BY created_at DESC
            LIMIT %s OFFSET %s
        """, (user_id, per_page, offset))
    else:
        execute_sql(cursor, """
            SELECT * FROM classification_history 
            WHERE user_id = ?
            ORDER BY created_at DESC
            LIMIT ? OFFSET ?
        """, (user_id, per_page, offset))
    
    records = [dict(row) for row in cursor.fetchall()]
    conn.close()
    
    # Calculate pagination info
    total_pages = (total + per_page - 1) // per_page
    has_prev = page > 1
    has_next = page < total_pages
    
    return {
        'records': records,
        'total': total,
        'page': page,
        'per_page': per_page,
        'total_pages': total_pages,
        'has_prev': has_prev,
        'has_next': has_next,
        'prev_num': page - 1 if has_prev else None,
        'next_num': page + 1 if has_next else None
    }


def get_user_classification_history(user_id, limit=10):
    """Get classification history for a specific user."""
    conn = get_db_connection()
    cursor = conn.cursor()

    execute_sql(cursor,
        """
        SELECT * FROM classification_history 
        WHERE user_id = ? 
        ORDER BY created_at DESC
        LIMIT ?
        """,
        (user_id, limit)
    )

    history = [dict(row) for row in cursor.fetchall()]
    conn.close()

    return history


def get_all_classification_history(limit=100):
    """Get all classification history (legacy, limited). Prefer get_classification_history_paginated."""
    conn = get_db_connection()
    cursor = conn.cursor()

    execute_sql(cursor,
        """
        SELECT ch.*, u.username, u.first_name, u.last_name
        FROM classification_history ch
        LEFT JOIN users u ON ch.user_id = u.id
        ORDER BY ch.created_at DESC
        LIMIT ?
        """,
        (limit,)
    )

    history = [dict(row) for row in cursor.fetchall()]
    conn.close()

    return history

def get_classification_history_paginated(page=1, per_page=5):
    """Get classification history with server-side pagination."""
    conn = get_db_connection()
    cursor = conn.cursor()
    
    # Calculate offset
    if page < 1:
        page = 1
    offset = (page - 1) * per_page
    
    # Get total count
    cursor.execute("SELECT COUNT(*) as total FROM classification_history")
    total = cursor.fetchone()['total']
    
    # Get paginated results
    execute_sql(cursor,
        """
        SELECT ch.*, u.username, u.first_name, u.last_name
        FROM classification_history ch
        LEFT JOIN users u ON ch.user_id = u.id
        ORDER BY ch.created_at DESC
        LIMIT ? OFFSET ?
        """,
        (per_page, offset)
    )
    
    records = [dict(row) for row in cursor.fetchall()]
    conn.close()
    
    # Calculate pagination info
    total_pages = (total + per_page - 1) // per_page
    has_prev = page > 1
    has_next = page < total_pages
    
    return {
        'records': records,
        'total': total,
        'page': page,
        'per_page': per_page,
        'total_pages': total_pages,
        'has_prev': has_prev,
        'has_next': has_next,
        'prev_num': page - 1 if has_prev else None,
        'next_num': page + 1 if has_next else None
    }


def get_system_setting(setting_name):
    """Get a system setting value."""
    conn = get_db_connection()
    cursor = conn.cursor()
    
    try:
        execute_sql(cursor, "SELECT setting_value FROM system_settings WHERE setting_name = ?", (setting_name,))
        row = cursor.fetchone()
        return row['setting_value'] if row else None
    except Exception as e:
        print(f"Error getting system setting {setting_name}: {e}")
        return None
    finally:
        conn.close()


def update_system_setting(setting_name, setting_value, updated_by=None):
    """Update a system setting."""
    conn = get_db_connection()
    cursor = conn.cursor()
    
    try:
        from timezone_utils import get_philippines_time_for_db
        
        if USE_POSTGRES:
            # PostgreSQL uses ON CONFLICT
            execute_sql(cursor, """
                INSERT INTO system_settings 
                (setting_name, setting_value, updated_at, updated_by) 
                VALUES (?, ?, ?, ?)
                ON CONFLICT (setting_name) DO UPDATE SET 
                setting_value = EXCLUDED.setting_value, 
                updated_at = EXCLUDED.updated_at,
                updated_by = EXCLUDED.updated_by
            """, (setting_name, setting_value, get_philippines_time_for_db(), updated_by))
        else:
            # SQLite uses INSERT OR REPLACE
            execute_sql(cursor, """
                INSERT OR REPLACE INTO system_settings 
                (setting_name, setting_value, updated_at, updated_by) 
                VALUES (?, ?, ?, ?)
            """, (setting_name, setting_value, get_philippines_time_for_db(), updated_by))
        
        conn.commit()
        return True
        
    except Exception as e:
        conn.rollback()
        conn.close()
        print(f"Error updating system setting {setting_name}: {str(e)}")
        return False


def get_all_users(limit=100):
    """Get all users (legacy, limited). Prefer get_users_paginated."""
    conn = get_db_connection()
    cursor = conn.cursor()
    execute_sql(cursor,
        """
        SELECT id, username, email, first_name, last_name, gender, 
               date_of_birth, medical_id, is_admin, created_at, last_login
        FROM users
        ORDER BY created_at DESC
        LIMIT ?
        """,
        (limit,)
    )
    users = [dict(row) for row in cursor.fetchall()]
    conn.close()
    return users


def get_users_paginated(page=1, per_page=5):
    """Get users with server-side pagination and counts for UI stats."""
    conn = get_db_connection()
    cursor = conn.cursor()

    # Counts for header cards
    cursor.execute("SELECT COUNT(*) AS total FROM users")
    total = cursor.fetchone()['total']

    cursor.execute("SELECT COUNT(*) AS admins FROM users WHERE is_admin = 1")
    admins = cursor.fetchone()['admins']

    cursor.execute("SELECT COUNT(*) AS regulars FROM users WHERE is_admin = 0")
    regulars = cursor.fetchone()['regulars']

    cursor.execute("SELECT COUNT(*) AS active FROM users WHERE last_login IS NOT NULL")
    active = cursor.fetchone()['active']

    # Pagination window
    if page < 1:
        page = 1
    offset = (page - 1) * per_page

    execute_sql(cursor,
        """
        SELECT id, username, email, first_name, last_name, gender,
               date_of_birth, medical_id, is_admin, created_at, last_login
        FROM users
        ORDER BY created_at DESC
        LIMIT ? OFFSET ?
        """,
        (per_page, offset)
    )
    records = [dict(row) for row in cursor.fetchall()]

    conn.close()

    total_pages = (total + per_page - 1) // per_page
    has_prev = page > 1
    has_next = page < total_pages

    return {
        'records': records,
        'total': total,
        'page': page,
        'per_page': per_page,
        'total_pages': total_pages,
        'has_prev': has_prev,
        'has_next': has_next,
        'prev_num': page - 1 if has_prev else None,
        'next_num': page + 1 if has_next else None,
        # header stats
        'counts': {
            'total': total,
            'regulars': regulars,
            'admins': admins,
            'active': active
        }
    }


def update_medical_data(user_id, **kwargs):
    """Update user medical data."""
    conn = get_db_connection()
    cursor = conn.cursor()
    
    # Build the SET part of the SQL query
    set_clauses = []
    values = []
    
    for key, value in kwargs.items():
        if key in ['height', 'weight', 'blood_type', 'medical_conditions', 'medications']:
            set_clauses.append(f'{key} = ?')
            values.append(value)
    
    set_clauses.append('updated_at = ?')
    from timezone_utils import get_philippines_time_for_db
    values.append(get_philippines_time_for_db())
    
    if not set_clauses:
        conn.close()
        return False, "No valid fields to update."
    
    # Check if the record exists
    execute_sql(cursor, "SELECT id FROM medical_data WHERE user_id = ?", (user_id,))
    if cursor.fetchone():
        # Update existing record
        sql = f"UPDATE medical_data SET {', '.join(set_clauses)} WHERE user_id = ?"
        values.append(user_id)
        execute_sql(cursor, sql, tuple(values))
    else:
        # Insert new record
        keys = [key for key, _ in kwargs.items() if key in ['height', 'weight', 'blood_type', 'medical_conditions', 'medications']]
        keys.append('user_id')
        values.append(user_id)

        sql = f"INSERT INTO medical_data ({', '.join(keys)}) VALUES ({', '.join(['?'] * len(keys))})"
        execute_sql(cursor, sql, tuple(values))
    
    conn.commit()
    conn.close()
    
    return True, "Medical data updated successfully."


def get_medical_data(user_id):
    """Get user medical data."""
    conn = get_db_connection()
    cursor = conn.cursor()
    
    execute_sql(cursor, "SELECT * FROM medical_data WHERE user_id = ?", (user_id,))
    data = cursor.fetchone()
    
    conn.close()
    return dict(data) if data else None


def get_statistics():
    """Get system statistics for admin dashboard."""
    conn = get_db_connection()
    cursor = conn.cursor()
    
    # Get user count
    cursor.execute("SELECT COUNT(*) as user_count FROM users WHERE is_admin = 0")
    user_count = cursor.fetchone()['user_count']
    
    # Get total classifications
    cursor.execute("SELECT COUNT(*) as classification_count FROM classification_history")
    classification_count = cursor.fetchone()['classification_count']

    # Get class distribution (raw)
    cursor.execute("""
        SELECT predicted_class, COUNT(*) as count
        FROM classification_history
        GROUP BY predicted_class
    """)
    raw_distribution = { (row['predicted_class'] or '').strip(): row['count'] for row in cursor.fetchall() }

    # Normalize labels to canonical forms and aggregate anemic vs normal
    # Common variants in code: 'Normal', 'Mild', 'Moderate', 'Severe',
    # sometimes routes append ' Anemia' (e.g., 'Mild Anemia').
    class_distribution = {}
    anemic_count = 0
    normal_count = 0
    for label, cnt in raw_distribution.items():
        if not label:
            continue
        l = label.lower()
        if 'normal' in l:
            canonical = 'Normal'
            normal_count += cnt
        elif 'mild' in l:
            canonical = 'Mild'
            anemic_count += cnt
        elif 'moderate' in l:
            canonical = 'Moderate'
            anemic_count += cnt
        elif 'severe' in l:
            canonical = 'Severe'
            anemic_count += cnt
        elif 'anemia' in l:
            # fallback: if label mentions anemia but not severity, treat as anemic
            canonical = 'Anemia'
            anemic_count += cnt
        else:
            canonical = label
        class_distribution[canonical] = class_distribution.get(canonical, 0) + cnt
    
    # Get new users in the last 7 days
    if USE_POSTGRES:
        execute_sql(cursor, """
            SELECT COUNT(*) as new_user_count 
            FROM users 
            WHERE created_at > NOW() - INTERVAL '7 days' AND is_admin = 0
        """)
    else:
        execute_sql(cursor, """
            SELECT COUNT(*) as new_user_count 
            FROM users 
            WHERE created_at > datetime('now', '-7 days') AND is_admin = 0
        """)
    new_user_count = cursor.fetchone()['new_user_count']
    
    # Get active users in the last 7 days
    if USE_POSTGRES:
        execute_sql(cursor, """
            SELECT COUNT(DISTINCT user_id) as active_user_count 
            FROM classification_history 
            WHERE created_at > NOW() - INTERVAL '7 days'
        """)
    else:
        execute_sql(cursor, """
            SELECT COUNT(DISTINCT user_id) as active_user_count 
            FROM classification_history 
            WHERE created_at > datetime('now', '-7 days')
        """)
    active_user_count = cursor.fetchone()['active_user_count']
    
    conn.close()

    # Prepare stats keys expected by the admin template
    return {
        'total_users': user_count,
        'total_classifications': classification_count,
        'class_distribution': class_distribution,
        'anemic_cases': anemic_count,
        'normal_cases': normal_count,
        'new_user_count': new_user_count,
        'active_user_count': active_user_count
    }

def get_recent_classifications(page=1, per_page=5):
    """Get recent classifications with pagination."""
    conn = get_db_connection()
    cursor = conn.cursor()
    
    # Calculate offset
    offset = (page - 1) * per_page
    
    # Get total count
    execute_sql(cursor, "SELECT COUNT(*) as total FROM classification_history")
    total = cursor.fetchone()['total']
    
    # Get paginated results
    if USE_POSTGRES:
        execute_sql(cursor, """
            SELECT ch.*, u.username
            FROM classification_history ch
            LEFT JOIN users u ON ch.user_id = u.id
            ORDER BY ch.created_at DESC
            LIMIT %s OFFSET %s
        """, (per_page, offset))
    else:
        execute_sql(cursor, """
            SELECT ch.*, u.username
            FROM classification_history ch
            LEFT JOIN users u ON ch.user_id = u.id
            ORDER BY ch.created_at DESC
            LIMIT ? OFFSET ?
        """, (per_page, offset))
    
    records = [dict(row) for row in cursor.fetchall()]
    conn.close()
    
    # Calculate pagination info
    total_pages = (total + per_page - 1) // per_page
    has_prev = page > 1
    has_next = page < total_pages
    
    return {
        'records': records,
        'total': total,
        'page': page,
        'per_page': per_page,
        'total_pages': total_pages,
        'has_prev': has_prev,
        'has_next': has_next,
        'prev_num': page - 1 if has_prev else None,
        'next_num': page + 1 if has_next else None
    }

def delete_user(user_id):
    """Delete a user and all associated data."""
    conn = get_db_connection()
    cursor = conn.cursor()
    
    try:
        # Check if user exists
        execute_sql(cursor, "SELECT id, username, is_admin FROM users WHERE id = ?", (user_id,))
        user = cursor.fetchone()
        
        if not user:
            conn.close()
            return False, "User not found"
        
        # Prevent deletion of admin users
        if user['is_admin']:
            conn.close()
            return False, "Cannot delete admin users"
        
        # Delete user's classification history
        execute_sql(cursor, "DELETE FROM classification_history WHERE user_id = ?", (user_id,))
        
        # Delete user's medical data
        execute_sql(cursor, "DELETE FROM medical_data WHERE user_id = ?", (user_id,))
        
        # Delete user's chat conversations and messages
        execute_sql(cursor, "SELECT id FROM chat_conversations WHERE user_id = ?", (user_id,))
        conversations = cursor.fetchall()
        
        for conv in conversations:
            execute_sql(cursor, "DELETE FROM chat_messages WHERE conversation_id = ?", (conv['id'],))
        
        execute_sql(cursor, "DELETE FROM chat_conversations WHERE user_id = ?", (user_id,))
        
        # Finally, delete the user
        execute_sql(cursor, "DELETE FROM users WHERE id = ?", (user_id,))
        
        conn.commit()
        conn.close()
        
        return True, f"User '{user['username']}' deleted successfully"
        
    except Exception as e:
        conn.rollback()
        conn.close()
        return False, f"Error deleting user: {str(e)}"

def get_classification_record(record_id):
    """Get a specific classification record by ID."""
    conn = get_db_connection()
    cursor = conn.cursor()
    
    try:
        execute_sql(cursor, """
            SELECT * FROM classification_history 
            WHERE id = ?
        """, (record_id,))
        
        record = cursor.fetchone()
        conn.close()
        
        if record:
            return dict(record)
        return None
        
    except Exception as e:
        conn.close()
        return None


def get_user_by_id(user_id):
    """Get user data by ID."""
    conn = get_db_connection()
    cursor = conn.cursor()
    
    try:
        execute_sql(cursor, """
            SELECT * FROM users 
            WHERE id = ?
        """, (user_id,))
        
        user = cursor.fetchone()
        conn.close()
        
        if user:
            return dict(user)
        return None
        
    except Exception as e:
        conn.close()
        return None


def get_user_by_email(email: str):
    """Get user by email, or None if not found."""
    conn = get_db_connection()
    cursor = conn.cursor()
    execute_sql(cursor, "SELECT * FROM users WHERE email = ?", (email,))
    row = cursor.fetchone()
    conn.close()
    return dict(row) if row else None


def get_user_by_medical_id(medical_id: str):
    """Get user by medical ID, or None if not found."""
    if medical_id is None:
        return None
    mid = str(medical_id).strip()
    if not mid:
        return None
    conn = get_db_connection()
    cursor = conn.cursor()
    execute_sql(cursor, "SELECT * FROM users WHERE medical_id = ?", (mid,))
    row = cursor.fetchone()
    conn.close()
    return dict(row) if row else None


def get_user_by_username(username: str):
    """Get user by username, or None if not found."""
    if username is None:
        return None
    uname = str(username).strip()
    if not uname:
        return None
    conn = get_db_connection()
    cursor = conn.cursor()
    execute_sql(cursor, "SELECT * FROM users WHERE username = ?", (uname,))
    row = cursor.fetchone()
    conn.close()
    return dict(row) if row else None

def get_admin_dashboard_charts():
    """Get data for admin dashboard charts: age groups, gender distribution, and severity classification."""
    conn = get_db_connection()
    cursor = conn.cursor()

    age_years = sql_age_years_from_dob('date_of_birth')

    # Age groups distribution
    execute_sql(cursor, f"""
        SELECT 
            CASE 
                WHEN ({age_years}) < 18 THEN 'Under 18'
                WHEN ({age_years}) BETWEEN 18 AND 30 THEN '18-30'
                WHEN ({age_years}) BETWEEN 31 AND 45 THEN '31-45'
                WHEN ({age_years}) BETWEEN 46 AND 60 THEN '46-60'
                ELSE 'Over 60'
            END as age_group,
            COUNT(*) as count
        FROM users 
        WHERE date_of_birth IS NOT NULL AND is_admin = 0
        GROUP BY age_group
        ORDER BY 
            CASE age_group
                WHEN 'Under 18' THEN 1
                WHEN '18-30' THEN 2
                WHEN '31-45' THEN 3
                WHEN '46-60' THEN 4
                WHEN 'Over 60' THEN 5
            END
    """)
    age_groups = {row['age_group']: row['count'] for row in cursor.fetchall()}

    # Gender distribution
    execute_sql(cursor, """
        SELECT gender, COUNT(*) as count
        FROM users 
        WHERE gender IS NOT NULL AND is_admin = 0
        GROUP BY gender
    """)
    gender_stats = {row['gender']: row['count'] for row in cursor.fetchall()}

    # Severity classification distribution
    execute_sql(cursor, """
        SELECT 
            CASE 
                WHEN predicted_class LIKE '%Normal%' OR predicted_class LIKE '%normal%' THEN 'Normal'
                WHEN predicted_class LIKE '%Mild%' OR predicted_class LIKE '%mild%' THEN 'Mild Anemia'
                WHEN predicted_class LIKE '%Moderate%' OR predicted_class LIKE '%moderate%' THEN 'Moderate Anemia'
                WHEN predicted_class LIKE '%Severe%' OR predicted_class LIKE '%severe%' THEN 'Severe Anemia'
                ELSE 'Other'
            END as severity,
            COUNT(*) as count
        FROM classification_history
        WHERE predicted_class IS NOT NULL
        GROUP BY severity
        ORDER BY 
            CASE severity
                WHEN 'Normal' THEN 1
                WHEN 'Mild Anemia' THEN 2
                WHEN 'Moderate Anemia' THEN 3
                WHEN 'Severe Anemia' THEN 4
                WHEN 'Other' THEN 5
            END
    """)
    severity_stats = {row['severity']: row['count'] for row in cursor.fetchall()}

    conn.close()

    return {
        'age_groups': age_groups,
        'gender_stats': gender_stats,
        'severity_stats': severity_stats
    }


def create_imported_file(filename, original_filename, total_records, imported_by):
    """Create a new imported file record."""
    conn = get_db_connection()
    cursor = conn.cursor()
    
    # Use Philippines time for timestamp
    from timezone_utils import get_philippines_time_for_db
    ph_timestamp = get_philippines_time_for_db()
    
    if USE_POSTGRES:
        execute_sql(cursor, '''
            INSERT INTO imported_files (filename, original_filename, total_records, imported_by, imported_at)
            VALUES (?, ?, ?, ?, ?)
            RETURNING id
        ''', (filename, original_filename, total_records, imported_by, ph_timestamp))
    else:
        execute_sql(cursor, '''
            INSERT INTO imported_files (filename, original_filename, total_records, imported_by, imported_at)
            VALUES (?, ?, ?, ?, ?)
        ''', (filename, original_filename, total_records, imported_by, ph_timestamp))

    file_id = fetch_last_insert_id(cursor)
    conn.commit()
    conn.close()
    
    return file_id


def get_imported_files():
    """Get all imported files with their status."""
    conn = get_db_connection()
    cursor = conn.cursor()
    
    # Check if imported_files table exists
    if not table_exists(cursor, 'imported_files'):
        print("imported_files table does not exist")
        conn.close()
        return []

    execute_sql(cursor, '''
        SELECT 
            f.id,
            f.original_filename as filename,
            f.imported_at,
            f.total_records,
            f.is_applied,
            u.username as imported_by
        FROM imported_files f
        LEFT JOIN users u ON f.imported_by = u.id
        ORDER BY f.imported_at DESC
    ''')
    
    files = []
    for row in cursor.fetchall():
        # Convert timestamp to Philippines time
        imported_at_ph = convert_to_philippines_time(row['imported_at'])
        files.append({
            'id': row['id'],
            'filename': row['filename'],
            'imported_at': imported_at_ph,
            'total_records': row['total_records'],
            'is_applied': bool(row['is_applied']),
            'imported_by': row['imported_by']
        })
    
    print(f"Found {len(files)} imported files")
    conn.close()
    return files


def update_file_status(file_id, is_applied):
    """Update the applied status of an imported file."""
    conn = get_db_connection()
    cursor = conn.cursor()
    
    execute_sql(cursor, '''
        UPDATE imported_files 
        SET is_applied = ?
        WHERE id = ?
    ''', (1 if is_applied else 0, file_id))
    
    conn.commit()
    conn.close()


def delete_imported_file(file_id):
    """Delete an imported file and all its data."""
    conn = get_db_connection()
    cursor = conn.cursor()
    
    # Delete all data associated with this file
    execute_sql(cursor, 'DELETE FROM classification_import_data WHERE file_id = ?', (file_id,))
    
    # Delete the file record
    execute_sql(cursor, 'DELETE FROM imported_files WHERE id = ?', (file_id,))
    
    conn.commit()
    conn.close()


def get_applied_imported_data():
    """Get all applied imported data for chart calculations."""
    conn = get_db_connection()
    cursor = conn.cursor()
    
    has_file_id = column_exists(cursor, 'classification_import_data', 'file_id')

    if has_file_id:
        # Age groups from applied imported data
        execute_sql(cursor, '''
            SELECT 
                CASE 
                    WHEN age < 18 THEN 'Under 18'
                    WHEN age BETWEEN 18 AND 30 THEN '18-30'
                    WHEN age BETWEEN 31 AND 45 THEN '31-45'
                    WHEN age BETWEEN 46 AND 60 THEN '46-60'
                    ELSE 'Over 60'
                END as age_group,
                COUNT(*) as count
            FROM classification_import_data cid
            JOIN imported_files f ON cid.file_id = f.id
            WHERE f.is_applied = 1
            GROUP BY age_group
        ''')
        age_groups = {row['age_group']: row['count'] for row in cursor.fetchall()}
        
        # Gender stats from applied imported data
        execute_sql(cursor, '''
            SELECT gender, COUNT(*) as count
            FROM classification_import_data cid
            JOIN imported_files f ON cid.file_id = f.id
            WHERE f.is_applied = 1
            GROUP BY gender
        ''')
        gender_stats = {row['gender']: row['count'] for row in cursor.fetchall()}

        # Severity stats from applied imported data
        execute_sql(cursor, '''
            SELECT category, COUNT(*) as count
            FROM classification_import_data cid
            JOIN imported_files f ON cid.file_id = f.id
            WHERE f.is_applied = 1
            GROUP BY category
        ''')
        severity_stats = {row['category']: row['count'] for row in cursor.fetchall()}
    else:
        # Fallback to old format - get all imported data
        execute_sql(cursor, '''
            SELECT 
                CASE 
                    WHEN age < 18 THEN 'Under 18'
                    WHEN age BETWEEN 18 AND 30 THEN '18-30'
                    WHEN age BETWEEN 31 AND 45 THEN '31-45'
                    WHEN age BETWEEN 46 AND 60 THEN '46-60'
                    ELSE 'Over 60'
                END as age_group,
                COUNT(*) as count
            FROM classification_import_data
            GROUP BY age_group
        ''')
        age_groups = {row['age_group']: row['count'] for row in cursor.fetchall()}
        
        execute_sql(cursor, '''
            SELECT gender, COUNT(*) as count
            FROM classification_import_data
            GROUP BY gender
        ''')
        gender_stats = {row['gender']: row['count'] for row in cursor.fetchall()}

        execute_sql(cursor, '''
            SELECT category, COUNT(*) as count
            FROM classification_import_data
            GROUP BY category
        ''')
        severity_stats = {row['category']: row['count'] for row in cursor.fetchall()}
    
    conn.close()
    
    return {
        'age_groups': age_groups,
        'gender_stats': gender_stats,
        'severity_stats': severity_stats
    }


def migrate_database():
    """Migrate database to add new tables if they don't exist."""
    conn = get_db_connection()
    cursor = conn.cursor()
    
    # Check if imported_files table exists
    if USE_POSTGRES:
        cursor.execute("""
            SELECT table_name FROM information_schema.tables 
            WHERE table_schema = 'public' AND table_name = 'imported_files'
        """)
    else:
        cursor.execute("""
            SELECT name FROM sqlite_master 
            WHERE type='table' AND name='imported_files'
        """)
    
    if not cursor.fetchone():
        # Create imported_files table with proper PostgreSQL syntax
        if USE_POSTGRES:
            cursor.execute('''
                CREATE TABLE imported_files (
                    id SERIAL PRIMARY KEY,
                    filename VARCHAR(255) NOT NULL,
                    original_filename VARCHAR(255) NOT NULL,
                    total_records INTEGER NOT NULL,
                    imported_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    is_applied INTEGER DEFAULT 1,
                    imported_by INTEGER,
                    FOREIGN KEY (imported_by) REFERENCES users(id)
                )
            ''')
        else:
            cursor.execute('''
                CREATE TABLE imported_files (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    filename TEXT NOT NULL,
                    original_filename TEXT NOT NULL,
                    total_records INTEGER NOT NULL,
                    imported_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    is_applied INTEGER DEFAULT 1,
                    imported_by INTEGER,
                    FOREIGN KEY (imported_by) REFERENCES users(id)
                )
            ''')
        
        # Add file_id column to classification_import_data if it doesn't exist
        try:
            cursor.execute('ALTER TABLE classification_import_data ADD COLUMN file_id INTEGER')
        except:
            # Column might already exist, ignore error
            pass
        
        conn.commit()
        print("Database migrated successfully - added imported_files table")
    
    # Check if otp_verification table exists
    if USE_POSTGRES:
        cursor.execute("""
            SELECT table_name FROM information_schema.tables 
            WHERE table_schema = 'public' AND table_name = 'otp_verification'
        """)
    else:
        cursor.execute("""
            SELECT name FROM sqlite_master 
            WHERE type='table' AND name='otp_verification'
        """)
    
    if not cursor.fetchone():
        # Create otp_verification table with proper PostgreSQL syntax
        if USE_POSTGRES:
            cursor.execute('''
                CREATE TABLE otp_verification (
                    id SERIAL PRIMARY KEY,
                    email VARCHAR(255) NOT NULL UNIQUE,
                    otp_code VARCHAR(10) NOT NULL,
                    username VARCHAR(255) NOT NULL,
                    password_hash TEXT NOT NULL,
                    first_name VARCHAR(255) NOT NULL,
                    last_name VARCHAR(255) NOT NULL,
                    gender VARCHAR(50) NOT NULL,
                    date_of_birth DATE NOT NULL,
                    medical_id VARCHAR(255) NOT NULL,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    expires_at TIMESTAMP NOT NULL,
                    is_verified INTEGER DEFAULT 0
                )
            ''')
        else:
            cursor.execute('''
                CREATE TABLE otp_verification (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    email TEXT NOT NULL UNIQUE,
                    otp_code TEXT NOT NULL,
                    username TEXT NOT NULL,
                    password_hash TEXT NOT NULL,
                    first_name TEXT NOT NULL,
                    last_name TEXT NOT NULL,
                    gender TEXT NOT NULL,
                    date_of_birth DATE NOT NULL,
                    medical_id TEXT NOT NULL,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    expires_at TIMESTAMP NOT NULL,
                    is_verified INTEGER DEFAULT 0
                )
            ''')
        
        conn.commit()
        print("Database migrated successfully - added otp_verification table")
    
    # Check if password_reset_otp table exists
    if USE_POSTGRES:
        cursor.execute("""
            SELECT table_name FROM information_schema.tables 
            WHERE table_schema = 'public' AND table_name = 'password_reset_otp'
        """)
    else:
        cursor.execute("""
            SELECT name FROM sqlite_master 
            WHERE type='table' AND name='password_reset_otp'
        """)
    
    if not cursor.fetchone():
        # Create password_reset_otp table with proper PostgreSQL syntax
        if USE_POSTGRES:
            cursor.execute('''
                CREATE TABLE password_reset_otp (
                    id SERIAL PRIMARY KEY,
                    email VARCHAR(255) NOT NULL,
                    otp_code VARCHAR(10) NOT NULL,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    expires_at TIMESTAMP NOT NULL,
                    is_verified INTEGER DEFAULT 0
                )
            ''')
        else:
            cursor.execute('''
                CREATE TABLE password_reset_otp (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    email TEXT NOT NULL,
                    otp_code TEXT NOT NULL,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    expires_at TIMESTAMP NOT NULL,
                    is_verified INTEGER DEFAULT 0
                )
            ''')
        
        conn.commit()
        print("Database migrated successfully - added password_reset_otp table")
    
    conn.close()


def store_otp_verification(email, otp_code, username, password_hash, first_name, last_name, gender, date_of_birth, medical_id, expires_at):
    """Store OTP verification data."""
    conn = get_db_connection()
    cursor = conn.cursor()
    
    try:
        print(f"Storing OTP: email={email}, code={otp_code}, expires={expires_at}")
        
        # Delete any existing OTP for this email
        execute_sql(cursor, 'DELETE FROM otp_verification WHERE email = ?', (email,))
        print(f"Deleted existing OTP records for {email}")
        
        # Insert new OTP data
        execute_sql(cursor, '''
            INSERT INTO otp_verification 
            (email, otp_code, username, password_hash, first_name, last_name, gender, date_of_birth, medical_id, expires_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ''', (email, otp_code, username, password_hash, first_name, last_name, gender, date_of_birth, medical_id, expires_at))
        
        conn.commit()
        print(f"OTP stored successfully for {email}")
        
        # Verify it was stored
        execute_sql(cursor, 'SELECT * FROM otp_verification WHERE email = ?', (email,))
        stored = cursor.fetchone()
        if stored:
            print(f"Verification: OTP stored with code {stored['otp_code']}, expires {stored['expires_at']}")
        else:
            print("ERROR: OTP was not stored properly")
        
        return True
    except Exception as e:
        print(f"Error storing OTP verification: {e}")
        return False
    finally:
        conn.close()


def verify_otp_code(email, otp_code):
    """Verify OTP code and return user data if valid."""
    conn = get_db_connection()
    cursor = conn.cursor()
    
    try:
        print(f"Verifying OTP: email={email}, code={otp_code}")
        
        # First, check if there's any OTP record for this email
        execute_sql(cursor, '''
            SELECT * FROM otp_verification 
            WHERE email = ?
        ''', (email,))
        
        all_records = cursor.fetchall()
        print(f"Found {len(all_records)} OTP records for email {email}")
        
        for record in all_records:
            print(f"Record: email={record['email']}, code={record['otp_code']}, expires={record['expires_at']}")
        
        # Now check for valid OTP
        execute_sql(cursor, f'''
            SELECT * FROM otp_verification 
            WHERE email = ? AND otp_code = ? AND expires_at > {sql_now()}
        ''', (email, otp_code))
        
        result = cursor.fetchone()
        print(f"Valid OTP found: {result is not None}")
        
        if result:
            print(f"OTP is valid, creating user account")
            # Mark as verified
            execute_sql(cursor, '''
                UPDATE otp_verification 
                SET is_verified = 1 
                WHERE email = ? AND otp_code = ?
            ''', (email, otp_code))
            conn.commit()
            
            return {
                'username': result['username'],
                'password_hash': result['password_hash'],
                'first_name': result['first_name'],
                'last_name': result['last_name'],
                'gender': result['gender'],
                'date_of_birth': result['date_of_birth'],
                'medical_id': result['medical_id']
            }
        else:
            print(f"OTP verification failed - no valid record found")
        return None
    except Exception as e:
        print(f"Error verifying OTP: {e}")
        return None
    finally:
        conn.close()


def cleanup_expired_otp():
    """Clean up expired OTP records."""
    conn = get_db_connection()
    cursor = conn.cursor()
    
    try:
        execute_sql(cursor, f'DELETE FROM otp_verification WHERE expires_at < {sql_now()}')
        conn.commit()
    except Exception as e:
        print(f"Error cleaning up expired OTP: {e}")
    finally:
        conn.close()


def update_otp_code(email, otp_code, expires_at):
    """Update OTP code for existing verification record."""
    conn = get_db_connection()
    cursor = conn.cursor()
    
    try:
        print(f"Updating OTP: email={email}, code={otp_code}, expires={expires_at}")
        
        # Update the OTP code and expiry time
        execute_sql(cursor, '''
            UPDATE otp_verification 
            SET otp_code = ?, expires_at = ?, is_verified = 0
            WHERE email = ?
        ''', (otp_code, expires_at, email))
        
        if cursor.rowcount > 0:
            conn.commit()
            print(f"OTP updated successfully for {email}")
            return True
        else:
            print(f"No existing OTP record found for {email}")
            return False
            
    except Exception as e:
        print(f"Error updating OTP code: {e}")
        return False
    finally:
        conn.close()


def store_password_reset_otp(email, otp_code, expires_at):
    """Store password reset OTP data."""
    conn = get_db_connection()
    cursor = conn.cursor()
    
    try:
        # Delete any existing password reset OTP for this email
        execute_sql(cursor, 'DELETE FROM password_reset_otp WHERE email = ?', (email,))
        
        # Insert new password reset OTP data
        execute_sql(cursor, '''
            INSERT INTO password_reset_otp (email, otp_code, expires_at)
            VALUES (?, ?, ?)
        ''', (email, otp_code, expires_at))
        
        conn.commit()
        return True
    except Exception as e:
        print(f"Error storing password reset OTP: {e}")
        return False
    finally:
        conn.close()


def verify_password_reset_otp(email, otp_code):
    """Verify password reset OTP code."""
    conn = get_db_connection()
    cursor = conn.cursor()
    
    try:
        execute_sql(cursor, f'''
            SELECT * FROM password_reset_otp 
            WHERE email = ? AND otp_code = ? AND expires_at > {sql_now()}
        ''', (email, otp_code))
        
        result = cursor.fetchone()
        if result:
            # Mark as verified
            execute_sql(cursor, '''
                UPDATE password_reset_otp 
                SET is_verified = 1 
                WHERE email = ? AND otp_code = ?
            ''', (email, otp_code))
            conn.commit()
            return True
        return False
    except Exception as e:
        print(f"Error verifying password reset OTP: {e}")
        return False
    finally:
        conn.close()


def cleanup_password_reset_otp():
    """Clean up expired password reset OTP records."""
    conn = get_db_connection()
    cursor = conn.cursor()
    
    try:
        execute_sql(cursor, f'''
            DELETE FROM password_reset_otp 
            WHERE expires_at < {sql_now()}
        ''')
        conn.commit()
        return True
    except Exception as e:
        print(f"Error cleaning up expired password reset OTP: {e}")
        return False
    finally:
        conn.close()


def update_user_password_by_email(email, password_hash):
    """Update user password by email address."""
    conn = get_db_connection()
    cursor = conn.cursor()
    
    try:
        cursor.execute('''
            UPDATE users 
            SET password_hash = ? 
            WHERE email = ?
        ''', (password_hash, email))
        
        conn.commit()
        return cursor.rowcount > 0
    except Exception as e:
        print(f"Error updating user password: {e}")
        return False
    finally:
        conn.close()


# Initialize the database when this module is imported
if USE_POSTGRES:
    # For PostgreSQL, always run init_db to ensure tables exist
    try:
        print("Initializing PostgreSQL database...")
        init_db()
        print("PostgreSQL database initialized successfully")
    except Exception as e:
        print(f"CRITICAL ERROR initializing PostgreSQL database: {e}")
        print("This will cause the application to fail!")
        # Don't continue - this is critical
        raise e
else:
    # For SQLite, check if database file exists
    if not os.path.exists(DB_PATH):
        init_db()
    else:
        # Run migration for existing databases
        try:
            migrate_database()
        except Exception as e:
            print(f"Error during migration: {e}")
            # If migration fails, try init_db
            try:
                init_db()
            except Exception as e2:
                print(f"Error initializing database: {e2}")


def get_other_person_classifications(limit=100):
    """Return recent classifications that were submitted for another person (legacy detection via notes)."""
    conn = get_db_connection()
    cursor = conn.cursor()
    execute_sql(cursor,
        """
        SELECT ch.*, u.username, u.first_name, u.last_name
        FROM classification_history ch
        LEFT JOIN users u ON ch.user_id = u.id
        WHERE ch.notes LIKE 'Patient:%'
        ORDER BY ch.created_at DESC
        LIMIT ?
        """,
        (limit,)
    )
    rows = [dict(row) for row in cursor.fetchall()]
    conn.close()
    return rows

def get_other_person_classifications_paginated(page=1, per_page=5):
    """Get 'another person' classifications with pagination."""
    conn = get_db_connection()
    cursor = conn.cursor()
    
    # Calculate offset
    if page < 1:
        page = 1
    offset = (page - 1) * per_page
    
    # Total count
    cursor.execute("SELECT COUNT(*) as total FROM classification_history WHERE notes LIKE 'Patient:%'")
    total = cursor.fetchone()['total']
    
    # Page data with username
    execute_sql(cursor,
        """
        SELECT ch.*, u.username
        FROM classification_history ch
        LEFT JOIN users u ON ch.user_id = u.id
        WHERE ch.notes LIKE 'Patient:%'
        ORDER BY ch.created_at DESC
        LIMIT ? OFFSET ?
        """,
        (per_page, offset)
    )
    records = [dict(row) for row in cursor.fetchall()]
    conn.close()
    
    total_pages = (total + per_page - 1) // per_page
    has_prev = page > 1
    has_next = page < total_pages
    
    return {
        'records': records,
        'total': total,
        'page': page,
        'per_page': per_page,
        'total_pages': total_pages,
        'has_prev': has_prev,
        'has_next': has_next,
        'prev_num': page - 1 if has_prev else None,
        'next_num': page + 1 if has_next else None
    }

def _ensure_chat_columns(cursor):
    """Ensure chat tables have columns expected by simple_chat (safe on existing DBs)."""
    columns_to_add = [
        ('chat_conversations', 'admin_id', 'INTEGER'),
        ('chat_messages', 'sender_id', 'INTEGER'),
        ('chat_messages', 'message_text', 'TEXT'),
    ]
    for table, column, col_type in columns_to_add:
        try:
            cursor.execute(f"SELECT {column} FROM {table} LIMIT 1")
        except Exception:
            try:
                cursor.execute(f"ALTER TABLE {table} ADD COLUMN {column} {col_type}")
            except Exception:
                pass


def _ensure_patient_columns(cursor):
    """Ensure patient_name, patient_age, patient_gender columns exist on classification_history (SQLite-safe)."""
    try:
        cursor.execute("SELECT patient_name FROM classification_history LIMIT 1")
    except Exception:
        try:
            cursor.execute("ALTER TABLE classification_history ADD COLUMN patient_name TEXT")
        except Exception:
            pass
    try:
        cursor.execute("SELECT patient_age FROM classification_history LIMIT 1")
    except Exception:
        try:
            cursor.execute("ALTER TABLE classification_history ADD COLUMN patient_age INTEGER")
        except Exception:
            pass
    try:
        cursor.execute("SELECT patient_gender FROM classification_history LIMIT 1")
    except Exception:
        try:
            cursor.execute("ALTER TABLE classification_history ADD COLUMN patient_gender TEXT")
        except Exception:
            pass

def ensure_patient_columns():
    """Ensure patient_name, patient_age, patient_gender columns exist on classification_history.
    Safe to call repeatedly at startup on Railway or local.
    """
    conn = get_db_connection()
    cursor = conn.cursor()
    try:
        _ensure_patient_columns(cursor)
        conn.commit()
    finally:
        conn.close()


