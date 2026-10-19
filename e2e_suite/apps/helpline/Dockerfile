FROM python:3.12-slim
WORKDIR /app
# The suite's pinned, pure-Python pymongo (and bson, dnspython) come with the build under
# vendor/python; the image installs nothing.
COPY vendor ./vendor
COPY src ./src
COPY seed ./seed
COPY vocab ./vocab
# MONGODB_URL is given when the container runs; without it the server refuses to start.
ENV PORT=8080 HOST=0.0.0.0 PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1
EXPOSE 8080
USER 65534
CMD ["python", "src/server.py"]
