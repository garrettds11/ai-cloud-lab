# Agent instructions for ai-cloud-lab

These are the working rules for any coding agent (Codex, Claude or others) in this
repository. This file is the source of truth: if an agent's own memory or notes disagree
with it, this file wins. Each rule states where it applies and why. When a rule's reason
does not fit the situation in front of you, ask instead of applying it.

## Working copy and git

- Work in the local checkout (`C:\GitHub\ai-cloud-lab`), not on GitHub or a remote copy.
  The owner reviews changes in GitHub Desktop.
- Do not commit, push, merge, reset or rewrite branches unless the owner explicitly asks
  for that action in the current conversation. Other agents may be editing the same
  checkout, so inspect `git status` first and preserve changes you did not make.
- Keep unrelated work in separate commits and branches. Feature work branches from `dev`.
- If you push, say so in the first line of your reply, with the commit hash.
- For each uncommitted change, offer a commit comment sized to the change: a Summary line,
  plus a Description only when the change needs one.
- Never add skip-build markers (such as `[skip ci]` or `[skip-cd]`) to commit messages.

## Commands

- The owner runs Windows. Give commands in PowerShell, not bash.
- Commands that create, change or delete AWS resources are run by the owner. Say what a
  command will change before giving it, and never create resources that were not agreed.

## Terraform roots

Each root has its own state. Set `AWS_PROFILE` and `AWS_DEFAULT_REGION` to the lab account
first, as in Step 1 of [the Terraform smoke test plan](docs/runbook/terraform-smoke-test-plan.md).
The owner's default AWS profile is a different account, so never rely on it.

| Root | Providers | How to run Terraform | Why |
|---|---|---|---|
| Repository root (the lab) | AWS, Cloudflare | `Invoke-TerraformWithCloudflareToken -Arguments @(...)`, defined in Step 1 of the smoke test plan and loaded per PowerShell window | The Cloudflare provider needs `CLOUDFLARE_API_TOKEN`; the function reads it from Secrets Manager per command and clears it afterwards |
| `scanner/` (Greenbone) | AWS, archive | plain `terraform` | No Cloudflare provider, so no token is needed |
| `logging/<track>/` | AWS | plain `terraform` | No Cloudflare provider, so no token is needed |

Before using the Cloudflare wrapper anywhere new, check that root's `versions.tf` actually
requires the Cloudflare provider. Terraform run from provisioners or scripts must also use
the profile set above.

## Configuration files

- Repository root: `terraform.tfvars.example` is the source of truth and is committed with
  the owner's real values (account ARNs, domain). Every run copies it over the git-ignored
  `terraform.tfvars`, so the two must stay identical. Make any config change in the example
  first, then make `terraform.tfvars` match. Do not scrub real values or remove defaults:
  cloners replace them with their own (see `cloudflare-and-domain-requirements.md`).
- Make deployment-specific values (project name, tags and similar) variables in tfvars,
  not hard-coded, so others can clone and run the lab. Resources carry the
  `project=aiwebdemo` tag.
- `logging/<track>/terraform.tfvars` holds per-deployment resource IDs from the root
  outputs and stays git-ignored; only the `.example` files are committed.
- Never put a token, password or private key in any committed file.

## Security assessment

- Do not loosen security gates, add CVE exclusions or suppressions, or accept risk without
  the owner's explicit approval. Report-only records (such as `security/source-fixes.json`)
  must not change the blocking count.
- The lab does not patch downstream software. A HIGH/CRITICAL CVE with no vendor fixed
  version is accepted by owner policy. One whose fix the vendor has not shipped needs an
  exact-digest, expiring owner record in `security/accepted-risk.json`; add one only with
  the owner's approval. Do not build patched packages from source to clear findings.
- Keep asset counts, finding occurrences, distinct IDs, verified source fixes and accepted
  risk separate in reports and docs. Do not claim a fix from a package upgrade alone.
- Verify current upstream advisories from primary sources before stating fix status.
- Feed test-data secret reviews apply only to exact reviewed files: matching path, rule,
  line and full-file SHA256 (see `security/feed-secret-review.md`).

## Documentation

- Markdown only, no HTML in Markdown files. `==highlight==` is fine.
- The smoke test plan uses `## Step N. Title` headings. Keep that format.
- Every step the owner must do by hand, including after-apply steps, goes into the smoke
  test docs.
- Do not list the AWS CLI, Terraform or Git as prerequisites in docs; they are assumed.
- The smoke test is a playbook for standing up the lab and its components, not a test of
  everything.

## Project context

- The lab is an internal, per-deployment demo system, not a public service.
- Cloudflare integration may be dropped in a later release. Keep Cloudflare-specific pieces
  isolated and easy to remove.
- Vulnerability data lives in a separate repository (`C:\GitHub\sec-data`); all MCP server
  and tool code belongs in this repository.

## Communication

- Keep replies short. Put any question for the owner on its own line, stated plainly.
- Say what you are about to do before doing it. If you cannot do something that was asked,
  say so up front.
