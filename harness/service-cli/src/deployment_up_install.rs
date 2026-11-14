//! Shared service/OpenShell rendering and private credential staging.
use super::*;
impl Native<'_> {
    pub(super) async fn tool(
        &self,
        program: &str,
        args: Vec<String>,
        input: Option<Vec<u8>>,
    ) -> Result<Vec<u8>> {
        super::super::super::local::exec_with_env(
            Path::new(program),
            &args,
            input,
            &self.environment()?,
        )
        .await
    }
    pub(super) async fn images(&self) -> Result<Observation> {
        if self.provider() != "aws" {
            return self.azure_images().await;
        }
        // Recover journals written by an older coordinator which completed the
        // outer stage from a read-only status without advancing native receipts.
        let journal = self.document(&self.cloud_state().join("cloud-journal.json"))?;
        if journal["phase"] != "infrastructure_ready" {
            let reconciled = self.cloud_apply("foundation").await?;
            ensure!(
                reconciled["phase"] == "infrastructure_ready",
                "Original AWS infrastructure receipts are not ready for image publication"
            );
        }
        let mut receipts = BTreeMap::<String, Value>::new();
        for (role, source) in [
            ("harness", Some(&self.package.release.images.harness)),
            ("runner", Some(&self.package.release.images.runner)),
            (
                "service-ui",
                self.package.release.images.service_ui.as_ref(),
            ),
        ] {
            let Some(source) = source else { continue };
            // A local content-addressed pull is repeatable; the native publisher
            // separately reconciles the original registry mutation and never replays it.
            self.tool(
                "docker",
                strings(&["pull", "--platform", "linux/arm64", source]),
                None,
            )
            .await?;
            let mut a = strings(&["deploy", "aws", "lifecycle", "publish-image", "--state"]);
            a.extend([
                arg(&self.cloud_state())?,
                "--profile".into(),
                self.aws_profile()?,
                "--role".into(),
                role.into(),
                "--source".into(),
                source.clone(),
                "--allow-experimental-cloud".into(),
            ]);
            receipts.insert(role.into(), self.command(a).await?);
            write_private(&self.root.join("published-images.json"), &receipts)?;
        }
        Ok(Observation::Complete(json!(receipts)))
    }
    pub(super) async fn azure_material(&self) -> Result<Value> {
        let mut a = strings(&["deploy", "azure", "installation", "material", "--state"]);
        a.push(arg(&self.cloud_state())?);
        self.command(a).await
    }
    pub(super) async fn render_installation(&self) -> Result<()> {
        let c = &self.review.config;
        let (plan, _) = self.plan()?;
        let images = self.document(&self.root.join("published-images.json"))?;
        let harness = text(&images["harness"], "image")?;
        let runner = text(&images["runner"], "image")?;
        let fullname = "ow-gateway";
        let ns = "ow-openshell";
        let endpoint = format!("https://{fullname}.{ns}.svc.cluster.local:8080");
        let artifacts = if self.provider() == "aws" {
            let status = self.cloud_status().await?;
            let outputs = &status["observed"]["infra"]["outputs"];
            json!({"kind":"s3","deployment":plan.deployment_id,"bucket":outputs["ArtifactBucket"],"region":c.cloud.region()})
        } else {
            let m = self.azure_material().await?;
            json!({"kind":"azure","deployment":plan.deployment_id,"account":m["artifactAccount"],"container":"artifacts","tenant_id":m["target"]["tenant"],"client_id":m["artifactIdentity"]["properties"]["clientId"],"federated_token_file":"/var/run/secrets/azure/tokens/azure-identity-token"})
        };
        let provider = c
            .models
            .keys()
            .next()
            .map(String::as_str)
            .unwrap_or("openai");
        let model = match provider {
            "anthropic" => "claude-sonnet-4-6",
            _ => "unconfigured",
        };
        let connections:Vec<Value>=c.models.keys().enumerate().map(|(i,p)|{
            let id=if i==0{"primary"}else{p.as_str()};
            json!({"id":id,"provider":p,"identity":format!("{}-{id}",plan.deployment_id),"key_env":if p=="anthropic"{"ANTHROPIC_API_KEY"}else{"OPENAI_API_KEY"}})
        }).collect();
        let mut values = json!({"image":harness,"apiReplicas":1,"worker":{"replicas":1,"ownership":"manual","maxReplicas":2,"slots":2,"drainSeconds":30},"terminationGracePeriodSeconds":90,"secrets":{"model":if c.models.is_empty(){""}else{"ow-service-model"}},"config":{"service":{"id":plan.deployment_id,"namespace":"local","agent":"installed-tools","legacy_compatibility":false},"database_env":"OW_PG_URL","schema":"ow_service","database_loopback_plaintext":false,"database_ca":"/run/service/pg-ca.crt","state_root":"/state","bundle":"/opt/openworker/bundle","runner":"/opt/openworker/openworker","model":model,"scripted":false,"model_provider":provider,"model_connections":connections,"api_token_file":"/bootstrap/admin-token","actor":"deployment-admin","openshell":{"identity":plan.deployment_id,"endpoint":endpoint,"tls_server_name":format!("{fullname}.{ns}.svc.cluster.local"),"certificates":"/openshell","workspace":"default","image":runner},"artifacts":artifacts}});
        if let Some(ui) = &c.management_ui {
            super::super::super::local::features::configure_ui(
                &mut values,
                ui,
                images["service-ui"]["image"].as_str(),
                "ows",
                "ow-service",
            )?;
        } else if let Some(image) = images["service-ui"]["image"].as_str() {
            values["managementUI"] = json!({"enabled":false,"image":image});
        }
        if let Some(obs) = &c.observability {
            super::super::super::local::features::configure_observability(&mut values, obs)?;
        }
        self.exact_file("service-values.json", &values)?;
        let mut shell: Value =
            serde_json::from_slice(&fs::read(&self.package.release.openshell_values.file)?)?;
        shell["fullnameOverride"] = json!(fullname);
        shell["sandbox"]["image"] = json!({"repository":runner.split('@').next().unwrap(),"digest":runner.split('@').nth(1).unwrap()});
        shell["server"]["workspaceStorageClass"] = json!(if self.provider() == "aws" {
            "ows-workspaces"
        } else {
            "managed-csi"
        });
        shell["pkiInitJob"]["serverDnsNames"] = json!([
            format!("{fullname}.{ns}.svc.cluster.local"),
            format!("{fullname}.{ns}.svc")
        ]);
        self.exact_file("openshell-values.json", &shell)?;
        let platform = if self.provider() == "aws" {
            let path = self.root.join("platform.json");
            if !path.exists() {
                let mut args =
                    strings(&["deploy", "aws", "lifecycle", "platform-artifact", "--plan"]);
                args.extend([
                    arg(&self.plan_root())?,
                    "--out".into(),
                    arg(&path)?,
                    "--openshell-namespace".into(),
                    ns.into(),
                    "--openshell-release".into(),
                    "ow-shell".into(),
                ]);
                self.command(args).await?;
            }
            json!({"file":path,"sha256":hash(&fs::read(&path)?)})
        } else {
            serde_json::to_value(&self.package.release.platform_manifest)?
        };
        let config = json!({"schemaVersion":1,"kubeconfig":self.root.join("handoff/kubeconfig.json"),"context":"pending-owned-handoff","helm":"helm","service":{"namespace":"ow-service","release":"ows","chart":self.package.release.service_chart,"valuesFile":self.root.join("service-values.json"),"fixtureDatabase":false},"openshell":{"mode":"install","namespace":ns,"release":"ow-shell","fullname":fullname,"archive":self.package.release.openshell_archive.file,"valuesFile":self.root.join("openshell-values.json")},"platform":platform});
        self.exact_file("installation-input.json", &config)?;
        Ok(())
    }
    fn exact_file(&self, name: &str, value: &Value) -> Result<()> {
        let p = self.root.join(name);
        if p.exists() {
            ensure!(
                self.document(&p)? == *value,
                "Generated installer input changed; retain the original handoff"
            );
        } else {
            write_private(&p, value)?;
        }
        Ok(())
    }
    pub(super) async fn handoff(&self) -> Result<Observation> {
        self.render_installation().await?;
        let path = self.root.join("handoff");
        let name = if self.provider() == "aws" {
            "installer-config.json"
        } else {
            "cluster-install.json"
        };
        if !path.join(name).exists() {
            ensure!(!path.exists(),"Partial installation handoff; inspect the original native receipt before continuing");
            let mut a = if self.provider() == "aws" {
                strings(&["deploy", "aws", "lifecycle", "installation"])
            } else {
                strings(&["deploy", "azure", "installation", "prepare"])
            };
            a.extend([
                "--state".into(),
                arg(&self.cloud_state())?,
                "--config".into(),
                arg(&self.root.join("installation-input.json"))?,
                "--out".into(),
                arg(&path)?,
            ]);
            if self.provider() == "aws" {
                a.extend(["--profile".into(), self.aws_profile()?]);
            }
            self.command(a).await?;
        }
        self.connect_private(&path.join("kubeconfig.json")).await?;
        if !self.install_state().exists() {
            let mut args = strings(&["deploy", "cluster", "plan", "--config"]);
            args.extend([
                arg(&path.join(name))?,
                "--out".into(),
                arg(&self.install_state())?,
            ]);
            self.command(args).await?;
        }
        Ok(Observation::Complete(
            json!({"installerState":self.install_state(),"managementApi":"private"}),
        ))
    }
    pub(super) async fn secrets(&self) -> Result<Observation> {
        let secret_dir = self.root.join("credentials");
        directory(&secret_dir)?;
        let cert = secret_dir.join("api.crt");
        let key = secret_dir.join("api.key");
        if !cert.exists() || !key.exists() {
            ensure!(
                !cert.exists() && !key.exists(),
                "Partial TLS generation; inspect private credential files before continuing"
            );
            self.tool("openssl",vec!["req".into(),"-x509".into(),"-newkey".into(),"rsa:3072".into(),"-nodes".into(),"-days".into(),"30".into(),"-subj".into(),"/CN=ows-api.ow-service.svc.cluster.local".into(),"-addext".into(),"basicConstraints=critical,CA:TRUE".into(),"-addext".into(),"keyUsage=critical,digitalSignature,keyCertSign,cRLSign".into(),"-addext".into(),"extendedKeyUsage=serverAuth".into(),"-addext".into(),"subjectAltName=DNS:localhost,IP:127.0.0.1,DNS:ows-api.ow-service.svc.cluster.local,DNS:ows-api.ow-service.svc".into(),"-keyout".into(),arg(&key)?,"-out".into(),arg(&cert)?],None).await?;
        }
        #[cfg(unix)]
        {
            use std::os::unix::fs::PermissionsExt;
            fs::set_permissions(&key, fs::Permissions::from_mode(0o600))?;
        }
        let mut model = serde_json::Map::new();
        for (name, reference) in &self.review.config.models {
            let value = if let Some(env) = &reference.env {
                std::env::var(env).with_context(|| {
                    format!("Missing model credential environment variable {env}")
                })?
            } else {
                fs::read_to_string(reference.file.as_ref().unwrap())
                    .context("Read private model credential file")?
            };
            let value = value.trim();
            ensure!(
                !value.is_empty() && value.len() <= 32768 && !value.contains(['\r', '\n']),
                "Invalid model credential format"
            );
            model.insert(name.clone(), json!(value));
        }
        let mut input = json!({"database":{"pg-ca.crt":fs::read_to_string(&self.package.release.database_ca.file)?},"apiTLS":{"tls.crt":fs::read_to_string(&cert)?,"tls.key":fs::read_to_string(&key)?},"model":model});
        if self.review.config.models.is_empty() {
            input["model"] = Value::Null;
        }
        if self.provider() == "azure" {
            input["database"]["database-url"] = json!(self.azure_database_url().await?.as_str());
        }
        let path = secret_dir.join("installation-secrets.json");
        write_private(&path, &input)?;
        let native = self.document(&self.install_state().join("local-plan.json"))?;
        let mut a = strings(&["deploy", "cluster", "secrets", "--state"]);
        a.extend([
            arg(&self.install_state())?,
            "--accept-plan".into(),
            text(&native, "digest")?,
            "--input".into(),
            arg(&path)?,
        ]);
        let result = self.command(a).await?;
        Ok(Observation::Complete(result))
    }
    pub(super) async fn verify(&self) -> Result<Observation> {
        let result = self.installer("status", &[]).await?;
        let kube = self.root.join("handoff/kubeconfig.json");
        let mut components = vec!["api", "worker", "controller", "platform"];
        if self
            .review
            .config
            .management_ui
            .as_ref()
            .is_some_and(|ui| ui.enabled)
        {
            components.push("ui");
        }
        for component in components {
            self.tool(
                "kubectl",
                vec![
                    "--kubeconfig".into(),
                    arg(&kube)?,
                    "-n".into(),
                    "ow-service".into(),
                    "rollout".into(),
                    "status".into(),
                    format!("deployment/ows-{component}"),
                    "--timeout=180s".into(),
                ],
                None,
            )
            .await?;
        }
        let mut connection = json!({"deploymentId":self.review.deployment_id,"serviceId":self.review.deployment_id,"namespace":"local","url":"https://ows-api.ow-service.svc.cluster.local","access":"private cluster network","kubeconfig":kube,"caFile":self.root.join("credentials/api.crt"),"tokenFile":self.install_state().join("bootstrap-token"),"privatePreview":{"command":"kubectl","arguments":["--kubeconfig",arg(&kube)?,"-n","ow-service","port-forward","service/ows-api","8443:443","--address=127.0.0.1"],"url":"https://localhost:8443"},"modelSmokeTest":"not_run"});
        if let Some(ui) = self
            .review
            .config
            .management_ui
            .as_ref()
            .filter(|ui| ui.enabled)
        {
            connection["managementUI"] =
                json!({"url":ui.browser_origin()?,"login":"existing service token"});
            let url = reqwest::Url::parse(&ui.browser_origin()?)?;
            if matches!(url.host_str(), Some("localhost" | "127.0.0.1")) {
                connection["managementUI"]["privatePreview"] = json!({"command":"kubectl","arguments":["--kubeconfig",arg(&kube)?,"-n","ow-service","port-forward","service/ows-ui",format!("{}:443",url.port_or_known_default().unwrap()),"--address=127.0.0.1"],"caFile":self.root.join("credentials/api.crt")});
            }
        }
        connection["observability"] = json!({"enabled":self.review.config.observability.as_ref().is_some_and(|obs|obs.enabled)});
        write_private(&self.root.join("connection.json"), &connection)?;
        Ok(Observation::Complete(
            json!({"nativeStatus":result,"rolloutsReady":true,"modelSmokeTest":"not_run","connectionFile":self.root.join("connection.json")}),
        ))
    }
}
