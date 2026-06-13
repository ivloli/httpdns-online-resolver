FROM golang:1.25-bookworm AS builder

WORKDIR /src

COPY go.mod go.sum ./
RUN go mod download

COPY . .
RUN CGO_ENABLED=0 GOOS=linux GOARCH=amd64 go build -o /out/httpdns-online-resolver-go .

FROM debian:bookworm-slim

RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates tzdata \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY --from=builder /out/httpdns-online-resolver-go /app/httpdns-online-resolver-go

RUN mkdir -p /etc/httpdns-online-resolver-go /opt/ip2region/data

EXPOSE 8088 38088

ENTRYPOINT ["/app/httpdns-online-resolver-go"]
