from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.core.db import close_db


@asynccontextmanager
async def lifespan(app: FastAPI):
    yield
    await close_db()


app = FastAPI(
    title="Customer Flow API",
    version="1.0.0",
    lifespan=lifespan,
)


@app.get("/health")
def health_check():
    return {"status": "ok"}