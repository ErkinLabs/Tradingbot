FROM python:3.11-slim

WORKDIR /app

# System dependencies for python packages if needed (like gcc, etc.)
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt fastapi uvicorn

COPY . .

# logs klasörünü oluştur ve izinleri ayarla
RUN mkdir -p logs

EXPOSE 3005

# Trading bot'u arka planda, FastAPI web sunucusunu ön planda başlatan betik
CMD ["sh", "-c", "python main.py & uvicorn web_server:app --host 0.0.0.0 --port 3005"]
