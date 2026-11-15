# One container: FastAPI serves the API and the built frontend. No user login anywhere.
# Works on Hugging Face Spaces (Docker SDK, port 7860, non-root user 1000).
FROM node:22-slim AS web
WORKDIR /web
COPY frontend/package*.json ./
RUN npm ci
COPY frontend/ ./
RUN npm run build

FROM python:3.12-slim
# rasterio/GDAL wheels need libexpat at runtime; the slim image leaves it out
RUN apt-get update && apt-get install -y --no-install-recommends libexpat1 && rm -rf /var/lib/apt/lists/*
RUN useradd -m -u 1000 user
WORKDIR /app
COPY backend/requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt
COPY --chown=user backend/ ./backend/
COPY --chown=user --from=web /web/dist ./frontend/dist
USER user
# No Earthdata token in the image: showcase places are served from backend/demo; new deep analyses stay off
# unless the host sets EARTHDATA_TOKEN as a private secret.
ENV FRONTEND_DIST=/app/frontend/dist CACHE_DIR=/tmp/earthecho-cache HOME=/home/user
EXPOSE 7860
# Hosts like Render set $PORT; Hugging Face uses 7860.
CMD ["sh", "-c", "exec python -m uvicorn app.main:app --app-dir backend --host 0.0.0.0 --port ${PORT:-7860}"]
