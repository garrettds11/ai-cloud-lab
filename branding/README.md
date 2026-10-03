# Branding

Files here control how the sign-in experience looks. Edit them and run
`terraform apply`; no other changes are needed.

| File | What it controls |
|---|---|
| `logo.png` | Logo on the Cognito sign-in page. This is the Open WebUI mark (`static/favicon.png` from https://github.com/open-webui/open-webui). Keep it under 100 KB. |
| `cognito.css` | Colors and spacing on the Cognito sign-in page. Cognito accepts only a fixed set of classes and properties, and the file must stay under 3 KB. |

The security and acceptable use banner is **not** in this folder. It is the
`security_banner_text` variable in `terraform.tfvars`, and it appears at the top
of Open WebUI after sign-in. Cognito's classic hosted UI cannot show custom
text, so the banner cannot appear on the Cognito page itself.

## Open WebUI license

Open WebUI's license requires its branding to stay visible and not be altered,
removed, or co-branded unless you have 50 or fewer users in a 30-day period or
an enterprise license. See https://docs.openwebui.com/license/. This repo keeps
the Open WebUI name and mark as they are.
