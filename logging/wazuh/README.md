# Wazuh track

This track uses the Wazuh Agent on the root Ubuntu host for enrollment, log analysis, security alerts, and host security monitoring. A second Vector package writes the common host and Open WebUI container event feed to this track's S3 archive. Wazuh manager archives are enabled and forwarded to the indexer so the same raw events are searchable alongside security alerts.

The backend security group accepts TCP 1514 and 1515 only from the root host security group. Enable `enable_wazuh_agent_egress = true` in root Terraform before the bake-off; this opens only those two ports to the default VPC. See [the shared end-to-end test](../END-TO-END-TEST.md) for the exact search and teardown steps.

The dashboard tunnel uses HTTPS at `https://localhost:18080`. The initial single-node Wazuh Docker credentials are lab defaults; change them before any broader exposure. The stack is intentionally short-lived and not production hardened.
