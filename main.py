import os
import json
import numpy as np
import cv2
import oracledb
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Form, UploadFile, File
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from contextlib import asynccontextmanager
from pathlib import Path
import face_recognition

# ─────────────────────────────────────────────
# ENV + ORACLE INIT
# ─────────────────────────────────────────────
load_dotenv()

# Thick mode — Oracle Instant Client required
LIB_DIR = os.getenv("ORACLE_LIB_DIR", "/usr/lib/oracle/19.23/client64/lib")
oracledb.init_oracle_client(lib_dir=LIB_DIR)

db_config = {
    "user":     os.getenv("DB_USER"),
    "password": os.getenv("DB_PASSWORD"),
    "dsn":      os.getenv("DB_DSN"),
}

PHOTOS_DIR = Path("./student_photos")
PHOTOS_DIR.mkdir(exist_ok=True)

# ─────────────────────────────────────────────
# IN-MEMORY CACHE
# ─────────────────────────────────────────────
KNOWN_FACE_ENCODINGS: list = []
KNOWN_PERSON_IDS:     list = []
KNOWN_PERSON_TYPES:   list = []


# ─────────────────────────────────────────────
# DB HELPER
# ─────────────────────────────────────────────
def get_db_connection():
    try:
        return oracledb.connect(**db_config)
    except Exception as e:
        print(f"[DB ERROR] {e}")
        raise HTTPException(status_code=500, detail="Database unreachable")


# ─────────────────────────────────────────────
# CACHE BUILDER  (called at startup + /reload_cache)
# ─────────────────────────────────────────────
def build_cache():
    global KNOWN_FACE_ENCODINGS, KNOWN_PERSON_IDS, KNOWN_PERSON_TYPES

    connection = get_db_connection()
    cursor     = connection.cursor()
    cursor.execute(
        "SELECT person_id, person_type, descriptor "
        "FROM FACE_DESCRIPTORS1 "
        "WHERE descriptor IS NOT NULL"
    )
    records = cursor.fetchall()
    cursor.close()
    connection.close()

    encodings, ids, types = [], [], []
    for row in records:
        p_id, p_type, embedding_data = row
        if embedding_data:
            if hasattr(embedding_data, "read"):   # CLOB safety guard
                embedding_data = embedding_data.read()
            encodings.append(np.array(json.loads(embedding_data)))
            ids.append(p_id)
            types.append(p_type)

    KNOWN_FACE_ENCODINGS = encodings
    KNOWN_PERSON_IDS     = ids
    KNOWN_PERSON_TYPES   = types
    return len(encodings)


# ─────────────────────────────────────────────
# LIFESPAN
# ─────────────────────────────────────────────
@asynccontextmanager
async def lifespan(app: FastAPI):
    print("🧠 Building biometric cache from Oracle...")
    try:
        count = build_cache()
        print(f"✅ Cache ready — {count} vectors loaded.")
    except Exception as e:
        print(f"❌ Cache build failed: {e}")
    yield
    print("🛑 Server shutting down.")


# ─────────────────────────────────────────────
# APP
# ─────────────────────────────────────────────
app = FastAPI(title="School Face Recognition Engine", version="1.0.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Serve student photos → GET /photos/123.jpg
app.mount("/photos", StaticFiles(directory="student_photos"), name="photos")


# ─────────────────────────────────────────────
# HEALTH CHECK  (AWS load balancer + CI/CD needs this)
# ─────────────────────────────────────────────
@app.get("/health")
async def health():
    return {
        "status": "ok",
        "cache_size": len(KNOWN_FACE_ENCODINGS),
    }


# ─────────────────────────────────────────────
# RELOAD CACHE  (call after bulk registration without restart)
# ─────────────────────────────────────────────
@app.post("/reload_cache")
async def reload_cache():
    try:
        count = build_cache()
        return {"status": "success", "vectors_loaded": count}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ─────────────────────────────────────────────
# REGISTER FACE
# ─────────────────────────────────────────────
@app.post("/register_face")
async def register_face(
    person_id:   str        = Form(...),
    person_type: str        = Form(...),
    file:        UploadFile = File(...),
):
    global KNOWN_FACE_ENCODINGS, KNOWN_PERSON_IDS, KNOWN_PERSON_TYPES

    if person_type.lower() not in ["student", "teacher"]:
        raise HTTPException(status_code=400, detail="person_type must be 'student' or 'teacher'")

    try:
        # ── 1. Decode image ──────────────────────────────
        contents = await file.read()
        nparr    = np.frombuffer(contents, np.uint8)
        img      = cv2.imdecode(nparr, cv2.IMREAD_COLOR)

        if img is None:
            raise HTTPException(status_code=400, detail="Invalid image file")

        rgb_img        = cv2.cvtColor(img, cv2.COLOR_BGR2RGB)
        face_encodings = face_recognition.face_encodings(rgb_img)

        if len(face_encodings) == 0:
            raise HTTPException(status_code=400, detail="No face detected in image")

        embedding_vector = face_encodings[0]
        embedding_string = json.dumps(embedding_vector.tolist())

        # ── 2. Save photo (students only) ────────────────
        photo_filename = None
        if person_type.lower() == "student":
            photo_filename  = f"{person_id}.jpg"
            photo_save_path = PHOTOS_DIR / photo_filename
            with open(photo_save_path, "wb") as f:
                f.write(contents)

        # ── 3. DB — UPSERT face descriptor ───────────────
        connection = get_db_connection()
        cursor     = connection.cursor()

        cursor.execute(
            "SELECT id FROM FACE_DESCRIPTORS1 WHERE person_type = :1 AND person_id = :2",
            [person_type.lower(), int(person_id)],
        )
        existing = cursor.fetchone()

        if existing:
            cursor.execute(
                "UPDATE FACE_DESCRIPTORS1 SET descriptor = :1, created_at = SYSDATE "
                "WHERE person_type = :2 AND person_id = :3",
                [embedding_string, person_type.lower(), int(person_id)],
            )
        else:
            cursor.execute(
                "INSERT INTO FACE_DESCRIPTORS1 (id, person_type, person_id, descriptor, created_at) "
                "VALUES (FACE_DESCRIPTORS_SEQ1.NEXTVAL, :1, :2, :3, SYSDATE)",
                [person_type.lower(), int(person_id), embedding_string],
            )

        # ── 4. Update students.photo_url ─────────────────
        if person_type.lower() == "student" and photo_filename:
            cursor.execute(
                "UPDATE students SET photo_url = :1, updated_at = SYSDATE WHERE id = :2",
                [photo_filename, int(person_id)],
            )

        connection.commit()
        cursor.close()
        connection.close()

        # ── 5. Sync RAM cache ────────────────────────────
        try:
            idx = KNOWN_PERSON_IDS.index(int(person_id))
            KNOWN_FACE_ENCODINGS[idx] = embedding_vector
            KNOWN_PERSON_TYPES[idx]   = person_type.lower()
        except ValueError:
            KNOWN_FACE_ENCODINGS.append(embedding_vector)
            KNOWN_PERSON_IDS.append(int(person_id))
            KNOWN_PERSON_TYPES.append(person_type.lower())

        return {
            "status":    "success",
            "message":   f"Face {'updated' if existing else 'registered'} for {person_type.upper()} ID: {person_id}",
            "photo_url": photo_filename,
        }

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ─────────────────────────────────────────────
# VERIFY ATTENDANCE
# ─────────────────────────────────────────────
@app.post("/verify_attendance")
async def verify_attendance(file: UploadFile = File(...)):
    try:
        contents = await file.read()
        nparr    = np.frombuffer(contents, np.uint8)
        img      = cv2.imdecode(nparr, cv2.IMREAD_COLOR)

        if img is None:
            return {"status": "error", "message": "Corrupted frame", "recognized_people": []}

        rgb_img           = cv2.cvtColor(img, cv2.COLOR_BGR2RGB)
        current_encodings = face_recognition.face_encodings(rgb_img)

        if not current_encodings:
            return {"status": "unknown", "message": "No face in frame", "recognized_people": []}

        if not KNOWN_FACE_ENCODINGS:
            return {"status": "unknown", "message": "Cache is empty", "recognized_people": []}

        recognized_people = []

        for current_face in current_encodings:
            face_distances = face_recognition.face_distance(KNOWN_FACE_ENCODINGS, current_face)
            best_idx       = int(np.argmin(face_distances))
            matches        = face_recognition.compare_faces(KNOWN_FACE_ENCODINGS, current_face, tolerance=0.45)

            if matches[best_idx]:
                person_id   = KNOWN_PERSON_IDS[best_idx]
                person_type = KNOWN_PERSON_TYPES[best_idx]
                confidence  = round(float(1 - face_distances[best_idx]), 4)

                recognized_people.append({
                    "person_id":  str(person_id),
                    "role":       person_type,
                    "confidence": confidence,
                })

                # ── Attendance UPSERT — sirf students ke liye ──
                if person_type == "student":
                    try:
                        conn = get_db_connection()
                        cur  = conn.cursor()

                        # Aaj ki date pe row hai ya nahi check karo
                        cur.execute(
                            "SELECT id FROM student_attendance "
                            "WHERE student_id = :1 AND attendance_date = TRUNC(SYSDATE)",
                            [person_id],
                        )
                        existing_attendance = cur.fetchone()

                        if existing_attendance:
                            # Row hai — sirf face_verified aur check_in_time update karo
                            cur.execute(
                                "UPDATE student_attendance "
                                "SET face_verified = 1, "
                                "    check_in_time = SYSTIMESTAMP, "
                                "    updated_at    = SYSDATE "
                                "WHERE student_id = :1 "
                                "AND attendance_date = TRUNC(SYSDATE)",
                                [person_id],
                            )
                        else:
                            # Row nahi hai — naya insert karo
                            cur.execute(
                                "INSERT INTO student_attendance "
                                "(id, student_id, attendance_date, status, face_verified, check_in_time, created_at) "
                                "VALUES "
                                "(student_attendance_seq.NEXTVAL, :1, TRUNC(SYSDATE), 'present', 1, SYSTIMESTAMP, SYSDATE)",
                                [person_id],
                            )

                        conn.commit()
                        cur.close()
                        conn.close()

                    except Exception as db_err:
                        print(f"[ATTENDANCE UPSERT ERROR] student_id={person_id} — {db_err}")

        if recognized_people:
            return {
                "status":            "success",
                "message":           f"{len(current_encodings)} face(s) found, {len(recognized_people)} recognized.",
                "recognized_people": recognized_people,
            }

        return {
            "status":            "unknown",
            "message":           f"{len(current_encodings)} face(s) found, no match in cache.",
            "recognized_people": [],
        }

    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ─────────────────────────────────────────────
# CLASS STUDENTS  (for attendance UI grid)
# ─────────────────────────────────────────────
@app.get("/class_students/{class_id}")
async def get_class_students(class_id: int):
    try:
        connection = get_db_connection()
        cursor     = connection.cursor()
        cursor.execute(
            "SELECT s.id, s.first_name || ' ' || s.surname, s.roll_number, s.photo_url "
            "FROM students s "
            "WHERE s.class_id = :1 AND s.is_active = 1 "
            "ORDER BY s.roll_number",
            [class_id],
        )
        rows = cursor.fetchall()
        cursor.close()
        connection.close()

        students = [
            {
                "id":          str(row[0]),
                "name":        row[1],
                "roll_number": row[2],
                "photo_url":   row[3],
            }
            for row in rows
        ]

        return {"status": "success", "count": len(students), "students": students}

    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ─────────────────────────────────────────────
# ENTRY POINT
# ─────────────────────────────────────────────
if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)