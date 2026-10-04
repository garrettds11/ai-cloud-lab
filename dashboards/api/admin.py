"""Entry point for the admin Lambda: users, roles, grants and change history. It cannot start instances."""

from handler import admin_handler as lambda_handler  # noqa: F401
