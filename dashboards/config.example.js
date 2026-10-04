// Example of dashboards/config.js. scripts/make-panel-config.ps1 writes the real
// file from Terraform's control_panel_config output; it is not committed.
window.PANEL_CONFIG = {
  region: "us-east-1",
  userPoolId: "us-east-1_XXXXXXXXX",
  appClientId: "xxxxxxxxxxxxxxxxxxxxxxxxxx",
  hostedLoginDomain: "<prefix>.auth.us-east-1.amazoncognito.com",
  redirectUri: "https://cp.aiwebdemo.click/",
  apiUrl: null
};
