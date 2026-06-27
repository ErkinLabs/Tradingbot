FROM python:3.12-slim

WORKDIR /app

# Install dependencies first (layer cache)
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy source
COPY . .

# Disable Rich terminal dashboard (no TTY in container)
ENV HEADLESS=true

# Persist logs outside the container via a volume mount
VOLUME ["/app/logs"]

# Web dashboard port
EXPOSE 7000

CMD ["python", "main.py", "--with-dashboard"]
