from fastapi import FastAPI

from api.supabase.db_api import router as db_api_router
from api.voice_bot.voice_api import router as voice_api_router
from api.email_agent.email_inbound import router as email_inbound_router
from fastapi.middleware.cors import CORSMiddleware

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:5173",  # Vite dev server
        "http://localhost:3000",
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(db_api_router)
app.include_router(voice_api_router)
app.include_router(email_inbound_router)
