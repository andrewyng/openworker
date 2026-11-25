FROM golang:1.25-bookworm AS build
WORKDIR /src
COPY go.mod *.go ./
RUN CGO_ENABLED=0 go build -trimpath -o /gateway .
FROM scratch
COPY --from=build /gateway /gateway
USER 65532:65532
ENTRYPOINT ["/gateway"]
