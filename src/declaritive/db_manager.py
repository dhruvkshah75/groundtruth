"""sqlite database serves as the agent's permanent, long-term audit log required for conflict resolution"""

import sqlite3
import uuid
from pathlib import Path

# resolves the root of the project
PROJECT_PATH = Path(__file__).resolve().parent.parent.parent

# default db path is build wrt to the project path 
DEFAULT_DB_PATH = PROJECT_PATH / "data" / "agent_memory.sqlite"


class DatabaseManager:
    def __init__(self, dp_path: str = str(DEFAULT_DB_PATH)):
        self.db_path = dp_path
        self.initialise_tables()

    def _get_connection(self):
        """creates and returns the safe connection to the sqlite db"""

        conn = sqlite3.connect(self.db_path)
        # this allows to access the columns in the db
        conn.row_factory = sqlite3.Row
        return conn

    def initialise_tables(self):
        """ The initialise tables method acts as the Architect.
        it's job is to run the specific sql command `CREATE TABLE IF NOT EXISTS`
        """

        # sql query to create a table in the database 
        create_table_query = """
        CREATE TABLE IF NOT EXISTS memory_log (
            fact_id TEXT PRIMARY KEY,
            subject TEXT NOT NULL,
            predicate TEXT NOT NULL,
            object TEXT NOT NULL,                                                                                                                                               
            source_agent TEXT NOT NULL,                                                                                                                                         
            confidence_score REAL NOT NULL,                                                                                                                                     
            created_at TIMESTAMP NOT NULL,                                                                                                                                      
            superseded_by TEXT 
        );
        """

        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(create_table_query)
            conn.commit()


    def get_all_memories(self):
        """Fetches all the rows from the daatabse table memory_log"""

        select_query = "SELECT * FROM memory_log ORDER BY created_at DESC"

        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(select_query)

            # fetch all the rows from result of query and store as dictionary 
            return [dict(row) for row in cursor.fetchall()]



if __name__ == "__main__":
    db = DatabaseManager()
