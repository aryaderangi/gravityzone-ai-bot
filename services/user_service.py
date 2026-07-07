from database.db import SessionLocal
from database.models import User

def get_or_create_user(tg_user):
    db = SessionLocal()

    user = db.query(User).filter(
        User.telegram_id == tg_user.id
    ).first()

    if not user:
        user = User(
            telegram_id=tg_user.id,
            username=tg_user.username,
            first_name=tg_user.first_name,
            last_name=tg_user.last_name,
        )
        db.add(user)
        db.commit()
        db.refresh(user)

    return user


def get_user_by_id(telegram_id):
    db = SessionLocal()
    return db.query(User).filter(
        User.telegram_id == telegram_id
    ).first()


def update_user(user):
    db = SessionLocal()
    db.merge(user)
    db.commit()
