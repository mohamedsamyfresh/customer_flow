from __future__ import annotations

from enum import Enum


class Permission(str, Enum):
    """
    Standard permissions for Customer Flow & Analytics service.
    """
    ENTRIES_READ = "entries:read"
    WAITING_TIMES_READ = "waiting_times:read"
    ANALYTICS_READ = "analytics:read"
    ADMIN = "admin:all"

    def __str__(self) -> str:
        return self.value


class Role(str, Enum):
    """
    Standard roles for Customer Flow & Analytics service.
    """
    ADMIN = "admin"
    MANAGER = "manager"
    VIEWER = "viewer"
    ADMIN_DEV = "admin-dev"

    def __str__(self) -> str:
        return self.value
