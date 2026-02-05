FROM python:3.11-slim
WORKDIR /app
COPY reranker/requirements.txt /tmp/requirements.txt
RUN pip install --no-cache-dir -r /tmp/requirements.txt
COPY reranker/app /app/app
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8090"]
