FROM python:3.12-slim
WORKDIR /app
# The suite's pinned pg8000 comes with the build (vendor/python); the image installs nothing.
COPY vendor ./vendor
COPY src ./src
COPY seed ./seed
COPY vocab ./vocab
# DATABASE_URL is given when the container runs; without it the server refuses to start.
ENV PORT=8080 HOST=0.0.0.0 PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1
EXPOSE 8080
USER 65534
CMD ["python", "src/server.py"]
