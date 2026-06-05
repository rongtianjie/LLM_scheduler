FROM python:3.11-slim

WORKDIR /app

# Install dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application code
COPY app/ app/
COPY config.yaml .

# Create data directory for SQLite
RUN mkdir -p data

EXPOSE 8001

CMD ["uvicorn", "app.main:create_app", "--host", "0.0.0.0", "--port", "8001", "--factory"]
