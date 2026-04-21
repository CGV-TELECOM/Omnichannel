from fastapi import APIRouter

from . import account, agent_bots, agents, conversations, users

router = APIRouter(prefix="/chatwoot", tags=["Chatwoot"])
router.include_router(account.router)
router.include_router(conversations.router)
router.include_router(users.router)
router.include_router(agent_bots.router)
router.include_router(agents.router)
