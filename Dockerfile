# ── Base image ────────────────────────────────────────────────────────────────
FROM python:3.11-slim

# ── System deps (dlib / face_recognition compile karne ke liye) ───────────────
RUN apt-get update && apt-get install -y --no-install-recommends \
        build-essential \
        cmake \
        libopenblas-dev \
        liblapack-dev \
        libx11-dev \
        libgtk-3-dev \
        libboost-python-dev \
        wget \
        unzip \
    && rm -rf /var/lib/apt/lists/*

# ── Oracle Instant Client (Thick mode ke liye) ────────────────────────────────
# Download karo ya AWS S3/ECR pe pre-bake karo (CI/CD mein)
ARG ORACLE_VERSION=21.13.0.0.0
ENV ORACLE_LIB_DIR=/opt/oracle/instantclient_21_20

RUN mkdir -p /opt/oracle && \
    wget -q "https://download.oracle.com/otn_software/linux/instantclient/2113000/instantclient-basiclite-linux.x64-21.13.0.0.0dbru.zip" \
         -O /tmp/ic.zip && \
    unzip /tmp/ic.zip -d /opt/oracle && \
    rm /tmp/ic.zip && \
    echo "/opt/oracle/instantclient_21_20" > /etc/ld.so.conf.d/oracle.conf && \
    ldconfig

# ── App setup ─────────────────────────────────────────────────────────────────
WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY main.py .

# Photos persist karni hain — volume mount karo EC2 pe
RUN mkdir -p /app/student_photos

# ── Run ───────────────────────────────────────────────────────────────────────
EXPOSE 8000
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]