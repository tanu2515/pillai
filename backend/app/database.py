import os

from dotenv import load_dotenv
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, declarative_base

# Loads backend/.env (DATABASE_URL=postgresql://...) if present, so you don't
# have to set the env var by hand every time you start the server. Falls back
# to the local SQLite file when neither is set, so nothing breaks before
# Postgres is configured. Base.metadata.create_all() (in main.py) creates
# every table on first run against whichever database this points to — no
# migration step needed since there's no production data to preserve.
load_dotenv()
DATABASE_URL = os.environ.get("DATABASE_URL", "sqlite:///./vyavastha.db")

connect_args = {"check_same_thread": False} if DATABASE_URL.startswith("sqlite") else {}
engine = create_engine(DATABASE_URL, connect_args=connect_args)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
