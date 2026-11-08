FROM python:3.12-slim

WORKDIR /app

COPY docker/audit/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app app

CMD ["python", "-m", "app.audit_main"]
