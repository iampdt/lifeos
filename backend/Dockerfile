# LifeOS API. Render runs this image on its free web tier; docker-compose runs it locally.
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PORT=8000

WORKDIR /app
COPY requirements.txt .
RUN pip install -r requirements.txt

COPY . .
RUN useradd --create-home --uid 10001 lifeos
USER lifeos

EXPOSE 8000
# Migrations run on start: the free tier has no pre-deploy step, and one instance means no race.
CMD ["sh", "scripts/start.sh"]
