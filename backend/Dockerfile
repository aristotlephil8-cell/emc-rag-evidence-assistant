# syntax=docker/dockerfile:1.7
FROM python:3.11.15-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

RUN apt-get update \
    && apt-get install --yes --no-install-recommends libgomp1 \
    && rm -rf /var/lib/apt/lists/*

RUN groupadd --system cvrag \
    && useradd --system --gid cvrag --create-home cvrag

COPY pyproject.toml ./pyproject.toml
COPY app ./app
RUN --mount=type=cache,target=/root/.cache/pip \
    python -m pip install .

RUN mkdir -p /app/data/runtime /app/artifacts/evaluation /app/models/deepdoc \
    && chown -R cvrag:cvrag /app

USER cvrag

EXPOSE 8000

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
