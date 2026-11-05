# Pin by digest before the real experiment (docker buildx imagetools inspect golang:1.23)
FROM golang:1.23 AS build
WORKDIR /src
COPY main.go .
RUN go mod init svc && CGO_ENABLED=0 go build -o /svc .

FROM gcr.io/distroless/static
COPY --from=build /svc /svc
ENTRYPOINT ["/svc"]
