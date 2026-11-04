# Stage 1: сборка фронтенда (React + Vite + Tailwind)
FROM node:20-alpine AS frontend
WORKDIR /fe
COPY frontend/package.json frontend/package-lock.json ./
RUN npm install --no-audit --no-fund
COPY frontend/ ./
RUN npm run build

# Stage 2: backend (FastAPI + ffmpeg)
FROM python:3.12-slim
RUN apt-get update && apt-get install -y --no-install-recommends ffmpeg \
    && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY services/backend/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY services/backend/main.py .
COPY services/backend/entrypoint.sh .
RUN chmod +x entrypoint.sh
COPY --from=frontend /fe/dist ./dist
EXPOSE 8000
ENTRYPOINT ["./entrypoint.sh"]
