# CIForge server image. The Docker runner needs the docker CLI and the host's socket:
#   docker run -p 8111:8111 -v /var/run/docker.sock:/var/run/docker.sock ...
FROM docker:28-cli AS dockercli

FROM python:3.13-slim
RUN apt-get update && apt-get install -y --no-install-recommends git ca-certificates bash \
    && rm -rf /var/lib/apt/lists/*
COPY --from=dockercli /usr/local/bin/docker /usr/local/bin/docker
WORKDIR /app
COPY pyproject.toml README.md LICENSE ./
COPY src ./src
# Repositories are mounted from the host (owned by another uid); let git read them.
RUN pip install --no-cache-dir . && git config --system --add safe.directory '*'
ENV CIFORGE_DATA_DIR=/data
VOLUME ["/data"]
EXPOSE 8111
HEALTHCHECK --interval=10s --timeout=3s --retries=5 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8111/healthz')"
CMD ["ciforge", "serve", "--host", "0.0.0.0", "--port", "8111"]
