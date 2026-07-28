from aiohttp import web


async def health_check(_request: web.Request) -> web.Response:
    return web.json_response(
        {
            "status": "ok",
            "service": "distill"
        }
    )
