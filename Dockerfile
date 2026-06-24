FROM ghcr.io/oracle/oraclelinux8-instantclient:19 AS oracle-client

FROM python:3.11-slim

RUN apt-get update && apt-get install -y --no-install-recommends \
        build-essential \
        cmake \
        libopenblas-dev \
        liblapack-dev \
        libx11-dev \
        libgtk-3-dev \
        libboost-python-dev \
        libaio1t64 \
    && ln -s /usr/lib/x86_64-linux-gnu/libaio.so.1t64 /usr/lib/x86_64-linux-gnu/libaio.so.1 \
    && rm -rf /var/lib/apt/lists/*

COPY --from=oracle-client /usr/lib/oracle/19/client64/lib /opt/oracle/instantclient_19
COPY --from=oracle-client /usr/lib/oracle/19/client64/bin /opt/oracle/bin

RUN echo "/opt/oracle/instantclient_19" > /etc/ld.so.conf.d/oracle.conf && ldconfig

ENV LD_LIBRARY_PATH=/opt/oracle/instantclient_19
ENV ORACLE_LIB_DIR=/opt/oracle/instantclient_19

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY main.py .
RUN mkdir -p /app/student_photos

EXPOSE 8000
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
