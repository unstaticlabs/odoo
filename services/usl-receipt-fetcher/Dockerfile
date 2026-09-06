FROM mcr.microsoft.com/playwright/python:v1.62.0-noble@sha256:aa81288e738725378becba5b3e06cb0f3a7f012a610e87e8d767a090ea3f740d

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    HOME=/home/pwuser \
    TMPDIR=/tmp/receipt-fetcher

RUN apt-get update \
    && apt-get install --yes --no-install-recommends qpdf=11.9.0-1.1ubuntu0.1 \
    && rm -rf /var/lib/apt/lists/* \
    && mkdir -p /app /tmp/receipt-fetcher /run/receipt-control \
    && chown -R pwuser:pwuser /app /tmp/receipt-fetcher /run/receipt-control

COPY services/usl-receipt-fetcher/requirements.txt /app/requirements.txt
RUN python -m pip install --no-cache-dir --requirement /app/requirements.txt

COPY --chown=pwuser:pwuser services/usl-receipt-fetcher/app.py /app/app.py
COPY --chown=pwuser:pwuser services/usl-receipt-fetcher/validate_pdf.py /app/validate_pdf.py

USER pwuser
WORKDIR /app
ENTRYPOINT ["python", "-m", "uvicorn", "app:app", "--uds", "/run/receipt-control/fetcher.sock", "--no-access-log", "--log-level", "warning", "--ssl-certfile", "/run/secrets/receipt-fetcher/server.crt", "--ssl-keyfile", "/run/secrets/receipt-fetcher/server.key", "--ssl-ca-certs", "/run/secrets/receipt-fetcher/ca.crt", "--ssl-cert-reqs", "2"]
