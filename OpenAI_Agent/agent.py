"""
Minimal Google ADK OpenAPIToolset demo for GitHub integration.

The agent can:
1. List repositories for the authenticated GitHub user.
2. List issues for a repository.
3. Create an issue after user confirmation.
"""

import os

from google.adk.agents import LlmAgent
from google.adk.tools.openapi_tool.openapi_spec_parser.openapi_toolset import (
    OpenAPIToolset,
)
from google.adk.tools.openapi_tool.auth.auth_helpers import (
    token_to_scheme_credential,
)


# Read the GitHub token from the .env environment
GITHUB_TOKEN = os.environ["GITHUB_TOKEN"]


# Minimal OpenAPI specification for selected GitHub REST API operations
GITHUB_OPENAPI_SPEC = {
    "openapi": "3.0.0",
    "info": {
        "title": "GitHub API (minimal)",
        "version": "1.0.0",
    },
    "servers": [
        {
            "url": "https://api.github.com",
        }
    ],
    "paths": {
        "/user/repos": {
            "get": {
                "operationId": "list_my_repos",
                "summary": "List repositories for the authenticated user",
                "parameters": [
                    {
                        "name": "sort",
                        "in": "query",
                        "schema": {
                            "type": "string",
                            "enum": [
                                "created",
                                "updated",
                                "pushed",
                                "full_name",
                            ],
                        },
                    },
                    {
                        "name": "per_page",
                        "in": "query",
                        "schema": {
                            "type": "integer",
                            "default": 10,
                        },
                    },
                ],
                "responses": {
                    "200": {
                        "description": "OK",
                    }
                },
            }
        },
        "/repos/{owner}/{repo}/issues": {
            "get": {
                "operationId": "list_issues",
                "summary": "List issues on a repository",
                "parameters": [
                    {
                        "name": "owner",
                        "in": "path",
                        "required": True,
                        "schema": {"type": "string"},
                    },
                    {
                        "name": "repo",
                        "in": "path",
                        "required": True,
                        "schema": {"type": "string"},
                    },
                    {
                        "name": "state",
                        "in": "query",
                        "schema": {
                            "type": "string",
                            "enum": ["open", "closed", "all"],
                            "default": "open",
                        },
                    },
                ],
                "responses": {
                    "200": {
                        "description": "OK",
                    }
                },
            },
            "post": {
                "operationId": "create_issue",
                "summary": "Create an issue on a repository",
                "parameters": [
                    {
                        "name": "owner",
                        "in": "path",
                        "required": True,
                        "schema": {"type": "string"},
                    },
                    {
                        "name": "repo",
                        "in": "path",
                        "required": True,
                        "schema": {"type": "string"},
                    },
                ],
                "requestBody": {
                    "required": True,
                    "content": {
                        "application/json": {
                            "schema": {
                                "type": "object",
                                "properties": {
                                    "title": {"type": "string"},
                                    "body": {"type": "string"},
                                },
                                "required": ["title"],
                            }
                        }
                    },
                },
                "responses": {
                    "201": {
                        "description": "Created",
                    }
                },
            },
        },
    },
}


# Configure the GitHub Authorization header
auth_scheme, auth_credential = token_to_scheme_credential(
    "apikey",
    "header",
    "Authorization",
    f"Bearer {GITHUB_TOKEN}",
)


# Convert the OpenAPI operations into tools
github_toolset = OpenAPIToolset(
    spec_dict=GITHUB_OPENAPI_SPEC,
    auth_scheme=auth_scheme,
    auth_credential=auth_credential,
)


# ADK requires a module-level root_agent
root_agent = LlmAgent(
    name="github_agent",
    model="gemini-flash-latest",
    description="Answers questions about the user's GitHub repositories and issues.",
    instruction=(
        "You help the user inspect and manage their GitHub repositories. "
        "Use list_my_repos to find repositories. "
        "Use list_issues to check issue status. "
        "Before calling create_issue, ask the user to confirm the repository, "
        "issue title, and issue body."
    ),
    tools=[github_toolset],
)