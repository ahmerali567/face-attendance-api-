import os
import json
import numpy as np
import cv2
import oracledb
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Form, UploadFile, File
from fastapi.middleware.cors import CORSMiddleware
from contextlib import asynccontextmanager
import face_recognition


load_dotenv()


LIB_DIR = os.getenv("ORACLE_LIB_DIR", "/opt/oracle/instantclient_19_23")
oracledb.init_oracle_client(lib_dir=LIB_DIR)


db_config = {
    "user": os.getenv("DB_USER"),
    "password": os.getenv("DB_PASSWORD"),
    "dsn": os.getenv("DB_DSN")
}


KNOWN_FACE_ENCODINGS = []
KNOWN_PERSON_IDS = []
KNOWN_PERSON_TYPES = []

def get_db_connection():
    try:
        return oracledb.connect(**db_config)
    except Exception as e:
        print(f"Database Connectivity Error: {e}")
        raise HTTPException(status_code=500, detail="Central database unreachable")

@asynccontextmanager
async def lifespan(app: FastAPI):
    global KNOWN_FACE_ENCODINGS, KNOWN_PERSON_IDS, KNOWN_PERSON_TYPES
    print("🧠 Initiating Biometric Cache from Remote Oracle Database...")
    try:
        connection = get_db_connection()
        cursor = connection.cursor()
        cursor.execute("SELECT person_id, person_type, descriptor FROM FACE_DESCRIPTORS WHERE descriptor IS NOT NULL")
        records = cursor.fetchall()
        
        KNOWN_FACE_ENCODINGS = []
        KNOWN_PERSON_IDS = []
        KNOWN_PERSON_TYPES = []
        
        for row in records:
            p_id = row[0]
            p_type = row[1]
            embedding_data = row[2]
            
            if embedding_data:
                if hasattr(embedding_data, 'read'):
                    embedding_data = embedding_data.read()
                KNOWN_FACE_ENCODINGS.append(np.array(json.loads(embedding_data)))
                KNOWN_PERSON_IDS.append(p_id)
                KNOWN_PERSON_TYPES.append(p_type)
                
        cursor.close()
        connection.close()
        print(f" Cache sync complete. Loaded {len(KNOWN_FACE_ENCODINGS)} vectors in RAM.")
    except Exception as e:
        print(f" Failed to build initial cache: {e}")
    
    yield
    print("Stopping server and cleaning up resources...")

app = FastAPI(title="School Face Recognition Engine", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.post("/register_face")
async def register_face(person_id: str = Form(...), person_type: str = Form(...), file: UploadFile = File(...)):
    global KNOWN_FACE_ENCODINGS, KNOWN_PERSON_IDS, KNOWN_PERSON_TYPES
    if person_type.lower() not in ['student', 'teacher']:
         raise HTTPException(status_code=400, detail="person_type must be 'student' or 'teacher'")

    try:
        contents = await file.read()
        nparr = np.frombuffer(contents, np.uint8)
        img = cv2.imdecode(nparr, cv2.IMREAD_COLOR)
        
        if img is None:
            raise HTTPException(status_code=400, detail="Invalid image file format")
            
        rgb_img = cv2.cvtColor(img, cv2.COLOR_BGR2RGB)
        face_encodings = face_recognition.face_encodings(rgb_img)
        
        if len(face_encodings) == 0:
            raise HTTPException(status_code=400, detail="No face detected in the image")
            
        embedding_vector = face_encodings[0]
        embedding_string = json.dumps(embedding_vector.tolist())
        
        connection = get_db_connection()
        cursor = connection.cursor()
        
        
        query = """
        INSERT INTO FACE_DESCRIPTORS (id, person_type, person_id, descriptor, created_at) 
        VALUES (FACE_DESCRIPTORS_SEQ.NEXTVAL, :1, :2, :3, CURRENT_TIMESTAMP)
        """
        cursor.execute(query, [person_type.lower(), person_id, embedding_string])
        connection.commit()
        cursor.close()
        connection.close()
        
        KNOWN_FACE_ENCODINGS.append(embedding_vector)
        KNOWN_PERSON_IDS.append(person_id)
        KNOWN_PERSON_TYPES.append(person_type.lower())
        
        return {
            "status": "success", 
            "message": f"Face metrics registered securely via Sequence for {person_type.upper()} ID: {person_id}"
        }
        
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/verify_attendance")
async def verify_attendance(file: UploadFile = File(...)):
    try:
        contents = await file.read()
        nparr = np.frombuffer(contents, np.uint8)
        img = cv2.imdecode(nparr, cv2.IMREAD_COLOR)
        
        if img is None:
            return {"status": "error", "message": "Corrupted video frame"}
            
        rgb_img = cv2.cvtColor(img, cv2.COLOR_BGR2RGB)
        current_encodings = face_recognition.face_encodings(rgb_img)
        
        if len(current_encodings) == 0:
            return {"status": "unknown", "message": "Clear line of sight lost. No face in frame.", "recognized_people": []}
            
        if not KNOWN_FACE_ENCODINGS:
            return {"status": "unknown", "message": "Biometric memory cache is empty.", "recognized_people": []}
            
        recognized_people = []
        
        for current_face in current_encodings:
            matches = face_recognition.compare_faces(KNOWN_FACE_ENCODINGS, current_face, tolerance=0.45)
            face_distances = face_recognition.face_distance(KNOWN_FACE_ENCODINGS, current_face)
            best_match_index = np.argmin(face_distances)
            
            if matches[best_match_index]:
                matched_id = KNOWN_PERSON_IDS[best_match_index]
                matched_type = KNOWN_PERSON_TYPES[best_match_index]
                confidence = float(1 - face_distances[best_match_index])
                
                recognized_people.append({
                    "person_id": matched_id,
                    "role": matched_type,
                    "confidence": round(confidence, 4)
                })
        
        if recognized_people:
            return {
                "status": "success", 
                "message": f"Processed via RAM Cache. Found {len(current_encodings)} face(s). Recognized {len(recognized_people)}.",
                "recognized_people": recognized_people
            }
        else:
            return {
                "status": "unknown", 
                "message": f"Found {len(current_encodings)} face(s), but cache matrix mismatch.",
                "recognized_people": []
            }
            
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)