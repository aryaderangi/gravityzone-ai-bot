from database.db import engine
from database.models import Base

print("Creating tables...")

Base.metadata.create_all(bind=engine)

print("Done.")
