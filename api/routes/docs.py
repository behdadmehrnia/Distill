"""OpenAPI / Swagger docs for Distill API."""

from __future__ import annotations

from aiohttp import web

OPENAPI_SPEC = {
    "openapi": "3.0.3",
    "info": {
        "title": "Distill API",
        "version": "0.1.0",
    },
    "paths": {
        "/assistant": {
            "get": {
                "summary": "Assistant UI",
                "tags": ["pages"],
                "responses": {"200": {"description": "HTML"}},
            }
        },
        "/assistant/{meeting_id}": {
            "get": {
                "summary": "Assistant UI for an existing meeting",
                "tags": ["pages"],
                "parameters": [
                    {
                        "name": "meeting_id",
                        "in": "path",
                        "required": True,
                        "schema": {"type": "string"},
                    }
                ],
                "responses": {
                    "200": {"description": "HTML with session history loaded by client"},
                    "404": {"description": "Meeting not found"},
                },
            }
        },
        "/health": {
            "get": {
                "summary": "Health check",
                "tags": ["system"],
                "responses": {"200": {"description": "OK"}},
            }
        },
        "/meetings": {
            "get": {
                "summary": "List meetings",
                "tags": ["meetings"],
                "responses": {"200": {"description": "Meeting list"}},
            },
            "post": {
                "summary": "Create meeting",
                "tags": ["meetings"],
                "requestBody": {
                    "required": False,
                    "content": {
                        "application/json": {
                            "schema": {
                                "type": "object",
                                "properties": {
                                    "title": {"type": "string"},
                                    "participants": {
                                        "type": "array",
                                        "items": {"type": "string"},
                                    },
                                    "start": {"type": "boolean", "default": True},
                                },
                            }
                        }
                    },
                },
                "responses": {"201": {"description": "Created"}},
            },
        },
        "/meetings/{meeting_id}": {
            "get": {
                "summary": "Get meeting",
                "tags": ["meetings"],
                "parameters": [
                    {
                        "name": "meeting_id",
                        "in": "path",
                        "required": True,
                        "schema": {"type": "string"},
                    }
                ],
                "responses": {
                    "200": {"description": "Meeting"},
                    "404": {"description": "Not found"},
                },
            }
        },
        "/meetings/{meeting_id}/stop": {
            "post": {
                "summary": "Stop meeting recording",
                "tags": ["meetings"],
                "parameters": [
                    {
                        "name": "meeting_id",
                        "in": "path",
                        "required": True,
                        "schema": {"type": "string"},
                    }
                ],
                "responses": {"200": {"description": "Stopped"}},
            }
        },
        "/meetings/{meeting_id}/transcript": {
            "get": {
                "summary": "Get transcript timeline",
                "tags": ["meetings"],
                "parameters": [
                    {
                        "name": "meeting_id",
                        "in": "path",
                        "required": True,
                        "schema": {"type": "string"},
                    }
                ],
                "responses": {"200": {"description": "Segments"}},
            }
        },
        "/meetings/{meeting_id}/upload": {
            "post": {
                "summary": "Upload recorded audio",
                "tags": ["meetings"],
                "parameters": [
                    {
                        "name": "meeting_id",
                        "in": "path",
                        "required": True,
                        "schema": {"type": "string"},
                    }
                ],
                "requestBody": {
                    "required": True,
                    "content": {
                        "multipart/form-data": {
                            "schema": {
                                "type": "object",
                                "properties": {
                                    "file": {"type": "string", "format": "binary"}
                                },
                            }
                        }
                    },
                },
                "responses": {"200": {"description": "Transcribed"}},
            }
        },
        "/meetings/{meeting_id}/insights": {
            "get": {
                "summary": "Get saved insights",
                "tags": ["insights"],
                "parameters": [
                    {
                        "name": "meeting_id",
                        "in": "path",
                        "required": True,
                        "schema": {"type": "string"},
                    }
                ],
                "responses": {"200": {"description": "Insights"}},
            },
            "post": {
                "summary": "Generate insights",
                "tags": ["insights"],
                "parameters": [
                    {
                        "name": "meeting_id",
                        "in": "path",
                        "required": True,
                        "schema": {"type": "string"},
                    }
                ],
                "responses": {"200": {"description": "Generated insights"}},
            },
        },
        "/meetings/{meeting_id}/audio": {
            "get": {
                "summary": "WebSocket audio stream",
                "tags": ["realtime"],
                "description": "Upgrade to WebSocket. Send JSON `{type:'audio', data:int16[]}`.",
                "parameters": [
                    {
                        "name": "meeting_id",
                        "in": "path",
                        "required": True,
                        "schema": {"type": "string"},
                    }
                ],
                "responses": {"101": {"description": "Switching Protocols"}},
            }
        },
    },
}

SWAGGER_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1.0" />
  <title>Distill API Docs</title>
  <link rel="icon" href="/logo.svg" type="image/svg+xml" />
  <link rel="stylesheet" href="https://unpkg.com/swagger-ui-dist@5/swagger-ui.css" />
  <style>
    /* Keep Swagger's default light theme — a dark body bg alone
       leaves opblock titles/paths unreadable (dark text on dark). */
    body { margin: 0; background: #fafafa; }
    .topbar { display: none; }
    .swagger-ui .info .title { color: #1a1a1a; }
  </style>
</head>
<body>
  <div id="swagger-ui"></div>
  <script src="https://unpkg.com/swagger-ui-dist@5/swagger-ui-bundle.js"></script>
  <script>
    window.ui = SwaggerUIBundle({
      url: "/openapi.json",
      dom_id: "#swagger-ui",
      presets: [SwaggerUIBundle.presets.apis],
      layout: "BaseLayout",
    });
  </script>
</body>
</html>
"""


async def serve_openapi(request: web.Request) -> web.Response:
    return web.json_response(OPENAPI_SPEC)


async def serve_docs(_request: web.Request) -> web.Response:
    return web.Response(text=SWAGGER_HTML, content_type="text/html", charset="utf-8")
