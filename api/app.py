"""FastAPI Application für CodingCrew Gateway."""
from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path
from typing import AsyncGenerator

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from .routes import projects_router, workers_router, queue_router, webhooks_router
from shared.config import list_projects


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator:
    """Application lifespan handler."""
    # Startup
    Path.home().joinpath("CodingCrew", "health").mkdir(parents=True, exist_ok=True)
    yield
    # Shutdown
    pass


def create_app() -> FastAPI:
    """Create and configure FastAPI application."""
    app = FastAPI(
        title="CodingCrew API",
        description="API Gateway für autonome Coding-Crew Microservices",
        version="0.3.0",
        docs_url="/docs",
        redoc_url="/redoc",
        lifespan=lifespan,
    )

    # CORS middleware
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],  # In production: restrict to known origins
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # Include routers
    app.include_router(projects_router, prefix="/api/v1/projects", tags=["projects"])
    app.include_router(workers_router, prefix="/api/v1/workers", tags=["workers"])
    app.include_router(queue_router, prefix="/api/v1/queue", tags=["queue"])
    app.include_router(webhooks_router, prefix="/api/v1/webhooks", tags=["webhooks"])

    @app.get("/health")
    async def health_check():
        """Basic health check endpoint."""
        return {"status": "healthy"}

    @app.get("/")
    async def root():
        """Root endpoint with API info."""
        return {
            "name": "CodingCrew API",
            "version": "0.3.0",
            "docs": "/docs",
            "projects": list_projects(),
        }

    return app
