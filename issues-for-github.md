# Issues for GitHub

Draft issues from the repo assessment. Each heading is an issue title; each code block is the issue body.
Suggested labels are listed in the first line of each body.

---

# Security

## Move Open WebUI admin password out of user-data and remove the public default

```markdown
Labels: security, priority:high

### Problem
The admin password is passed through `templatefile()` into EC2 user-data (`main.tf`, `cloud-init.sh.tpl`).
`sensitive = true` only hides it from plan output. The value is still:

- Readable via `ec2:DescribeInstanceAttribute` (userData)
- Visible in `docker inspect open-webui` (`WEBUI_ADMIN_PASSWORD` env var)
- Stored in plaintext in Terraform state

The default (`ChangeMeBeforeUse123!`) is public in this repo, so anyone who deploys with defaults has a known credential.

Also, `WEBUI_ADMIN_PASSWORD` only applies on first start with an empty DB. Changing the variable later replaces the
instance (`user_data_replace_on_change = true`) and wipes the Docker volume, so the "change it later" guidance in the
README is misleading.

### Proposed fix
- Remove the default, or generate one with `random_password`.
- Store it as an SSM Parameter Store SecureString (or Secrets Manager secret).
- Grant the instance role read access to that one parameter and fetch it at boot instead of embedding it.
- Output the parameter name (not the value) and document how to retrieve it.
- Update the "Secure Admin Password" README section.

### Acceptance criteria
- [ ] Password does not appear in user-data (verify with `aws ec2 describe-instance-attribute --attribute userData`)
- [ ] No weak default shipped in `variables.tf`
- [ ] README documents retrieval and rotation
```

## Restrict security group egress

```markdown
Labels: security, priority:medium

### Problem
The security group allows all outbound traffic (`protocol = "-1"`, `0.0.0.0/0`). The lab only needs HTTPS/HTTP for
apt, Docker image pulls, the Ollama installer and model downloads.

### Proposed fix
- Limit egress to TCP 443 and TCP 80 (and UDP/TCP 53 if resolver traffic needs it).
- Optionally add a variable to supply additional allowed egress CIDRs.

### Acceptance criteria
- [ ] Bootstrap still completes with restricted egress
- [ ] Lab instance cannot make arbitrary outbound connections on other ports
```

## Offer a private-subnet deployment with VPC endpoints (no public IP)

```markdown
Labels: security, enhancement, priority:medium

### Problem
The instance gets a public IP (`associate_public_ip_address = true`) purely for outbound access during bootstrap.
The README says "no public inbound access", which is true, but the public IP is unnecessary exposure surface and
the output `public_ip` implies it is useful.

### Proposed fix
- Add an optional dedicated VPC (or use a supplied subnet) with a private subnet.
- Add interface endpoints for `ssm`, `ssmmessages`, `ec2messages` and a NAT gateway or egress proxy for package/model
  downloads (or pre-baked AMI, see separate issue).
- Make this switchable via a variable (e.g. `network_mode = "default_vpc" | "private"`).

### Acceptance criteria
- [ ] SSM port forwarding works with no public IP
- [ ] Documented cost trade-off (NAT gateway / endpoint hourly charges)
```

## Pin container image, Ollama version and stop running apt upgrade at boot

```markdown
Labels: security, reproducibility, priority:medium

### Problem
The bootstrap is not reproducible and has several unpinned supply-chain inputs:

- `ghcr.io/open-webui/open-webui:main` is a floating tag
- `curl -fsSL https://ollama.com/install.sh | sh` installs whatever is current
- `apt-get upgrade -y` runs at every boot of a fresh instance
- Ubuntu AMI uses the `current` SSM parameter, so the base image changes between applies

Two applies months apart produce different labs, and a compromised upstream would be executed as root.

### Proposed fix
- Default `open_webui_container_image` to a version tag or digest.
- Add `ollama_version` variable and pass `OLLAMA_VERSION=...` to the installer.
- Drop `apt-get upgrade -y` (or make it opt-in).
- Document how to deliberately bump versions.

### Acceptance criteria
- [ ] Defaults are pinned
- [ ] README explains upgrade procedure
```

## Remove ubuntu user from the docker group

```markdown
Labels: security, priority:low

### Problem
`usermod -aG docker ubuntu` gives the `ubuntu` user root-equivalent access. The lab is headless and administered via
SSM (which runs as root / ssm-user), so the membership isn't needed.

### Proposed fix
Remove the `usermod` line, or make it opt-in via a variable.

### Acceptance criteria
- [ ] Bootstrap and README commands still work without docker group membership
```

## Use an encrypted remote backend for Terraform state

```markdown
Labels: security, hygiene, priority:medium

### Problem
State is local (a `terraform.tfstate` exists in the working directory). State contains the admin password and other
sensitive values. It is git-ignored (good), but it is unencrypted on disk, unlocked, and easy to lose.

### Proposed fix
- Document (and optionally provide a bootstrap snippet for) an S3 backend with encryption, versioning and locking
  (S3 native lockfile or DynamoDB).
- Add a commented-out `backend "s3"` block to `main.tf` or a `backend.tf.example`.

### Acceptance criteria
- [ ] README has a "State" section
- [ ] Example backend config provided
```

---

# Bugs and correctness

## Default aws_profile of "CHANGEME" breaks env-var and SSO credential flows

```markdown
Labels: bug, priority:medium

### Problem
`variables.tf` defaults `aws_profile` to `"CHANGEME"` and the provider always sets `profile = var.aws_profile`.
Anyone using environment credentials, SSO via default profile, or an instance/CI role gets a
"failed to get shared config profile" error unless they override it. The outputs also print `--profile CHANGEME`.

### Proposed fix
- Default `aws_profile` to `null`.
- Build the `--profile` flag in outputs conditionally (omit it when null).

### Acceptance criteria
- [ ] `terraform plan` works with only `AWS_ACCESS_KEY_ID`/`AWS_SECRET_ACCESS_KEY` set
- [ ] Output commands omit `--profile` when no profile is set
```

## Subnet selection may choose an AZ that does not offer the chosen instance type

```markdown
Labels: bug, priority:medium

### Problem
`main.tf` uses `sort(data.aws_subnets.default.ids)[0]`. Not every AZ offers every instance type (e.g. `c7i.4xlarge`),
so apply can fail with "Unsupported instance type in AZ" depending on the account/region.

### Proposed fix
- Use `aws_ec2_instance_type_offerings` filtered by `location_type = "availability-zone"`, then choose a default-VPC
  subnet in a supporting AZ.
- Alternatively expose a `subnet_id` variable override.

### Acceptance criteria
- [ ] Apply succeeds in regions where the first sorted subnet's AZ lacks the instance type
```

## Terraform apply returns before the lab is usable; no readiness signal

```markdown
Labels: bug, usability, priority:medium

### Problem
`terraform apply` completes as soon as the instance is running. Bootstrap (apt upgrade, Docker, Ollama install,
model pull, Open WebUI image pull) takes several minutes more. Nothing in Terraform or the README says how long, or how
to check, so users hit connection errors on the first tunnel attempt.

### Proposed fix
- Document expected bootstrap duration and how to tail `/var/log/ai-lab-bootstrap.log` via SSM.
- Add a readiness check: either a helper script/output (`aws ssm send-command ... ai-lab-status`) or a
  `terraform_data` provisioner that waits for a marker file.
- Have cloud-init write a `/var/lib/ai-lab/ready` marker on success and a failure marker on error.

### Acceptance criteria
- [ ] README has a "Wait for bootstrap" section
- [ ] A single command reports ready / not ready / failed
```

## Remove unused data source and unnecessary sudo in bootstrap

```markdown
Labels: cleanup, priority:low

### Problem
- `data "aws_caller_identity" "current"` in `main.tf` is never referenced.
- `sudo -H -u ubuntu env OLLAMA_HOST=... ollama pull` in `cloud-init.sh.tpl` is unnecessary; `ollama pull` only
  talks to the local server API and can run as root with `OLLAMA_HOST` set.

### Proposed fix
Delete the unused data source and simplify the pull command.

### Acceptance criteria
- [ ] `terraform validate` still passes
- [ ] Model pull still succeeds during bootstrap
```

## Bootstrap should fail loudly and not rely on `|| true` in critical paths

```markdown
Labels: bug, robustness, priority:low

### Problem
`ensure_ssm_agent` swallows failures with `|| true`, so an instance can finish bootstrap with no working SSM agent.
Since SSM is the only access path by default, the instance becomes unreachable with no indication why.

### Proposed fix
- After `ensure_ssm_agent`, verify the agent is active and fail (and log) if not.
- Write a failure marker consumed by the readiness check (see readiness issue).

### Acceptance criteria
- [ ] A broken SSM agent produces a clear failure in the bootstrap log and readiness check
```

---

# Cost and operations

## Add cost guardrails: idle auto-stop, budget alarm, smaller default

```markdown
Labels: enhancement, cost, priority:high

### Problem
Default instance is `c7i.4xlarge` (about $0.70/hr, roughly $500/month if left running) and it is CPU-only, serving a
3B-parameter model. There is no idle shutdown, schedule or budget alert; the only guidance is a manual
`aws ec2 stop-instances` in the README. An instance forgotten over a weekend or month is an expensive mistake.

### Proposed fix
- Optional idle auto-stop: CloudWatch alarm on low CPU / network for N minutes with an EC2 stop action.
- Optional schedule (EventBridge Scheduler) to stop nightly.
- Optional `aws_budgets_budget` with email notification (`budget_limit_usd`, `budget_email` variables).
- Reconsider default instance size (document throughput vs cost for 3B/7B models on CPU).
- Add a Cost section to the README with approximate hourly/monthly figures.

### Acceptance criteria
- [ ] Idle instance stops automatically with default-enabled (or clearly documented opt-in) settings
- [ ] Budget alert resource available behind a variable
- [ ] README cost section added
```

## Persist models and Open WebUI data on a separate EBS volume

```markdown
Labels: enhancement, reliability, priority:medium

### Problem
Open WebUI data (Docker volume) and Ollama models both live on the root disk. Any change to the bootstrap template
replaces the instance (`user_data_replace_on_change = true`), destroying chat history, accounts and downloaded models
and forcing a full re-download.

### Proposed fix
- Add a dedicated `aws_ebs_volume` + attachment, mounted at e.g. `/data`.
- Point `OLLAMA_MODELS` and the Open WebUI volume at it.
- Set `prevent_destroy` or a variable-controlled `delete_on_termination` / snapshot policy.
- Consider switching from replace-on-change to an in-place re-run mechanism (SSM association/document).

### Acceptance criteria
- [ ] Replacing the instance preserves chats and models
- [ ] Documented backup/restore approach (EBS snapshot via DLM)
```

## Ship bootstrap and service logs to CloudWatch

```markdown
Labels: enhancement, observability, priority:low

### Problem
Diagnostics live only in `/var/log/ai-lab-bootstrap.log` and `docker logs`, reachable only if SSM works. If the
agent fails, there is no visibility.

### Proposed fix
- Install the CloudWatch agent (and add `CloudWatchAgentServerPolicy` or a scoped policy) to ship the bootstrap log,
  Ollama journal and Open WebUI container logs.
- Add basic alarms (status check failed, disk usage over threshold, since models fill the disk).

### Acceptance criteria
- [ ] Bootstrap log visible in CloudWatch Logs within a minute of boot
```

## Add default_tags and standardise tagging

```markdown
Labels: hygiene, priority:low

### Problem
Tags are applied by hand on only some resources (the instance profile and role attachment have none; the role has
only `Project`). This hurts cost allocation and cleanup.

### Proposed fix
Use `default_tags` in the AWS provider block (Project, Environment, ManagedBy = "terraform") and remove per-resource
duplication.

### Acceptance criteria
- [ ] All taggable resources carry the standard tags
```

---

# Documentation

## README: remove personal AWS profile name and align examples with tfvars workflow

```markdown
Labels: documentation, priority:low

### Problem
- Examples hardcode a personal profile name (`garrett_gspear`) and repeat long `-var` flag lists.
- `terraform.tfvars.example` already exists but the README never tells users to copy it.
- The README says "no public inbound access" without noting the instance has a public IP (outbound-only).
- No expected bootstrap time, cost estimate or cleanup caveats.

### Proposed fix
- Use `<your-profile>` placeholders.
- Add a "Quick start": `cp terraform.tfvars.example terraform.tfvars`, edit, `terraform apply`.
- Clarify the public-IP wording.
- Add sections: Cost, Bootstrap time and readiness, State handling, Security model and known limitations.

### Acceptance criteria
- [ ] No personal identifiers in docs
- [ ] A new user can deploy using only the Quick start
```

## Add LICENSE and CHANGELOG, and use meaningful commit messages

```markdown
Labels: documentation, hygiene, priority:low

### Problem
- No LICENSE file, so reuse rights are undefined.
- No CHANGELOG.
- Recent history is mostly commits named `.`, which makes it impossible to see what changed or why.

### Proposed fix
- Add a LICENSE (e.g. MIT or Apache-2.0).
- Add a CHANGELOG.md (Keep a Changelog format).
- Adopt short descriptive commit messages (optionally Conventional Commits) and consider branch protection with PRs.

### Acceptance criteria
- [ ] LICENSE and CHANGELOG present
```

---

# CI and quality

## Add CI: terraform fmt, validate, tflint and security scanning

```markdown
Labels: ci, quality, priority:medium

### Problem
There is no automated validation. `terraform validate` and `fmt -check` pass today, but nothing prevents regressions,
and no static security scanner has been run against the config.

### Proposed fix
GitHub Actions workflow on push and PR:
- `terraform fmt -check -recursive`
- `terraform init -backend=false && terraform validate`
- `tflint` (with the AWS ruleset)
- `trivy config` or `checkov` for IaC security scanning
- Render `cloud-init.sh.tpl` via a `terraform console`/test and run `shellcheck` on the output
- Optional: `pre-commit` config with the same hooks

### Acceptance criteria
- [ ] CI runs on every PR and is green on main
- [ ] shellcheck covers the rendered bootstrap script
```

## Add terraform test coverage for variable validation and preconditions

```markdown
Labels: ci, quality, priority:low

### Problem
Validations (`root_volume_size`, `allowed_ssh_cidr`, password length, port range) and the SSH precondition are
untested.

### Proposed fix
Add `tests/*.tftest.hcl` using `terraform test` with mocked providers to assert that:
- `0.0.0.0/0` for SSH is rejected
- `enable_ssh = true` without a key/CIDR fails the precondition
- Short passwords and invalid ports are rejected
- No ingress rule exists by default

### Acceptance criteria
- [ ] `terraform test` runs in CI
```

---

# Feature expansion

## Add GPU instance profile (g5/g6) with NVIDIA driver setup

```markdown
Labels: enhancement, feature, priority:medium

### Problem
CPU-only inference on a 16 vCPU instance is slow for anything larger than small models, and it is expensive for what
it delivers.

### Proposal
- Variable `compute_profile = "cpu" | "gpu"`.
- For GPU: default to a g5/g6 instance, install NVIDIA drivers (or use the Deep Learning Base AMI), and let Ollama
  auto-detect the GPU. Document model-size-to-instance guidance (7B to 70B).
- Pass GPU through to the container only if needed.

### Acceptance criteria
- [ ] `ollama ps` shows GPU offload on a g5/g6 deployment
- [ ] README table of tested instance types and observed tokens/sec
```

## Private RAG lab: embeddings, vector store and document corpus in S3

```markdown
Labels: enhancement, feature, priority:medium

### Proposal
Open WebUI already supports document upload and RAG. Turn that into a first-class use case:
- Pull an embedding model (e.g. `nomic-embed-text`) at bootstrap.
- Configure a persistent vector store (Chroma/pgvector) on the persistent data volume.
- Optional S3 bucket (private, encrypted, VPC-only access via the instance role) with a sync job that ingests
  documents.
- Documented walkthrough: query internal documents without data leaving the VPC.

### Acceptance criteria
- [ ] Documents synced from S3 are queryable in Open WebUI
- [ ] Data stays on encrypted storage
```

## Bedrock hybrid mode: compare local models with managed models

```markdown
Labels: enhancement, feature, priority:low

### Proposal
Optionally route Open WebUI to Amazon Bedrock through a gateway (e.g. LiteLLM or Bedrock Access Gateway) using the
instance IAM role (scoped `bedrock:InvokeModel` for chosen models). Users get local and managed models side by side
in one UI for quality, latency and cost comparison. Note this changes the current `ENABLE_OPENAI_API=false` stance and
the "nothing leaves the instance" claim, so it must be an explicit opt-in with a clear README warning.

### Acceptance criteria
- [ ] Opt-in variable; disabled by default
- [ ] Least-privilege IAM policy limited to listed model IDs
```

## Model benchmark and evaluation harness

```markdown
Labels: enhancement, feature, priority:low

### Proposal
Add a script (run via SSM) that runs a fixed prompt set against a list of models and records:
- tokens/sec, time to first token, total latency
- memory use
- estimated cost per 1k tokens for the instance type

Write results as CSV/JSON to a private S3 bucket and render a summary table in the README. This turns the lab into a
sizing and model-selection tool.

### Acceptance criteria
- [ ] One command benchmarks N models and outputs a comparable report
```

## Pre-baked AMI with Packer for fast, reproducible boots

```markdown
Labels: enhancement, performance, priority:low

### Problem
Every apply repeats apt upgrade, Docker install, Ollama install and model download, which takes minutes and depends on
external networks.

### Proposal
Build a golden AMI with Packer (Docker, Ollama, optional model baked in or on a snapshot volume), and have Terraform
select it by tag. Pairs well with the private-subnet issue because the instance would need little or no outbound access.

### Acceptance criteria
- [ ] Boot-to-ready under 2 minutes with a baked AMI
```

## Reusable Terraform module with examples

```markdown
Labels: enhancement, structure, priority:low

### Proposal
Restructure into `modules/ai-lab` with `examples/basic`, `examples/gpu` and `examples/rag`, plus `outputs`,
`versions.tf` and documented inputs (terraform-docs). Enables reuse and makes the CI/test story cleaner.

### Acceptance criteria
- [ ] Examples deploy successfully
- [ ] terraform-docs generated README for the module
```

## Shared team deployment behind ALB with OIDC/Cognito authentication

```markdown
Labels: enhancement, feature, priority:low, needs-design

### Proposal
For small-team use, put an ALB with Cognito/OIDC authentication in front of Open WebUI, use an ASG with a golden AMI
and persistent EBS data. This intentionally breaks the "no inbound" property, so it needs a written threat model
(WAF, TLS via ACM, auth, rate limiting, logging) before implementation, and must remain an opt-in separate example.

### Acceptance criteria
- [ ] Threat model documented
- [ ] TLS-only, authenticated access with access logging
```

## Agent and tool-use sandbox (MCP servers, function calling) on the private network

```markdown
Labels: enhancement, feature, priority:low, needs-design

### Proposal
Explore running MCP servers and sandboxed code execution alongside Open WebUI so local models can use tools without
sending data off the VPC. Needs isolation design (separate containers/users, no instance-role credentials exposed to
tool runtimes, egress controls).

### Acceptance criteria
- [ ] Design doc covering isolation and credential exposure
- [ ] Reference example with at least one safe tool
```
