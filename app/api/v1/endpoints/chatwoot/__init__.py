from fastapi import APIRouter

from . import account, agent_bots, agents, conversations, users, teams, reports, search

router = APIRouter(prefix="/messaging", tags=["Messaging"])
router.include_router(account.router)
# search trước conversations: tránh /conversations/search bị nuốt bởi {conversation_id}
router.include_router(search.router)
router.include_router(conversations.router)
router.include_router(users.router)
router.include_router(agent_bots.router)
router.include_router(agents.router)
router.include_router(teams.router)
router.include_router(reports.router)
