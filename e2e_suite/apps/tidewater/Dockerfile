FROM python:3.12-slim
WORKDIR /app
# The build carries the suite's pinned pg8000 under vendor/python; nothing is downloaded.
COPY vendor ./vendor
COPY src ./src
COPY seed ./seed
COPY vocab ./vocab
# DATABASE_URL is supplied when the container runs; the server will not start without it.
ENV PORT=8080 HOST=0.0.0.0 PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1
EXPOSE 8080
USER 65534
CMD ["python", "src/server.py"]
