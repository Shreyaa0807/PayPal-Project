"""Print the PostgreSQL DDL for the schema (handy for judges / manual setup in the Neon SQL editor)."""
from sqlalchemy.dialects import postgresql
from sqlalchemy.schema import CreateIndex, CreateTable

from app import models  # noqa: F401
from app.db import Base

for table in Base.metadata.sorted_tables:
    print(str(CreateTable(table).compile(dialect=postgresql.dialect())).strip() + ";")
    for idx in table.indexes:
        print(str(CreateIndex(idx).compile(dialect=postgresql.dialect())).strip() + ";")
    print()
