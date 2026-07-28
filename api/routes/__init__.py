from aiohttp import web

from api.routes import docs, health, meetings, pages, tuning


def setup_routes(app: web.Application) -> None:
    app.router.add_get("/health", health.health_check)
    app.router.add_get("/docs", docs.serve_docs)
    app.router.add_get("/openapi.json", docs.serve_openapi)

    app.router.add_get("/", pages.serve_landing)
    app.router.add_get("/assistant", pages.serve_assistant)
    app.router.add_get("/assistant/{meeting_id}", pages.serve_assistant_session)
    app.router.add_get("/styles.css", pages.serve_css)
    app.router.add_get("/meeting.js", pages.serve_js)
    app.router.add_get("/logo.svg", pages.serve_logo)

    app.router.add_get("/tuning", tuning.get_tuning)
    app.router.add_put("/tuning", tuning.put_tuning)
    app.router.add_post("/tuning/reset", tuning.reset_tuning)

    app.router.add_get("/meetings", meetings.list_meetings)
    app.router.add_post("/meetings", meetings.create_meeting)
    app.router.add_get("/meetings/{meeting_id}", meetings.get_meeting)
    app.router.add_post("/meetings/{meeting_id}/stop", meetings.stop_meeting)
    app.router.add_get("/meetings/{meeting_id}/transcript", meetings.get_transcript)
    app.router.add_post("/meetings/{meeting_id}/upload", meetings.upload_audio)
    app.router.add_post("/meetings/{meeting_id}/insights", meetings.generate_insights)
    app.router.add_get("/meetings/{meeting_id}/insights", meetings.get_insights)
    app.router.add_get("/meetings/{meeting_id}/audio", meetings.audio_ws)
