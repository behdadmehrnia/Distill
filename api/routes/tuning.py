from __future__ import annotations

from aiohttp import web

from api.tuning import make_tuning, public_tuning_payload, _sanitize


async def get_tuning(request: web.Request) -> web.Response:
    return web.json_response(public_tuning_payload(request.app["tuning"]))


async def put_tuning(request: web.Request) -> web.Response:
    try:
        body = await request.json()
    except Exception:
        raise web.HTTPBadRequest(text="invalid json")

    values = body.get("values") if isinstance(body, dict) else None
    if not isinstance(values, dict):
        values = body if isinstance(body, dict) else {}

    cleaned = _sanitize(values)
    tuning = request.app["tuning"]
    tuning.update(cleaned)

    manager = request.app["manager"]
    manager.apply_tuning(tuning)

    return web.json_response(public_tuning_payload(tuning))


async def reset_tuning(request: web.Request) -> web.Response:
    fresh = make_tuning()
    request.app["tuning"].clear()
    request.app["tuning"].update(fresh)
    request.app["manager"].apply_tuning(fresh)
    return web.json_response(public_tuning_payload(fresh))
