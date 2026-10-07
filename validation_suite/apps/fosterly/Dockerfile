FROM node:22-slim
WORKDIR /srv/fosterly
COPY src ./src
COPY seed ./seed
COPY vocab ./vocab
ENV PORT=8080 HOST=0.0.0.0 NODE_ENV=production WTA_DATA_DIR=/tmp/fosterly-data
EXPOSE 8080
USER node
CMD ["node", "src/server.js"]
