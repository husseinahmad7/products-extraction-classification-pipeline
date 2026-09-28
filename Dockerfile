FROM node:26-alpine AS dashboard
WORKDIR /web
COPY dashboard/package.json dashboard/package-lock.json ./
RUN npm ci --ignore-scripts
COPY dashboard/ ./
RUN npm run build

FROM python:3.13-slim-bookworm AS build
RUN pip install --no-cache-dir uv==0.12.13
WORKDIR /app
COPY pyproject.toml uv.lock README.md LICENSE NOTICE ./
COPY src/ ./src/
RUN uv sync --frozen --no-dev --extra server --no-editable

FROM python:3.13-slim-bookworm AS runtime
ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIPELINE_DASHBOARD_DIR=/app/dashboard \
    PIPELINE_ARTIFACT_ROOT=/data/artifacts
WORKDIR /app
RUN groupadd --gid 10001 pipeline && useradd --uid 10001 --gid pipeline --no-create-home pipeline && mkdir /data && chown pipeline:pipeline /data
COPY --from=build /app/.venv /app/.venv
COPY --from=dashboard /web/dist /app/dashboard
USER 10001:10001
EXPOSE 8000
ENTRYPOINT ["product-pipeline"]
CMD ["serve", "--host", "0.0.0.0", "--port", "8000"]
