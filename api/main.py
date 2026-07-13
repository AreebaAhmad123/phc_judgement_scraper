#Creates an asynchronous context manager to handle setup and cleanup using async/await.
from contextlib import asynccontextmanager

from fastapi import FastAPI

from phc_scraper.logging_setup import configure_logging
from phc_scraper.weaviate_client import ensure_schema, close_client

from . import routes_chat, routes_ingest


@asynccontextmanager
async def lifespan(app: FastAPI):
    configure_logging()
    ensure_schema()  # creates the Weaviate collection on first run, no-op after
    yield
    close_client()


app = FastAPI(
    title="PHC Judgments API",
    description="Serves the Peshawar High Court reported-judgments archive: "
               "triggers ingestion into Weaviate and answers grounded "
               "questions over it (RAG) with citations.",
    version="1.0.0",
    lifespan=lifespan,
)

app.include_router(routes_chat.router)
app.include_router(routes_ingest.router)


@app.get("/health", tags=["health"])
def health():
    return {"status": "ok"}
