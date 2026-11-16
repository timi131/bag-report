FROM python:3.12-slim

WORKDIR /app
COPY index.html README.md ./

# Порт берётся из переменной PORT (её задаёт хостинг), по умолчанию 8501.
ENV PORT=8501
EXPOSE 8501

CMD ["sh", "-c", "python -m http.server \"$PORT\" --bind 0.0.0.0 --directory /app"]
