FROM python:3.12-slim

WORKDIR /app

ENV PYTHONUNBUFFERED=1

# dependencies first, so this layer is cached as long as requirements are unchanged
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# application code only; the geodata is mounted at /app/data at runtime
COPY app/ ./app/

EXPOSE 8080

CMD ["waitress-serve", "--listen=0.0.0.0:8080", "--call", "app:create_app"]
