FROM python:3.11-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 HOME=/tmp
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt \
    && pip uninstall --yes pip setuptools wheel \
    && useradd --uid 10001 --no-create-home kumaoperator
COPY kuma_operator/ ./kuma_operator/
COPY operator.py .
USER 10001:10001
EXPOSE 8080
CMD ["kopf", "run", "--all-namespaces", "--standalone", "--liveness=http://0.0.0.0:8080/healthz", "operator.py"]
