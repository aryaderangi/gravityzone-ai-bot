from database.db import SessionLocal
from database.models import Node

from ai.ollama import generate


def ask(node_name, model, prompt):

    db = SessionLocal()

    node = db.query(Node).filter(Node.name == node_name).first()

    if node is None:
        raise Exception("Node not found")

    return generate(
        node.url,
        model,
        prompt
    )
