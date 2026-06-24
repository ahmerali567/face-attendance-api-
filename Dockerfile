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
        wget \
        alien \
    && ln -s /usr/lib/x86_64-linux-gnu/libaio.so.1t64 /usr/lib/x86_64-linux-gnu/libaio.so.1 \
    && wget -q https://download.oracle.com/otn_software/linux/instantclient/1923000/oracle-instantclient19.23-basiclite-19.23.0.0.0-1.x86_64.rpm \
    && alien -i oracle-instantclient19.23-basiclite-19.23.0.0.0-1.x86_64.rpm \
    && rm oracle-instantclient19.23-basiclite-19.23.0.0.0-1.x86_64.rpm \
    && echo "/usr/lib/oracle/19.23/client64/lib" > /etc/ld.so.conf.d/oracle.conf \
    && ldconfig \
    && rm -rf /var/lib/apt/lists/*

ENV LD_LIBRARY_PATH=/usr/lib/oracle/19.23/client64/lib
ENV ORACLE_LIB_DIR=/usr/lib/oracle/19.23/client64/lib

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY main.py .
RUN mkdir -p /app/student_photos

EXPOSE 8000
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
