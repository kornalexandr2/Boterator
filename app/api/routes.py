from fastapi import APIRouter, Request, HTTPException
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.templating import Jinja2Templates
from loguru import logger
import json

router = APIRouter(prefix="/twa", tags=["twa"])
templates = Jinja2Templates(directory="app/templates")

@router.get("/store", response_class=HTMLResponse)
@router.get("//store", response_class=HTMLResponse, include_in_schema=False)
async def get_store(request: Request):
    """Client TWA - Storefront"""
    return templates.TemplateResponse("client/store.html", {"request": request})

@router.get("/admin", response_class=HTMLResponse)
@router.get("//admin", response_class=HTMLResponse, include_in_schema=False)
async def get_admin_crm(request: Request):
    """Admin TWA - CRM Dashboard"""
    return templates.TemplateResponse("admin/crm.html", {"request": request})

@router.post("/action")
async def process_twa_action(request: Request):
    """Processes actions from TWA (buy, export, etc.) via fetch."""
    try:
        data = await request.json()
        action = data.get("action")
        # In a real app, we would validate tg.initData here
        # and use the user_id from it.
        
        logger.info(f"TWA Action received: {action} with data {data}")
        
        # This is where you would trigger bot messages or DB updates
        # For now, just return success
        return JSONResponse({"status": "ok", "message": f"Action {action} processed"})
    except Exception as e:
        logger.error(f"Error processing TWA action: {e}")
        return JSONResponse({"status": "error", "message": str(e)}, status_code=400)
