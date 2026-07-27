FROM apache/answer:2.0.2 AS answer-base

FROM golang:1.25-alpine AS plugin-builder

COPY --from=answer-base /usr/bin/answer /usr/bin/answer

ARG GOPROXY=https://proxy.golang.org,direct
ARG NPM_REGISTRY=https://registry.npmjs.org
ARG BASE_PATH=
ARG NODE_OPTIONS=--max-old-space-size=1536
ENV GOPROXY=${GOPROXY}
ENV NODE_OPTIONS=${NODE_OPTIONS}
ENV PUBLIC_URL=${BASE_PATH}
ENV REACT_APP_BASE_URL=${BASE_PATH}

RUN apk add --no-cache bash build-base git nodejs npm

RUN npm config set registry "${NPM_REGISTRY}" \
    && npm install --global pnpm@10.7.0

RUN answer build \
      --with github.com/apache/answer-plugins/connector-basic@v1.2.12 \
      --output /usr/bin/openclass-answer

FROM apache/answer:2.0.2

COPY --from=plugin-builder /usr/bin/openclass-answer /usr/bin/answer
COPY openclass-theme.css /opt/openclass/openclass-theme.css
COPY openclass-sso-bridge.js /opt/openclass/openclass-sso-bridge.js
COPY openclass-ridoc-bridge.js /opt/openclass/openclass-ridoc-bridge.js
COPY entrypoint.sh /usr/local/bin/openclass-answer-entrypoint.sh

RUN chmod +x /usr/local/bin/openclass-answer-entrypoint.sh

ENTRYPOINT ["/usr/local/bin/openclass-answer-entrypoint.sh"]
