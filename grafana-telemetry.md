# Grafana Cloud telemetry

The lab can send host metrics and logs to Grafana Cloud through [Grafana Alloy](https://grafana.com/docs/alloy/latest/) running on the instance. It is **off by default**. Turning it on or off replaces the instance, because the bootstrap script changes.

## What it sends

- **Metrics**, every 60 seconds: CPU, memory, disk, filesystem and network from the host, plus the state of these services: `ollama`, `docker`, `alloy`, the SSM agent and the `ai-lab-*` units.
- **Logs**: the bootstrap log (`/var/log/ai-lab-bootstrap.log`), the journal for `ollama`, `docker` and `alloy`, and the Docker container logs (this includes Open WebUI).
- **Not included**: traces, and Open WebUI or Ollama application metrics (neither exposes a metrics endpoint in this setup). Service state and container logs are the health signals.

Everything leaves the instance over HTTPS (outbound TCP 443, already allowed). No inbound port is opened.

## What you need from Grafana Cloud

1. The **OTLP endpoint**, for example `https://otlp-gateway-prod-us-east-3.grafana.net/otlp`. It is not a secret.
2. The **OTLP instance ID** (a number).
3. An **access policy token** with only the `metrics:write` and `logs:write` scopes.

   The "Create an API token" dialog on the OTLP Endpoint page uses a predefined policy (`stack-<id>-otlp-write`) that also grants `alerts:write`, `rules:write`, `traces:write`, `profiles:write`, `metrics:import` and `datadog:validate`. Those are more than the lab needs. Instead, create your own policy under Administration > Users and access > Cloud access policies, give it only `metrics:write` and `logs:write` for your stack, and add a token to that policy. Set an expiry and note the date, because the telemetry stops when the token expires.

## Store the credentials in Secrets Manager

The instance reads one secret at boot, so the token never appears in Terraform state, user-data, or the repository. Run this in PowerShell (use your own instance ID, region and secret name). It prompts for the token without echoing it:

```powershell
$sec   = Read-Host "Grafana token" -AsSecureString
$token = [Runtime.InteropServices.Marshal]::PtrToStringAuto([Runtime.InteropServices.Marshal]::SecureStringToBSTR($sec))
$file  = New-TemporaryFile
[IO.File]::WriteAllText($file.FullName, (@{ instance_id = "<instance-id>"; token = $token } | ConvertTo-Json -Compress))
aws secretsmanager create-secret --name aiwebdemo/grafana-otlp --secret-string "file://$($file.FullName)" --region us-east-1 --query ARN --output text
Remove-Item $file.FullName
Remove-Variable token, sec
```

The secret is JSON with two keys: `instance_id` and `token`. The command prints the secret's ARN.

## Turn it on

In `terraform.tfvars`:

```hcl
enable_grafana_telemetry       = true
grafana_otlp_endpoint          = "https://otlp-gateway-prod-us-east-3.grafana.net/otlp"
grafana_credentials_secret_arn = "<the ARN printed above>"
```

Then run `terraform plan` and `apply` as usual. The instance is replaced (about 3 minutes). The instance role gets read access to that one secret and nothing else is widened.

If telemetry setup fails during boot, bootstrap still finishes and the lab works. The bootstrap log says `WARNING: Grafana telemetry setup failed`.

## Check that it works

See "Grafana telemetry test" in the smoke test plan.

## Cost and data volume

- Alloy is light, but it adds a small amount of CPU and memory on the instance.
- Grafana Cloud bills or limits by ingested metrics series and log volume, and the allowances depend on your plan. Check your plan's current limits before leaving this on.
- Logs are the part that can grow. Container logs include every Open WebUI request line. To cut volume, edit `scripts/alloy-config.alloy` (for example, drop the container source or add a `stage.match` that drops INFO lines).

## Privacy and redaction

- Logs can contain user email addresses and request paths. Prompts and replies are not intentionally logged by Open WebUI, but check what your version writes before enabling this for real users.
- Before export, Alloy masks values that follow `password`, `passwd`, `secret`, `token`, `authorization` or `api_key`, `Bearer` tokens, and JWT-shaped strings. This is a safety net, not a guarantee. Do not log secrets in the first place.
- Alloy's own usage reporting is disabled.

## Retention

Retention is set by your Grafana Cloud plan, not by this repository. Check it under your stack's settings.

## Teardown

`terraform destroy` removes the instance, so collection stops. These remain until you delete them: the Secrets Manager secret, the Grafana access policy token, and the data already stored in Grafana Cloud. Delete the secret and revoke the token when you no longer need them.

## Rotating the token

Create a new token, update the secret's `token` value, then replace the instance (for example `terraform apply -replace=aws_instance.ai_lab`). The instance reads the secret only at boot.
