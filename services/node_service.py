from database.db import SessionLocal
from database.models import Node

def get_node(name):
    db = SessionLocal()

    return db.query(Node).filter(
        Node.name == name
    ).first()
