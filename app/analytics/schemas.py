from __future__ import annotations

from pydantic import BaseModel, Field


class EmotionTransitions(BaseModel):
    natural_to_angry: int = Field(default=0, description="Transitions from natural/neutral to angry")
    angry_to_natural: int = Field(default=0, description="Transitions from angry to natural/neutral")
    natural_to_natural: int = Field(default=0, description="Transitions from natural/neutral to natural/neutral")
    angry_to_angry: int = Field(default=0, description="Transitions from angry to angry")


class LongestStay(BaseModel):
    entry_time: str | None = Field(default=None, description="Entry timestamp in ISO 8601 format")
    exit_time: str | None = Field(default=None, description="Exit timestamp in ISO 8601 format")
    duration_seconds: float | None = Field(default=None, description="Duration inside the store in seconds")
    customer_id: str | None = Field(default=None, description="Customer unique identifier (UUID)")
    entry_count: int | None = Field(default=None, description="Entry sequence counter")
    branch_id: str | None = Field(default=None, description="Branch identifier")
    camera_id: str | None = Field(default=None, description="Camera identifier")


class HighestOccupancyPeriod(BaseModel):
    start: str | None = Field(default=None, description="Bucket start timestamp in ISO 8601 format")
    end: str | None = Field(default=None, description="Bucket end timestamp in ISO 8601 format")
    occupancy: int = Field(default=0, description="Peak occupancy count during this bucket")


class OccupancyBucket(BaseModel):
    start: str = Field(description="Start time of the bucket in ISO 8601 format")
    end: str = Field(description="End time of the bucket in ISO 8601 format")
    occupancy: int = Field(description="Number of concurrent people inside during this bucket")


class OccupancyTimelineResponse(BaseModel):
    bucket: str = Field(description="Bucket interval used (e.g. 5m, 15m, 30m, 1h)")
    date: str = Field(description="Target date string YYYY-MM-DD")
    branch_id: str | None = Field(default=None, description="Branch filter if applied")
    peak_period: HighestOccupancyPeriod | None = Field(default=None, description="Period with peak occupancy")
    timeline: list[OccupancyBucket] = Field(default_factory=list, description="Occupancy per time slot")


class DashboardMetrics(BaseModel):
    people_in_store: int = Field(default=0, description="Number of people currently inside the store")
    total_entries_today: int = Field(default=0, description="Total entries recorded today")
    total_exits_today: int = Field(default=0, description="Total exits recorded today")
    emotion_transitions: EmotionTransitions = Field(
        default_factory=EmotionTransitions,
        description="Emotion sentiment transition counts from entry to exit",
    )
    longest_stay: LongestStay | None = Field(
        default=None,
        description="Longest completed customer stay record",
    )
    highest_occupancy_period: HighestOccupancyPeriod | None = Field(
        default=None,
        description="Highest occupancy time window today",
    )


class DashboardEvent(BaseModel):
    type: str = Field(default="dashboard_update", description="Event type identifier")
    timestamp: str = Field(description="Event timestamp in ISO 8601 format")
    branch_id: str | None = Field(default=None, description="Branch identifier")
    data: DashboardMetrics = Field(description="Current dashboard metrics state")
