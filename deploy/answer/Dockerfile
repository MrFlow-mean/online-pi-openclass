FROM apache/answer:2.0.2 AS answer-base

FROM golang:1.25-alpine AS plugin-builder

COPY --from=answer-base /usr/bin/answer /usr/bin/answer

RUN apk add --no-cache bash build-base git nodejs npm \
    && npm install --global pnpm@10.7.0 \
    && answer build \
        --with github.com/apache/answer-plugins/connector-basic@v1.2.12 \
        --output /usr/bin/openclass-answer

FROM apache/answer:2.0.2

COPY --from=plugin-builder /usr/bin/openclass-answer /usr/bin/answer
