"""WebSocket stream of live agent activity.

On connect the client gets a full snapshot (sectors, agents, recent findings, kill switch),
then every event as it happens: agent status/progress, new findings, kill-switch changes.
"""

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from ptl.agents.runtime import AgentRuntime


def build_ws_router(runtime: AgentRuntime) -> APIRouter:
    router = APIRouter()

    @router.websocket("/ws/activity")
    async def activity(websocket: WebSocket) -> None:
        await websocket.accept()
        queue = runtime.bus.subscribe()
        try:
            snapshot = runtime.snapshot().model_dump(mode="json")
            await websocket.send_json({"type": "snapshot", **snapshot})
            while True:
                await websocket.send_json(await queue.get())
        except WebSocketDisconnect:
            pass
        finally:
            runtime.bus.unsubscribe(queue)

    return router
