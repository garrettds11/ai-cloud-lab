# Sends people to the control panel when the lab will not load.
#
# When the lab instance is stopped (or unhealthy), opening the lab's address in a browser
# ends in a 5xx from the load balancer or Cloudflare, or occasionally a 400. This rule
# swaps that error for a short page with a button to the control panel, where they can
# start the lab. The page keeps the original status code.
#
# Only browser page loads are changed (requests that accept HTML). The lab's own background
# calls, and ordinary 401, 403 and 404 responses, are left alone so the app keeps working.
# The rule applies only to the lab's host name, not to the rest of the zone.
#
# A zone has one custom error ruleset. If you already have rules in the zone's custom error
# phase, move them into this resource or leave control_panel_url unset.

locals {
  lab_unavailable_enabled = var.control_panel_url != null ? local.cloudflare_resources : {}

  lab_unavailable_html = <<-HTML
    <!DOCTYPE html>
    <html lang="en">
    <head>
      <meta charset="utf-8">
      <meta name="viewport" content="width=device-width, initial-scale=1">
      <title>The lab is not available</title>
      <style>
        body { margin: 0; min-height: 100vh; display: grid; place-items: center; background: #1a1d21; color: #e8ebee; font: 16px/1.5 "Segoe UI", system-ui, sans-serif; }
        main { max-width: 440px; padding: 32px 24px; text-align: center; }
        h1 { margin: 0 0 12px; font-size: 24px; }
        p { margin: 0 0 24px; color: #a3adb7; }
        a { display: inline-block; padding: 10px 20px; border-radius: 6px; background: #2f6df0; color: #fff; font-weight: 600; text-decoration: none; }
        a:hover { background: #4a82f5; }
      </style>
    </head>
    <body>
      <main>
        <h1>The lab is not available right now</h1>
        <p>It may be stopped. Open the control panel to check it and start it, then come back in a few minutes.</p>
        <a href="${var.control_panel_url}/">Open the control panel</a>
      </main>
    </body>
    </html>
  HTML
}

resource "cloudflare_ruleset" "lab_unavailable_page" {
  for_each = local.lab_unavailable_enabled

  zone_id     = data.cloudflare_zones.domain[0].result[0].id
  name        = "${var.project_name} lab unavailable page"
  description = "Sends people to the control panel when the lab does not load. Managed by Terraform."
  kind        = "zone"
  phase       = "http_custom_errors"

  rules = [{
    ref         = "lab_unavailable_to_control_panel"
    description = "Lab page loads that fail with 400 or 5xx"
    action      = "serve_error"
    enabled     = true
    expression  = "(http.host eq \"${var.domain_name}\" and (http.response.code eq 400 or (http.response.code ge 500 and http.response.code lt 600)) and any(http.request.headers[\"accept\"][*] contains \"text/html\"))"
    action_parameters = {
      content      = local.lab_unavailable_html
      content_type = "text/html"
    }
  }]
}
