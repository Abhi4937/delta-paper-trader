"""Every route's DB session must commit BEFORE the response is sent (scope="function").

With FastAPI's default request scope the commit runs after the response, so a client's
next request (e.g. arm a live group right after saving its SL) can read stale data.
"""
from fastapi.routing import APIRoute, APIWebSocketRoute

from app.db.session import get_session
from app.main import app


def _walk(dep, found):
    for sub in dep.dependencies:
        if sub.call is get_session:
            found.append(sub.scope)
        _walk(sub, found)


def test_every_session_dependency_commits_before_response():
    scopes: list = []
    for r in app.routes:
        if isinstance(r, (APIRoute, APIWebSocketRoute)):
            _walk(r.dependant, scopes)
    assert scopes, "no get_session dependencies found"
    assert set(scopes) == {"function"}, scopes
