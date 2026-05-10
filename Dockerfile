FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY operator.py .

# Run with --standalone to avoid needing a peering CRD
CMD ["kopf", "run", "--standalone", "--peering=standalone", "operator.py"]
