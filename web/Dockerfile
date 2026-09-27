FROM node:20-slim AS build
WORKDIR /app
# npm ci installs exactly what package-lock.json records and fails if the two
# disagree; npm install would re-resolve the ^ ranges on every deploy.
COPY package.json package-lock.json ./
RUN npm ci
COPY . .
# NEXT_PUBLIC_* vars are baked in at BUILD time, not read at container start —
# so this has to be a build arg, not a docker-compose `environment:` entry.
ARG NEXT_PUBLIC_API_URL
ENV NEXT_PUBLIC_API_URL=$NEXT_PUBLIC_API_URL
RUN npm run build

FROM node:20-slim
WORKDIR /app
COPY --from=build /app ./
EXPOSE 3000
CMD ["npm", "start"]
