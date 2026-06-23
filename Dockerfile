FROM python:3.11-slim

RUN apt-get update && apt-get install -y --no-install-recommends \
        build-essential \
        cmake \
        libopenblas-dev \
        liblapack-dev \
        libx11-dev \
        libgtk-3-dev \
        libboost-python-dev \
        unzip \
        libaio1t64 \
    && ln -s /usr/lib/x86_64-linux-gnu/libaio.so.1t64 /usr/lib/x86_64-linux-gnu/libaio.so.1 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY instantclient-basiclite-linux.x64-19.23.0.0.0dbru.zip /tmp/ic.zip
RUN unzip /tmp/ic.zip -d /opt/oracle \
    && rm /tmp/ic.zip \
    && echo "/opt/oracle/instantclient_19_23" > /etc/ld.so.conf.d/oracle.conf \
    && ldconfig

ENV LD_LIBRARY_PATH=/opt/oracle/instantclient_19_23

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY main.py .

RUN mkdir -p /app/student_photos

EXPOSE 8000
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
