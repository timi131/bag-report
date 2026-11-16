FROM python:3.12-slim

WORKDIR /app
COPY index.html server.py ./

# Порт берётся из переменной PORT (её задаёт хостинг), по умолчанию 8501.
ENV PORT=8501
EXPOSE 8501

CMD ["python", "/app/server.py", "--host", "0.0.0.0"]
