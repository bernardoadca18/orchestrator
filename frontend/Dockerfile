FROM node:20-slim
WORKDIR /app
COPY package.json package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY . .
EXPOSE 5174
CMD ["npm", "run", "dev"]
