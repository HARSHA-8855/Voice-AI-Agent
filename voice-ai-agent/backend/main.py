from fastapi import FastAPI

app = FastAPI(title="Clinical Voice AI Backend")

@app.get("/")
async def root():
    return {"message": "Voice AI Backend is running"}
