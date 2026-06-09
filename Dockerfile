# Pin to bookworm: Debian trixie renamed libaio1 -> libaio1t64, which breaks
# the Oracle Instant Client dependency below. bookworm ships libaio1.
FROM python:3.11-slim-bookworm

# Oracle Instant Client dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    libaio1 \
    wget \
    unzip \
    && rm -rf /var/lib/apt/lists/*

# Install Oracle Instant Client (Basic Lite).
# Pinned to 19c: it is the latest client line that still connects to Oracle
# 11.2 (the project targets "Oracle 11g+"); 21c/23ai clients dropped 11.2.
# A stable `instantclient` symlink points at whatever versioned directory the
# zip extracts to, so LD_LIBRARY_PATH and ldconfig can never drift from the
# real path (the previous hard-coded `instantclient_21_13` was wrong).
ARG ORACLE_IC_URL=https://download.oracle.com/otn_software/linux/instantclient/1924000/instantclient-basiclite-linux.x64-19.24.0.0.0dbru.zip
RUN mkdir -p /opt/oracle && cd /opt/oracle \
    && wget -q "$ORACLE_IC_URL" -O instantclient.zip \
    && unzip -q instantclient.zip \
    && rm -f instantclient.zip \
    && ln -s /opt/oracle/instantclient_* /opt/oracle/instantclient \
    && echo /opt/oracle/instantclient > /etc/ld.so.conf.d/oracle.conf \
    && ldconfig

ENV LD_LIBRARY_PATH=/opt/oracle/instantclient:$LD_LIBRARY_PATH

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

EXPOSE 8000

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
