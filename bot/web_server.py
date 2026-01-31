import os
from aiohttp import web
from db import init_models, get_download

async def handle_download(request: web.Request):
    token = request.match_info["token"]
    row = await get_download(token)
    if not row:
        raise web.HTTPNotFound(text="expired or not found")

    headers = {
        "Content-Disposition": f'attachment; filename="{row["filename"]}"',
        "Cache-Control": "no-store",
    }
    return web.Response(body=row["content"], headers=headers, content_type=row["mime"])

async def create_app():
    await init_models()
    app = web.Application()
    app.router.add_get("/d/{token}", handle_download)
    return app

def main():
    port = int(os.getenv("WEB_PORT", "8080"))
    web.run_app(create_app(), host="0.0.0.0", port=port)

if __name__ == "__main__":
    main()
