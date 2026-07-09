FROM node:20-slim AS builder

WORKDIR /app
COPY package.json ./
RUN npm install --legacy-peer-deps
COPY . .
RUN npm run build

FROM nginx:1.25-alpine

RUN rm /etc/nginx/conf.d/default.conf
COPY nginx.conf /etc/nginx/conf.d/netpol-dashboard.conf
COPY --from=builder /app/dist /usr/share/nginx/html

RUN chmod -R g+rwX /var/cache/nginx /var/run /var/log/nginx /usr/share/nginx/html \
    && chown -R nginx:0 /var/cache/nginx /var/run /var/log/nginx \
    && sed -i 's|pid.*nginx.pid|pid /tmp/nginx.pid|' /etc/nginx/nginx.conf

EXPOSE 8080
CMD ["nginx", "-g", "daemon off;"]
