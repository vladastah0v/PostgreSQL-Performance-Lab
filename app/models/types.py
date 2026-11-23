from datetime import datetime
from decimal import Decimal
from enum import StrEnum
from typing import Annotated

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    Enum,
    Identity,
    Numeric,
    func,
)
from sqlalchemy.orm import mapped_column

PrimaryKey = Annotated[int, mapped_column(BigInteger, Identity(), primary_key=True)]
Money = Annotated[Decimal, mapped_column(Numeric(14, 2))]
CreatedAt = Annotated[
    datetime, mapped_column(DateTime(timezone=True), server_default=func.now())
]
UpdatedAt = Annotated[
    datetime,
    mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    ),
]


def status_type(status: type[StrEnum], name: str) -> Enum:
    return Enum(
        status,
        name=name,
        native_enum=False,
        create_constraint=True,
        values_callable=lambda members: [member.value for member in members],
    )


def nonnegative_money(column: str) -> CheckConstraint:
    return CheckConstraint(
        f"{column} >= 0 AND {column} <> 'NaN'::numeric",
        name=f"{column}_nonnegative",
    )
