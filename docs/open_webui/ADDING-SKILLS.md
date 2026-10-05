# Adding Skills to Open WebUI

## Purpose

This procedure defines how to create, review, install, test, and maintain
LLM Skills in Open WebUI.

A Skill is a reusable Markdown instruction set that teaches an LLM how to
perform a task. Skills contain instructions rather than executable
capabilities.

Use a Skill when the requirement is primarily:

- procedure
- methodology
- analysis guidance
- formatting requirements
- troubleshooting steps
- organizational standards
- tool-use instructions

Do not use a Skill when the LLM must directly access an external system.
Use a Tool, MCP server, or OpenAPI server instead.

## Prerequisites

- Access to Open WebUI
- Permission to create or manage Workspace Skills
- A defined use case
- A model capable of following the instructions

## Procedure

### 1. Define the capability

Document:

- Skill name
- Purpose
- Trigger conditions
- Required tools
- Required permissions
- Expected output
- Failure conditions

### 2. Create SKILL.md

Use the following structure:

---
name: example-skill
description: Performs the defined operational workflow.
version: 0.1.0
---

# Example Skill

Description of the capability.

## When to Use

Use this skill when:

- condition one
- condition two

## Prerequisites

List required tools, credentials, applications, or state.

## How to Run

Describe the normal workflow.

## Quick Reference

List important commands, APIs, paths, or resources.

## Procedure

1. Perform the first action.
2. Validate the result.
3. Perform the next action.
4. Return the expected result.

## Pitfalls

- Known limitation
- Common failure
- Security consideration

## Verification

Describe one test that proves the skill is functioning correctly.

### 3. Install the Skill

In Open WebUI:

1. Open Workspace.
2. Select Skills.
3. Select Create.
4. Enter the Name.
5. Enter the Description.
6. Paste the Markdown into Content.
7. Save the Skill.

Alternatively, import the Markdown file.

### 4. Bind the Skill to a Model

1. Open Workspace > Models.
2. Edit the target model.
3. Locate Skills.
4. Select the required Skill.
5. Save the model.

### 5. Test the Skill

Create a new conversation using the target model.

Test:

- positive trigger
- ambiguous trigger
- non-trigger
- required tool usage
- failure handling
- expected output format

The Skill passes when the model consistently recognizes when the
procedure applies and follows the defined workflow.

### 6. Version Control

Store the authoritative SKILL.md in source control.

Changes should follow:

1. Edit in source control.
2. Peer review.
3. Test.
4. Import/update Open WebUI.
5. Validate.
6. Increment version.