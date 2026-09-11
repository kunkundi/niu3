FROM node:22-bookworm-slim AS web
WORKDIR /src/web
RUN npm install -g pnpm@11.19.0
COPY web/package.json web/pnpm-lock.yaml web/pnpm-workspace.yaml ./
RUN pnpm install --frozen-lockfile
COPY web/ ./
RUN pnpm build

FROM python:3.12-slim-bookworm
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 TZ=Asia/Shanghai NIUNO3_DATA_DIR=/data NIUNO3_HOST=0.0.0.0 NIUNO3_PORT=8789
WORKDIR /app
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt && useradd --uid 10001 --create-home niuno3 && mkdir /data && chown niuno3 /data
COPY app/ ./app/
COPY config/ ./config/
COPY tests/ ./tests/
COPY scripts/ ./scripts/
COPY --from=web /usr/local/bin/node /usr/local/bin/node
COPY --from=web /src/web/package.json ./web/package.json
COPY --from=web /src/web/src/price-action/ ./web/src/price-action/
COPY --from=web /src/web/dist ./web/dist/
USER niuno3
EXPOSE 8789
CMD ["python", "-m", "app.entrypoints.dashboard"]
