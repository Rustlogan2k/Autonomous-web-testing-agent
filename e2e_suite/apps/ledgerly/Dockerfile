FROM node:22-slim
WORKDIR /app
COPY src ./src
COPY seed ./seed
COPY vocab ./vocab
ENV PORT=8080 HOST=0.0.0.0 NODE_ENV=production
EXPOSE 8080
USER node
CMD ["node", "src/server.js"]
