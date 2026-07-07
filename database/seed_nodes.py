from database.db import SessionLocal
from database.models import Node

db = SessionLocal()

nodes = [
    {
        "name": "node1",
        "url": "https://ol.gravityzoneshop.top",
        "status": "online"
    },
    {
        "name": "node2",
        "url": "https://ol.gravityzone.click",
        "status": "online"
    },
    {
        "name": "node3",
        "url": "https://oll.gravityzoneshop.top",
        "status": "online"
    }
]

for n in nodes:
    if not db.query(Node).filter(Node.name == n["name"]).first():
        db.add(Node(**n))

db.commit()

print("Nodes Added")
