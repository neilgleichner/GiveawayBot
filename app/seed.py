from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Operator, OperatorAlias
from app.normalization import normalize_operator_text
from app.operator_defaults import DEFAULT_OPERATORS


async def seed_operators(session: AsyncSession) -> None:
    for canonical_name, aliases in DEFAULT_OPERATORS.items():
        operator = await session.scalar(select(Operator).where(Operator.canonical_name == canonical_name))
        if operator is None:
            operator = Operator(canonical_name=canonical_name)
            session.add(operator)
            await session.flush()

        # Several human-friendly aliases normalize to the same canonical lookup key
        # (e.g. "مبین نت" and "مبین‌نت"). Store each normalized key exactly once.
        unique_aliases: dict[str, str] = {}
        for alias in (canonical_name, *aliases):
            unique_aliases.setdefault(normalize_operator_text(alias), alias)

        for normalized_alias, alias in unique_aliases.items():
            exists = await session.scalar(
                select(OperatorAlias.id).where(OperatorAlias.normalized_alias == normalized_alias)
            )
            if exists is not None:
                continue
            session.add(
                OperatorAlias(
                    operator_id=operator.id,
                    alias=alias,
                    normalized_alias=normalized_alias,
                )
            )
    await session.commit()
