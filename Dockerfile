FROM python:3.12-slim

WORKDIR /app

# Install dependencies first (layer cache)
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy source
COPY . .

# Disable Rich terminal dashboard (no TTY on VPS)
ENV HEADLESS=true

# Enable web dashboard (Coolify: set DASHBOARD_ENABLED=true in env vars)
ENV DASHBOARD_ENABLED=false

# Persist logs outside the container via a volume mount
VOLUME ["/app/logs"]

# Web dashboard port
EXPOSE 8080

CMD ["sh", "-c", "if [ \"$DASHBOARD_ENABLED\" = 'true' ]; then python main.py --with-dashboard; else python main.py; fi"]
