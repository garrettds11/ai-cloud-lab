# Adding Tools to Open WebUI

## Purpose

This procedure defines how external capabilities are added to LLMs
running through Open WebUI.

Tools allow an LLM to perform actions or retrieve information that is
not available from the model itself.

Examples:

- Query an API
- Search an internal service
- Retrieve application status
- Execute an approved workflow
- Query infrastructure
- Retrieve live operational data

## Tool Selection

Select the implementation using the following order of preference.

### 1. Built-in Open WebUI capability

Use an existing built-in capability when one already satisfies the
requirement.

### 2. OpenAPI Tool Server

Use when an existing service exposes an HTTP API and can provide an
OpenAPI specification.

Preferred for:

- REST APIs
- internal microservices
- enterprise services
- reusable remote capabilities

### 3. MCP Server

Use when the capability already implements MCP or requires a richer
agent/tool interface.

Preferred for:

- reusable AI tool servers
- multiple related operations
- agent-oriented integrations
- capabilities shared between AI platforms

### 4. Workspace Python Tool

Use when:

- the integration is small
- Python is appropriate
- direct Open WebUI integration is required
- the code is fully trusted

Workspace Tools execute Python inside the Open WebUI environment and
must be treated as privileged code.

## Tool Intake Requirements

Before implementation document:

- Tool name
- Business purpose
- Tool owner
- Data accessed
- Systems accessed
- Authentication mechanism
- Required permissions
- Network destinations
- Read/write capability
- Expected inputs
- Expected outputs
- Failure behavior
- Logging requirements

## Security Review

Verify that the Tool:

- does not contain embedded credentials
- uses least-privilege credentials
- validates model-supplied input
- restricts network destinations
- does not expose secrets in responses
- does not return unnecessary sensitive data
- handles errors safely
- records appropriate audit information
- has bounded execution behavior
- has documented dependencies

## Installation

### Workspace Tool

1. Open Workspace > Tools.
2. Select Create or Import.
3. Review the complete Python source.
4. Verify dependencies.
5. Verify configuration/Valves.
6. Save the Tool.
7. Configure required credentials.
8. Configure access control.

### External Tool Server

1. Deploy or identify the Tool Server.
2. Verify connectivity from Open WebUI.
3. Configure authentication.
4. Add the server through Open WebUI Integrations.
5. Verify tool discovery.
6. Restrict access to authorized users/groups.

## Model Assignment

1. Open Workspace > Models.
2. Edit the target model.
3. Locate Tools.
4. Enable the approved Tool.
5. Save.

## Validation

Test:

1. Tool discovery
2. Correct tool selection
3. Valid arguments
4. Invalid arguments
5. Authentication failure
6. Authorization failure
7. Network failure
8. Timeout
9. Malformed response
10. Successful operation

Confirm that the LLM correctly interprets the result rather than merely
confirming that the API call succeeds.

## Production Approval

A Tool is production-ready when:

- code review is complete
- security review is complete
- permissions follow least privilege
- credentials are externally managed
- testing is successful
- ownership is documented
- rollback/removal procedure is documented