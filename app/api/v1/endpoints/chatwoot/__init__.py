from fastapi import APIRouter

from . import account, agent_bots, agents, conversations, users, teams, reports

router = APIRouter(prefix="/messaging", tags=["Messaging"])
router.include_router(account.router)
router.include_router(conversations.router)
router.include_router(users.router)
router.include_router(agent_bots.router)
router.include_router(agents.router)
router.include_router(teams.router)
router.include_router(reports.router)
