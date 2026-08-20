"""API module initialization."""

from fastapi import FastAPI
from . import atem, cameras, production, streaming, agents, websocket, vision, director, easyworship, identity, memory

def register_routes(app: FastAPI):
    """Register all API routes."""
    app.include_router(atem.router)
    app.include_router(cameras.router)
    app.include_router(production.router)
    app.include_router(streaming.router)
    app.include_router(agents.router)
    app.include_router(websocket.router)
    app.include_router(vision.router)
    app.include_router(director.router)
    app.include_router(easyworship.router)
    app.include_router(identity.router)
    app.include_router(memory.router)

__all__ = ["register_routes"]
