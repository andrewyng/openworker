//! Local qualification helper: a loopback listener through OpenShell's verified
//! mTLS ForwardTcp API (pinned OpenShell v0.1.3 wire format). It executes no tool and never changes sandbox policy.
use anyhow::{ensure, Context, Result};
use prost::{Message, Oneof};
use serde::Deserialize;
use std::{path::PathBuf, sync::Arc, time::Duration};
use tokio::{
    io::{AsyncReadExt, AsyncWriteExt},
    net::TcpStream,
    sync::{mpsc, Semaphore},
};
use tokio_stream::wrappers::ReceiverStream;
use tonic::{
    transport::{Certificate, Channel, ClientTlsConfig, Identity},
    Request,
};
#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct Config {
    endpoint: String,
    certificates: PathBuf,
    tls_server_name: String,
    sandbox_id: String,
    sandbox_name: String,
    workspace: String,
    local_port: u16,
    target_port: u16,
}
#[derive(Clone, PartialEq, Message)]
struct SessionRequest {
    #[prost(string, tag = "1")]
    sandbox: String,
    #[prost(message, optional, tag = "3")]
    workspace_scope: Option<openworker_runtime::sandbox::WorkspaceSelector>,
}
#[derive(Clone, PartialEq, Message)]
struct SessionResponse {
    #[prost(string, tag = "1")]
    sandbox_id: String,
    #[prost(string, tag = "2")]
    token: String,
}
#[derive(Clone, PartialEq, Message)]
struct Target {
    #[prost(string, tag = "1")]
    host: String,
    #[prost(uint32, tag = "2")]
    port: u32,
}
#[derive(Clone, PartialEq, Message)]
struct Init {
    #[prost(string, tag = "1")]
    sandbox: String,
    #[prost(string, tag = "3")]
    workspace: String,
    #[prost(string, tag = "4")]
    service_id: String,
    #[prost(message, optional, tag = "6")]
    tcp: Option<Target>,
    #[prost(string, tag = "7")]
    authorization_token: String,
}
#[derive(Clone, PartialEq, Message)]
struct Frame {
    #[prost(oneof = "Payload", tags = "1,2")]
    payload: Option<Payload>,
}
#[derive(Clone, PartialEq, Oneof)]
enum Payload {
    #[prost(message, tag = "1")]
    Init(Init),
    #[prost(bytes, tag = "2")]
    Data(Vec<u8>),
}
async fn forward(channel: Channel, cfg: Arc<Config>, socket: TcpStream) -> Result<()> {
    let mut grpc = tonic::client::Grpc::new(channel);
    grpc.ready().await?;
    let mut request = Request::new(SessionRequest {
        sandbox: cfg.sandbox_name.clone(),
        workspace_scope: Some(openworker_runtime::sandbox::WorkspaceSelector {
            workspace: cfg.workspace.clone(),
        }),
    });
    request.set_timeout(Duration::from_secs(10));
    let session: SessionResponse = grpc
        .unary(
            request,
            tonic::codegen::http::uri::PathAndQuery::from_static(
                "/openshell.v1.OpenShell/CreateSshSession",
            ),
            tonic::codec::ProstCodec::default(),
        )
        .await?
        .into_inner();
    ensure!(
        session.sandbox_id == cfg.sandbox_id && !session.token.is_empty(),
        "Forward authorization identity mismatch"
    );
    let (tx, rx) = mpsc::channel(16);
    tx.send(Frame {
        payload: Some(Payload::Init(Init {
            sandbox: cfg.sandbox_name.clone(),
            workspace: cfg.workspace.clone(),
            service_id: "openworker-local-qualification".into(),
            tcp: Some(Target {
                host: "127.0.0.1".into(),
                port: cfg.target_port.into(),
            }),
            authorization_token: session.token,
        })),
    })
    .await?;
    grpc.ready().await?;
    let mut request = Request::new(ReceiverStream::new(rx));
    request.set_timeout(Duration::from_secs(60));
    let mut reply = grpc
        .streaming(
            request,
            tonic::codegen::http::uri::PathAndQuery::from_static(
                "/openshell.v1.OpenShell/ForwardTcp",
            ),
            tonic::codec::ProstCodec::<Frame, Frame>::default(),
        )
        .await?
        .into_inner();
    let (mut read, mut write) = socket.into_split();
    let sending = tokio::spawn(async move {
        let mut b = [0; 16384];
        loop {
            let n = read.read(&mut b).await?;
            if n == 0 {
                break;
            }
            if tx
                .send(Frame {
                    payload: Some(Payload::Data(b[..n].to_vec())),
                })
                .await
                .is_err()
            {
                break;
            }
        }
        anyhow::Ok(())
    });
    let result = async {
        while let Some(frame) = reply.message().await? {
            if let Some(Payload::Data(b)) = frame.payload {
                write.write_all(&b).await?;
            }
        }
        anyhow::Ok(())
    }
    .await;
    sending.abort();
    let _ = sending.await;
    let _ = write.shutdown().await;
    result
}
#[tokio::main]
async fn main() -> Result<()> {
    let cfg: Config = serde_json::from_slice(&std::fs::read(
        std::env::args()
            .nth(1)
            .context("Private preview config required")?,
    )?)?;
    let url = reqwest::Url::parse(&cfg.endpoint)?;
    ensure!(
        url.scheme() == "https"
            && matches!(url.host_str(), Some("localhost" | "127.0.0.1"))
            && url.username().is_empty()
            && url.password().is_none()
            && url.query().is_none()
            && url.fragment().is_none(),
        "Verified loopback gateway only"
    );
    ensure!(
        cfg.local_port >= 1024 && cfg.target_port >= 1024,
        "Unprivileged ports required"
    );
    uuid::Uuid::parse_str(&cfg.sandbox_id)?;
    let tls = ClientTlsConfig::new()
        .domain_name(&cfg.tls_server_name)
        .ca_certificate(Certificate::from_pem(std::fs::read(
            cfg.certificates.join("ca.crt"),
        )?))
        .identity(Identity::from_pem(
            std::fs::read(cfg.certificates.join("tls.crt"))?,
            std::fs::read(cfg.certificates.join("tls.key"))?,
        ));
    let channel = Channel::from_shared(cfg.endpoint.clone())?
        .tls_config(tls)?
        .connect_timeout(Duration::from_secs(10))
        .connect()
        .await?;
    let listener = tokio::net::TcpListener::bind(("127.0.0.1", cfg.local_port)).await?;
    println!(
        "Private OpenShell preview at http://127.0.0.1:{}/",
        cfg.local_port
    );
    let cfg = Arc::new(cfg);
    let slots = Arc::new(Semaphore::new(32));
    loop {
        let (socket, _) = listener.accept().await?;
        let permit = slots.clone().acquire_owned().await?;
        let c = channel.clone();
        let cfg = cfg.clone();
        tokio::spawn(async move {
            let _permit = permit;
            if let Err(error) = forward(c, cfg, socket).await {
                eprintln!("Preview connection failed: {error}. No tool action was retried.");
            }
        });
    }
}
