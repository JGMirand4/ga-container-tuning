FROM python:3.12-slim
RUN pip install --no-cache-dir gunicorn==22.0.0
WORKDIR /app
COPY app.py .
ENV THREADS=4
CMD ["sh", "-c", "exec gunicorn -k gthread -w ${WORKERS:-1} --threads ${THREADS} -b 0.0.0.0:8080 app:app"]
