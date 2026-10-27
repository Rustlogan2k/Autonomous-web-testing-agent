# Inkwell Stationers: one Node.js process, no packages, so nothing is installed at build time.
FROM node:22-slim
WORKDIR /app
COPY server.js README.md ./
ENV PORT=8080
EXPOSE 8080
USER node
CMD ["node", "server.js"]
