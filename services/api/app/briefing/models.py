"""``briefing_preferences``: the owner's ONE durable morning-briefing preference row
(same singleton discipline as ``app.alarms.models.AmbientPolicyRow`` /
``AMBIENT_POLICY_ID`` — one owner, one row, created lazily with conservative defaults on
first read; see ``app.briefing.service.get_preferences_row``).
"""

from __future__ import annotations

from sqlalchemy import Boolean, String
from sqlalchemy.orm import Mapped, mapped_column

from app.models import Base

BRIEFING_PREFERENCES_ID = "owner"


class BriefingPreferencesRow(Base):
    __tablename__ = "briefing_preferences"

    preferences_id: Mapped[str] = mapped_column(
        String(32), primary_key=True, default=BRIEFING_PREFERENCES_ID
    )
    morning_briefing_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    include_weather: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    include_system_status: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    include_calendar: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    #: B45 (req 278, 362): the unread-mail clause.
    include_mail: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    include_overnight_work: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    include_news_summary: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    #: Defaults FALSE (task brief §3, non-negotiable): nothing may open a video without
    #: this explicitly on, whatever ``include_news_summary`` says — a summary is a
    #: sentence; opening a video on the owner's screen unasked is a different act.
    auto_open_news_video: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)


__all__ = ["BRIEFING_PREFERENCES_ID", "BriefingPreferencesRow"]
