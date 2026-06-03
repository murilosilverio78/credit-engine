FROM mcr.microsoft.com/playwright/python:v1.47.0-noble

WORKDIR /app

RUN apt-get update && apt-get install -y \
    gcc \
    libpq-dev \
    && rm -rf /var/lib/apt/lists/*

COPY backend/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY backend/ .

EXPOSE 8000

ENTRYPOINT ["sh", "-c"]
CMD ["uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000}"]
