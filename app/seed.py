from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Operator, OperatorAlias
from app.normalization import normalize_operator_text


DEFAULT_OPERATORS: dict[str, tuple[str, ...]] = {
    "همراه اول": ("همراه اول", "MCI", "Hamrah Aval"),
    "ایرانسل": ("ایرانسل", "Irancell"),
    "رایتل": ("رایتل", "Rightel"),
    "شاتل موبایل": ("شاتل موبایل", "Shatel Mobile"),
    "سامانتل": ("سامانتل", "Samantel"),
    "آپتل": ("آپتل", "Aptel"),
    "مخابرات": ("مخابرات", "TCI"),
    "شاتل": ("شاتل", "Shatel"),
    "آسیاتک": ("آسیاتک", "Asiatech"),
    "مبین نت": ("مبین نت", "مبین‌نت", "Mobinnet"),
    "پیشگامان": ("پیشگامان", "Pishgaman"),
    "پارس آنلاین": ("پارس آنلاین", "پارس‌آنلاین", "Pars Online"),
    "های وب": ("های وب", "های‌وب", "HiWeb"),
    "صبانت": ("صبانت", "Sabanet"),
    "فن آوا": ("فن آوا", "فن‌آوا", "Fanava"),
    "رهام داتک": ("رهام داتک", "Raham Datak"),
    "عصر تلکام": ("عصر تلکام", "Asr Telecom"),
    "ماهان نت": ("ماهان نت", "ماهان‌نت", "Mahan Net"),
    "زیتل": ("زیتل", "Zitel"),
    "دیده بان نت": ("دیده بان نت", "دیده‌بان نت", "Didban Net"),
    "مبنا تلکام": ("مبنا تلکام", "Mabna Telecom"),
    "تک نت": ("تک نت", "تک‌نت", "Taknet"),
}


async def seed_operators(session: AsyncSession) -> None:
    if (await session.scalar(select(Operator.id).limit(1))) is not None:
        return
    for canonical_name, aliases in DEFAULT_OPERATORS.items():
        operator = Operator(canonical_name=canonical_name)
        session.add(operator)
        await session.flush()
        for alias in set((canonical_name, *aliases)):
            session.add(
                OperatorAlias(
                    operator_id=operator.id,
                    alias=alias,
                    normalized_alias=normalize_operator_text(alias),
                )
            )
    await session.commit()

