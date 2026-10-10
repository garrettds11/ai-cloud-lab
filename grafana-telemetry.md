# Grafana Cloud telemetry

The lab sends host metrics, logs and Open WebUI traces to Grafana Cloud through [Grafana Alloy](https://grafana.com/docs/alloy/latest/) running on the instance. It is **on by default**, so `grafana_otlp_endpoint` and `grafana_credentials_secret_arn` must be set (see below), or set `enable_grafana_telemetry = false` to run without it. Turning it on or off replaces the instance, because the bootstrap script changes.

## What it sends

- **Metrics**, every 60 seconds: CPU, memory, disk, filesystem and network from the host, plus the state of these services: `ollama`, `docker`, `alloy`, the SSM agent and the `ai-lab-*` units.
- **Logs**: the bootstrap log (`/var/log/ai-lab-bootstrap.log`), the journal for `ollama`, `docker` and `alloy`, and the Docker container logs (this includes Open WebUI).
- **Traces and application metrics** from Open WebUI: HTTP routes (FastAPI), database queries, and outbound calls it makes (including to Ollama). Open WebUI exports them over OTLP/gRPC to Alloy on `127.0.0.1:4317`, which listens on loopback only.
- **Not included**: Ollama's own metrics (it has no metrics endpoint here). Service state and container logs are the health signals for Ollama.

Everything leaves the instance over HTTPS (outbound TCP 443, already allowed). No inbound port is opened.

## Set it up

The endpoint, instance ID and access token, and the secret that holds the token, are set up before
the first deploy: [pre-deployment.md, step 7](docs/runbook/pre-deployment.md#7-grafana-cloud). Because
telemetry is on by default, `plan` fails with a clear message until `grafana_otlp_endpoint`,
`grafana_otlp_instance_id` and `grafana_credentials_secret_arn` are set. Turning it on or off replaces
the instance (about 3 minutes). The instance role gets read access to that one secret and nothing
else is widened.

## Check that it works

See [Grafana telemetry test](docs/runbook/smoke-tests/lab-features.md#grafana-telemetry-test).

## Cost and data volume

- Alloy is light, but it adds a small amount of CPU and memory on the instance.
- Grafana Cloud bills or limits by ingested metrics series, log volume and trace volume, and the allowances depend on your plan. Check your plan's current limits before leaving this on.
- Logs are the part that can grow. Container logs include every Open WebUI request line. To cut volume, edit `scripts/alloy-config.alloy` (for example, drop the container source or add a `stage.match` that drops INFO lines).

## Privacy and redaction

- Logs and traces can contain user email addresses, request paths and database statements. Prompts and replies are not intentionally logged by Open WebUI, but check what your version writes before using this with real users.
- Before export, Alloy masks values in **logs** that follow `password`, `passwd`, `secret`, `token`, `authorization` or `api_key`, `Bearer` tokens, and JWT-shaped strings. This is a safety net, not a guarantee, and it does not touch traces. Do not log secrets in the first place.
- Alloy's own usage reporting is disabled.

## Retention

Retention is set by your Grafana Cloud plan, not by this repository. Check it under your stack's settings.

## Teardown

`destroy` (run through `Invoke-TerraformWithCloudflareToken`) removes the instance, so collection stops. These remain until you delete them: the Secrets Manager secret, the Grafana access policy token, and the data already stored in Grafana Cloud. Delete the secret and revoke the token when you no longer need them.

## Rotating the token

Create a new token, update the secret's value, then replace the instance (for example `Invoke-TerraformWithCloudflareToken -Arguments @('apply', '-replace=aws_instance.ai_lab')`). The instance reads the secret only at boot.
