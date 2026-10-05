# -*- coding: utf-8 -*-
from fastapi import APIRouter
from app.queue.manager import smcc_queue_manager
router = APIRouter(prefix="/api/queue", tags=["ATLAS Queue"])
@router.get("/status")
def queue_status() -> dict:
    return smcc_queue_manager.status()
@router.get("/health")
def queue_health() -> dict:
    return smcc_queue_manager.health()
