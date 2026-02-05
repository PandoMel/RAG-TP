FROM python:3.11-slim
WORKDIR /app
COPY worker/requirements.txt /tmp/requirements.txt
RUN pip install --no-cache-dir -r /tmp/requirements.txt
COPY worker/app /app/app
CMD ["celery", "-A", "app.tasks:celery_app", "worker", "--loglevel=WARNING", "--concurrency=1"]
