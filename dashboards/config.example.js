// Example of dashboards/config.js: the settings the page loads at start-up.
// The page signs in with any OpenID Connect provider. Fill in the provider you use.
// For the lab's own Cognito pool, scripts/make-panel-config.ps1 writes this file from
// Terraform's control_panel_config output; for any other provider, write it by hand.
// The real file is not committed.
window.PANEL_CONFIG = {
  // The provider's issuer URL. The page finds the sign-in and token addresses from
  // <issuer>/.well-known/openid-configuration. Examples:
  //   Okta       https://<org>.okta.com/oauth2/default
  //   Entra ID   https://login.microsoftonline.com/<tenant id>/v2.0
  //   Cognito    https://cognito-idp.us-east-1.amazonaws.com/<user pool id>
  issuer: "https://login.microsoftonline.com/<tenant id>/v2.0",
  // The single-page app (public client, no secret, PKCE) registered with the provider.
  // The Control API's authorizer must use this same issuer and client ID (audience).
  clientId: "<client id>",
  // This page's address. Register it with the provider as a redirect URI.
  redirectUri: "https://cp.aiwebdemo.click/",
  apiUrl: "https://<api id>.execute-api.us-east-1.amazonaws.com",
  // Optional. Default "openid email profile". Okta and Entra also need offline_access to refresh quietly.
  // scope: "openid email profile offline_access",
  // Shown in the status bar only.
  region: "us-east-1"
  // Cognito only, instead of issuer: userPoolId, plus hostedLoginDomain
  // ("<prefix>.auth.us-east-1.amazoncognito.com") for its sign-in and sign-out pages.
  // This is what make-panel-config.ps1 writes.
};
