# Sends browser visitors to the control panel when the lab origin is stopped or
# unhealthy. This uses a Cloudflare Worker instead of the Custom Error Rules
# feature, which is not available on Cloudflare's Free plan.
#
# The Worker is attached only to the lab hostname. It forwards the request to
# the ALB normally. If the origin returns a 4xx/5xx response, or the fetch fails
# because the origin is unreachable, browser document requests are redirected to
# the control panel. Non-browser requests (API calls, assets, and websockets)
# keep the origin response/error so the application is not masked by a redirect.

locals {
  lab_unavailable_enabled = var.control_panel_url != null ? local.cloudflare_resources : {}

  lab_unavailable_worker_name = lower("${var.project_name}-lab-fallback")

  lab_unavailable_worker_script = <<-JS
    const CONTROL_PANEL_URL = ${jsonencode("${var.control_panel_url}/")};

    function isBrowserDocument(request) {
      const accept = request.headers.get("Accept") || "";
      return request.method === "GET" && accept.includes("text/html");
    }

    function redirectToControlPanel() {
      return Response.redirect(CONTROL_PANEL_URL, 302);
    }

    export default {
      async fetch(request) {
        try {
          const response = await fetch(request);

          if (isBrowserDocument(request) && (response.status === 400 || response.status >= 500)) {
            return redirectToControlPanel();
          }

          return response;
        } catch (error) {
          if (isBrowserDocument(request)) {
            return redirectToControlPanel();
          }

          return new Response("The lab origin is unavailable.", { status: 503 });
        }
      },
    };
  JS
}

resource "cloudflare_workers_script" "lab_unavailable_page" {
  for_each = local.lab_unavailable_enabled

  account_id  = var.cloudflare_account_id
  script_name = local.lab_unavailable_worker_name
  content     = local.lab_unavailable_worker_script
  main_module = "worker.js"
}

resource "cloudflare_workers_route" "lab_unavailable_page" {
  for_each = local.lab_unavailable_enabled

  zone_id = data.cloudflare_zones.domain[0].result[0].id
  pattern = "${var.domain_name}/*"
  script  = cloudflare_workers_script.lab_unavailable_page[each.key].script_name
}
