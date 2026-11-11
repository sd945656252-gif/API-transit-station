FROM alpine:3.21
RUN apk add --no-cache python3 docker-cli ca-certificates
WORKDIR /app
COPY deploy/managed-updater/ /app/
ENV PYTHONUNBUFFERED=1
HEALTHCHECK --interval=30s --timeout=5s CMD python3 -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8099/health', timeout=3)"
CMD ["python3", "/app/server.py"]
