# Stage 1: Build React frontend
FROM node:20-alpine AS frontend-build

WORKDIR /app/frontend

# Install dependencies first (layer caching)
COPY frontend/package.json frontend/package-lock.json* frontend/yarn.lock* ./
COPY frontend/craco.config.js frontend/jsconfig.json frontend/tailwind.config.js frontend/postcss.config.js ./
COPY frontend/components.json ./
RUN npm install --legacy-peer-deps

# Copy source and build
COPY frontend/ ./
RUN npm run build


# Stage 2: Python backend + serve React static files
FROM python:3.11-slim

WORKDIR /app

# Install system dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    git \
    && rm -rf /var/lib/apt/lists/*

# Install Python dependencies
COPY backend/requirements.txt ./requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

# Copy backend code
COPY backend/ ./backend/

# Copy React build from stage 1
COPY --from=frontend-build /app/frontend/build ./frontend/build

# Create data directories for audit logs
RUN mkdir -p /app/backend/data/audit_logs

# Expose port (Railway injects $PORT)
EXPOSE ${PORT:-8000}

# Start FastAPI server
CMD uvicorn backend.server:app --host 0.0.0.0 --port ${PORT:-8000}
