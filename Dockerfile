FROM python:3.12-slim

WORKDIR /app
COPY index.html server.py ./

# Порт берётся из переменной PORT (её задаёт хостинг), по умолчанию 8501.
# База и вложения лежат в /data — подключите к этой папке постоянный диск (volume),
# иначе данные пропадут при пересоздании контейнера.
ENV PORT=8501 \
    DATA_DIR=/data \
    PYTHONUNBUFFERED=1
RUN mkdir -p /data
EXPOSE 8501

CMD ["python", "/app/server.py", "--host", "0.0.0.0"]
