"""WebSocket endpoints for real-time updates."""

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from app.events.bus import event_bus

router = APIRouter(tags=["WebSocket"])


@router.websocket("/ws/vision")
async def websocket_vision(websocket: WebSocket):
    """WebSocket endpoint for vision event updates."""
    await websocket.accept()
    queue = await event_bus.subscribe()
    try:
        while True:
            message = await queue.get()
            await websocket.send_json(message)
    except WebSocketDisconnect:
        pass
    finally:
        await event_bus.unsubscribe(queue)
