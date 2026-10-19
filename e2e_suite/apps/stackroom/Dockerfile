FROM python:3.12-slim
WORKDIR /srv/stackroom
# The suite's pinned, pure-Python PostgreSQL driver arrives with the build (vendor/python);
# nothing is installed, so the image builds with no network.
COPY vendor ./vendor
COPY src ./src
COPY seed ./seed
COPY vocab ./vocab
# DATABASE_URL is given at run time and has no default: the server refuses to start without it.
ENV PORT=8080 HOST=0.0.0.0 PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1
EXPOSE 8080
USER 65534
CMD ["python", "src/server.py"]
