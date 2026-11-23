from sqlalchemy import func
from sqlalchemy.sql.elements import ColumnElement

from app.models import User
from app.schemas.parameters import UserSearch


def user_filters(query: UserSearch) -> list[ColumnElement[bool]]:
    predicates = []
    if query.name is not None:
        predicates.append(User.full_name.icontains(query.name, autoescape=True))
    if query.city is not None:
        predicates.append(func.lower(User.city) == query.city.lower())
    if query.email is not None:
        predicates.append(User.email.icontains(query.email, autoescape=True))
    if query.status is not None:
        predicates.append(User.status == query.status)
    return predicates
