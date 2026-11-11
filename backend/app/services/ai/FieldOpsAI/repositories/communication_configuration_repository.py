from typing import Optional
from sqlalchemy.orm import Session
from sqlalchemy.exc import SQLAlchemyError
from app.models import CommunicationChannelConfiguration, CommunicationConfigurationAudit

class CommunicationConfigurationRepository:
    def __init__(self, db: Session, tenant_id: str | None = None):
        self.db = db
        self.tenant_id = tenant_id

    def get_by_channel(self, channel: str, for_update: bool = False) -> Optional[CommunicationChannelConfiguration]:
        query = self.db.query(CommunicationChannelConfiguration).filter(
            CommunicationChannelConfiguration.channel == channel
        ).execution_options(populate_existing=True)
        if self.tenant_id is not None:
            query = query.filter(
                CommunicationChannelConfiguration.tenant_id == self.tenant_id
            )
        if for_update:
            # Using with_for_update to lock the row for atomic updates
            query = query.with_for_update()
        configuration = query.first()
        # Older installs seeded the system-wide channel rows under the
        # bootstrap tenant. Continue using those rows until the platform
        # scoped configuration is provisioned, while preferring platform
        # rows whenever they exist.
        if configuration is None and self.tenant_id == "**platform**":
            fallback_query = self.db.query(CommunicationChannelConfiguration).filter(
                CommunicationChannelConfiguration.channel == channel,
                CommunicationChannelConfiguration.tenant_id == "default",
            ).execution_options(populate_existing=True)
            if for_update:
                fallback_query = fallback_query.with_for_update()
            configuration = fallback_query.first()
            if configuration is None:
                legacy_rows = (
                    self.db.query(CommunicationChannelConfiguration)
                    .filter(CommunicationChannelConfiguration.channel == channel)
                    .all()
                )
                if len(legacy_rows) == 1:
                    configuration = legacy_rows[0]
        return configuration

    def update_state(
        self,
        configuration: CommunicationChannelConfiguration,
        new_state: str,
        actor_id: str
    ) -> None:
        """
        Updates the state and increments revision.
        Assumes the configuration is already bound to the session (e.g. from get_by_channel(for_update=True)).
        Does NOT commit.
        """
        configuration.state = new_state
        configuration.revision += 1
        configuration.updated_by = actor_id
        # SQLAlchemy handles updated_at server default/onupdate

    def add_audit(self, audit: CommunicationConfigurationAudit) -> None:
        """
        Adds an audit record to the session.
        Does NOT commit.
        """
        self.db.add(audit)
