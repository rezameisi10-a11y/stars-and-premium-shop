"""پنل‌های جعلی برای تست درایورها و جریان فروش (فعلاً مرزبان)."""
import time

from aiohttp import web


def marzban_app():
    users = {}
    app = web.Application()
    app["users"] = users

    async def token(r):
        d = await r.post()
        if d.get("password") != "pw":
            return web.json_response({"detail": "bad"}, status=401)
        return web.json_response({"access_token": "tok"})

    async def system(r):
        return web.json_response({"version": "v0.8.4", "total_user": len(users)})

    async def inbounds(r):
        return web.json_response({"vless": [{"tag": "VLESS TCP", "network": "tcp", "tls": "reality"}]})

    def view(u):
        return {**u, "subscription_url": f"/sub/{u['username']}", "links": []}

    async def create(r):
        b = await r.json()
        if b["username"] in users:
            return web.json_response({"detail": "User already exists"}, status=409)
        users[b["username"]] = {**b, "used_traffic": 0}
        return web.json_response(view(users[b["username"]]))

    async def get(r):
        u = users.get(r.match_info["n"])
        return web.json_response(view(u)) if u else web.json_response({"detail": "nf"}, status=404)

    async def put(r):
        u = users[r.match_info["n"]]
        u.update(await r.json())
        return web.json_response(view(u))

    async def delete(r):
        users.pop(r.match_info["n"], None)
        return web.json_response({})

    async def reset(r):
        users[r.match_info["n"]]["used_traffic"] = 0
        return web.json_response({})

    async def sub(r):
        import base64
        body = "vless://aaaa@1.2.3.4:443?security=reality#one\ntrojan://xyz@5.6.7.8:443#two\n"
        return web.Response(text=base64.b64encode(body.encode()).decode())

    app.router.add_get("/sub/{n}", sub)
    app.router.add_post("/api/admin/token", token)
    async def nodes(r):
        return web.json_response([
            {"id": 1, "name": "DE-1", "address": "10.0.0.1", "port": 62050, "status": "connected", "xray_version": "25.1.1", "node_version": "0.2.0", "uplink": 5 * 1024 ** 3, "downlink": 9 * 1024 ** 3, "usage_coefficient": 1, "message": ""},
            {"id": 2, "name": "NL-2", "address": "10.0.0.2", "port": 62050, "status": "error", "message": "connection refused", "usage_coefficient": 1.5}])

    app.router.add_get("/api/system", system)
    app.router.add_get("/api/nodes", nodes)
    app.router.add_get("/api/inbounds", inbounds)
    app.router.add_post("/api/user", create)
    app.router.add_get("/api/user/{n}", get)
    app.router.add_put("/api/user/{n}", put)
    app.router.add_delete("/api/user/{n}", delete)
    app.router.add_post("/api/user/{n}/reset", reset)
    return app
