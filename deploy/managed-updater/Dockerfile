FROM alpine:3.21
RUN apk add --no-cache python3 git docker-cli docker-cli-buildx ca-certificates
WORKDIR /app
COPY deploy/managed-updater/ /app/
RUN chmod 755 /app/askpass.sh
ENV PYTHONUNBUFFERED=1 GIT_TERMINAL_PROMPT=0 GIT_ASKPASS=/app/askpass.sh
HEALTHCHECK --interval=30s --timeout=5s CMD python3 -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8099/health', timeout=3)"
CMD ["python3", "/app/server.py"]
