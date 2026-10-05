from fastapi import FastAPI

app = FastAPI(
    title="JobFinder API",
    description="API y backend para el agente multiusuario de ofertas de empleo",
    version="0.1.0",
)


@app.get("/health", tags=["Salud"])
async def health_check():
    return {"status": "ok", "service": "jobfinder-api"}


@app.get("/", tags=["Root"])
async def root():
    return {"message": "JobFinder API operativa"}
