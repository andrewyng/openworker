# ows

A Rust CLI over the OpenWorker Service SDK. Build with:

```sh
cargo build --locked --release -p openworker-service-cli
```

Start with the [CLI quickstart](../../docs/harness/SERVICE-M3-QUICKSTART.md) and
[local Docker package](../../deploy/service/README.md). Run `ows --help` for commands.
JSON is written to stdout, errors to stderr; `watch` produces NDJSON.
