FROM python:3.12-slim

RUN apt-get update && apt-get install -y --no-install-recommends git && rm -rf /var/lib/apt/lists/* \
    && git config --global --add safe.directory /project

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY watcher/ watcher/

# Run as host user so output files have correct ownership
RUN useradd -u 1000 -m appuser
USER appuser

CMD ["python", "-m", "watcher.main"]
