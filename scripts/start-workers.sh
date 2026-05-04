#!/bin/bash
set -e

echo "Starting Celery ingestion worker..."
celery -A src.celery_app worker \
    --queues=rag.ingestion,rag.doc_gen \
    --concurrency=2 \
    --pool=prefork \
    --loglevel=info &

echo "Starting Celery Beat scheduler..."
celery -A src.celery_app beat \
    --loglevel=info &

echo "Starting Flower monitoring on port 5555..."
celery -A src.celery_app flower \
    --port=5555 \
    --persistent=True \
    --db=/tmp/flower.db &

# Wait for any process to exit — if one crashes, the container stops
wait -n
echo "A process exited, shutting down..."
kill $(jobs -p) 2>/dev/null
exit 1
