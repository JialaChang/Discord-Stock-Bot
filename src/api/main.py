from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from src.api import router


app = FastAPI(title="Stock API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173"],  # frontend origin
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(router)