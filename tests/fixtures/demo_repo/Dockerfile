# A deliberately tiny application, so the intake demo measures the pipeline rather than
# a package manager. busybox httpd serves static files from one process, which is what a
# health check and a teardown both want.
#
# No RUN that installs anything, so this builds under the default `--build-network none`.
FROM busybox:1.36
COPY www /www
EXPOSE 8080
CMD ["httpd", "-f", "-p", "8080", "-h", "/www"]
