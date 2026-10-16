from sqlalchemy import func, select

from app.infrastructure.models import Citation, Conversation, KnowledgeBase, Membership, Message


class ChatQueries:
    async def conversation(self, conversation_id, *, lock=False):
        query = select(Conversation).where(Conversation.id == conversation_id)
        if lock:
            query = query.with_for_update().execution_options(populate_existing=True)
        return await self.session.scalar(query)

    async def message(self, message_id):
        return await self.session.get(Message, message_id)

    async def request_message(self, conversation_id, request_id):
        return await self.session.scalar(
            select(Message).where(
                Message.conversation_id == conversation_id,
                Message.client_request_id == request_id,
            )
        )

    async def conversations(self, user_id, kb_id, limit, position):
        from app.infrastructure.repositories import before_position

        query = (
            select(Conversation)
            .join(KnowledgeBase, KnowledgeBase.id == Conversation.kb_id)
            .join(
                Membership,
                Membership.kb_id == KnowledgeBase.id,
            )
            .where(
                Conversation.user_id == user_id,
                Membership.user_id == user_id,
                KnowledgeBase.deleted_at.is_(None),
            )
        )
        if kb_id:
            query = query.where(Conversation.kb_id == kb_id)
        if position:
            query = query.where(before_position(Conversation.created_at, Conversation.id, position))
        return (
            await self.session.scalars(
                query.order_by(Conversation.created_at.desc(), Conversation.id.desc()).limit(limit)
            )
        ).all()

    async def messages(self, conversation_id, limit=100, position=None):
        from app.infrastructure.repositories import before_position

        query = select(Message).where(Message.conversation_id == conversation_id)
        if position:
            query = query.where(before_position(Message.created_at, Message.id, position))
        return (
            await self.session.scalars(
                query.order_by(Message.created_at.desc(), Message.id.desc()).limit(limit)
            )
        ).all()

    async def history_pairs(self, conversation_id, before_sequence):
        return (
            await self.session.scalars(
                select(Message)
                .where(
                    Message.conversation_id == conversation_id,
                    Message.sequence_no < before_sequence,
                    Message.role == "assistant",
                    Message.status == "completed",
                )
                .order_by(Message.sequence_no.desc())
                .limit(6)
            )
        ).all()

    async def citations(self, message_id):
        return (
            await self.session.scalars(
                select(Citation)
                .where(Citation.message_id == message_id)
                .order_by(Citation.source_label)
            )
        ).all()

    async def expired_conversations(self):
        return (
            await self.session.scalars(
                select(Conversation)
                .join(
                    Message,
                    Conversation.active_message_id == Message.id,
                )
                .where(
                    Message.status == "generating",
                    Message.generation_deadline_at <= func.utc_timestamp(6),
                )
                .order_by(Conversation.id)
                .limit(100)
                .with_for_update(skip_locked=True)
            )
        ).all()
