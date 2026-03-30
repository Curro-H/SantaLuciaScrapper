FROM python:3.11-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

EXPOSE 10000

CMD ["sh", "-c", "gunicorn web_app:app --worker-class=gthread --workers=1 --threads=4 --timeout=300 --bind=0.0.0.0:${PORT:-10000}"]
