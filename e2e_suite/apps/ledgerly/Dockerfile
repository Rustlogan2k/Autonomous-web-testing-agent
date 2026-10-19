FROM node:22-slim
WORKDIR /app
# The pinned PostgreSQL driver arrives with the build (vendor/node): nothing is installed here.
COPY vendor/node ./vendor/node
COPY src ./src
COPY seed ./seed
COPY vocab ./vocab
# DATABASE_URL (the app's home database) is given at run time.
ENV PORT=8080 HOST=0.0.0.0 NODE_ENV=production
EXPOSE 8080
USER node
CMD ["node", "src/server.js"]
