"""``client.project``."""

from __future__ import annotations

from enigmoss._http import Endpoint
from enigmoss._options import RequestOptions
from enigmoss.models.project import Project
from enigmoss.resources._base import Resource

__all__ = ["ProjectResource"]

_GET = Endpoint.get("/project", "any")


class ProjectResource(Resource):
    """The project the client is configured for. Works with either key."""

    __slots__ = ()

    def get(self, *, options: RequestOptions | None = None) -> Project:
        """Returns the project, the key type that signed the request and its permissions.

        A cheap way to check the configuration: ``GET /api/v4/project``, either key, no permission.

        Raises:
            AuthenticationError: Wrong key, project or clock.
            EnigmossError: Any other failure.
        """
        return self._one(_GET, Project._parse, options=options)
