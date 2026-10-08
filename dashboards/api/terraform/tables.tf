# The panel's three tables. They hold who may do what, so DynamoDB deletion protection is on:
# AWS refuses to delete them, and a destroy or replacement fails instead of losing the data.
# Removing a table on purpose means setting deletion_protection_enabled = false and applying
# first. (prevent_destroy is not used because it would also block the offline tests' cleanup.)

resource "aws_dynamodb_table" "users" {
  #checkov:skip=CKV_AWS_119:Encrypted at rest with the AWS owned key; a customer managed key adds cost and a key policy to keep for no lab benefit
  name                        = var.users_table_name
  billing_mode                = "PAY_PER_REQUEST"
  hash_key                    = "email"
  deletion_protection_enabled = true

  attribute {
    name = "email"
    type = "S"
  }

  point_in_time_recovery {
    enabled = true
  }
}

resource "aws_dynamodb_table" "entitlements" {
  #checkov:skip=CKV_AWS_119:Encrypted at rest with the AWS owned key; a customer managed key adds cost and a key policy to keep for no lab benefit
  name                        = var.entitlements_table_name
  billing_mode                = "PAY_PER_REQUEST"
  hash_key                    = "userId"
  range_key                   = "instanceId"
  deletion_protection_enabled = true

  attribute {
    name = "userId"
    type = "S"
  }

  attribute {
    name = "instanceId"
    type = "S"
  }

  point_in_time_recovery {
    enabled = true
  }
}

resource "aws_dynamodb_table" "events" {
  #checkov:skip=CKV_AWS_119:Encrypted at rest with the AWS owned key; a customer managed key adds cost and a key policy to keep for no lab benefit
  name                        = var.events_table_name
  billing_mode                = "PAY_PER_REQUEST"
  hash_key                    = "pk"
  range_key                   = "sk"
  deletion_protection_enabled = true

  attribute {
    name = "pk"
    type = "S"
  }

  attribute {
    name = "sk"
    type = "S"
  }

  ttl {
    attribute_name = "expiresAt"
    enabled        = true
  }

  point_in_time_recovery {
    enabled = true
  }
}

# Adopting the hand-built tables (adopt_existing_tables = true). The import happens during
# the first apply; the plan shows "will be imported" for each table, and must not show a
# replacement. If it does, the table's keys differ from the ones above: stop and compare.
import {
  for_each = var.adopt_existing_tables ? toset(["adopt"]) : toset([])
  to       = aws_dynamodb_table.users
  id       = var.users_table_name
}

import {
  for_each = var.adopt_existing_tables ? toset(["adopt"]) : toset([])
  to       = aws_dynamodb_table.entitlements
  id       = var.entitlements_table_name
}

import {
  for_each = var.adopt_existing_tables ? toset(["adopt"]) : toset([])
  to       = aws_dynamodb_table.events
  id       = var.events_table_name
}
