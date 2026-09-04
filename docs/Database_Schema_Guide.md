# Database Schema & Architecture Guide

This document explains how the Declarative Layer (Tier 1) stores memories, why we use SQLite, and exactly what each column in our database does.

## 1. Architectural Rationale: Why SQLite?
While client-server databases (like PostgreSQL) are standard for web apps, they are architecturally incorrect for a single, stateful cognitive agent. We are using SQLite for strict system-design reasons:

1. **Elimination of Network Latency:** The ReAct loop requires ultra-fast, synchronous reads to resolve epistemic conflicts. SQLite runs in the same memory space as the Python process, eliminating socket overhead, network hops, and serialization delays.
2. **ACID Compliance for Epistemic Integrity:** SQLite guarantees full ACID (Atomicity, Consistency, Isolation, Durability) transactions. If the agent's LLM loop crashes mid-thought while updating a belief, the database rolls back safely, preventing corrupted memory states.
3. **In-Memory Testing Harness:** For the 10-scenario grading suite, SQLite allows the `db_path` to be set to `:memory:`. This lets the automated tests build and destroy the entire memory graph instantly without touching the hard drive.
4. **State Snapshotting:** Because the entire database is a single `.sqlite` file, the agent's precise epistemic state at a given timestamp can be copied, backed up, and version-controlled. This makes debugging hallucinations reproducible.

## 2. Where Should the Database File Live?
Since SQLite stores everything in a single file, you must be careful not to push this file to GitHub, or you will cause merge conflicts with your teammates' local databases.

**The Rule:**
The database file should be saved in a dedicated `data/` folder in the root of your project:
```text
groundtruth/
├── data/                  <-- Put agent_memory.sqlite here!
│   └── agent_memory.sqlite
├── src/
│   └── declarative/
│       └── db_manager.py  <-- The code lives here
├── docs/
└── .gitignore
```

### How the `DatabaseManager` Finds the File
Because the code (`db_manager.py`) and the database (`agent_memory.sqlite`) live in different folders, you cannot just hardcode `db_path="data/agent_memory.sqlite"`. If a teammate runs the script from inside the `src` folder, Python will look in the wrong place and create a duplicate database.

To fix this architecturally, the `DatabaseManager` class must use Python's `pathlib` to dynamically resolve the absolute path to the project root, no matter where the script is executed from:

```python
from pathlib import Path

# This finds the absolute path of the root 'groundtruth' directory
PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent

# This safely points to groundtruth/data/agent_memory.sqlite
DEFAULT_DB_PATH = PROJECT_ROOT / "data" / "agent_memory.sqlite"

class DatabaseManager:
    def __init__(self, db_path: str = str(DEFAULT_DB_PATH)):
        self.db_path = db_path
        self.initialize_tables()
```

---

## 3. The `memory_log` Table Explained

The AI doesn't just store "facts." It stores the **history and origin** (the provenance) of every fact. This allows the AI to resolve conflicts when the sensor disagrees with the map.

Here is the exact breakdown of the table columns:

### `fact_id` (TEXT, Primary Key)
* **What it is:** A mathematically unique string (UUID) assigned to every new memory so we can always reference it later.
* **Example:** `"f47ac10b-58cc-4372-a567-0e02b2c3d479"`

### `subject` (TEXT)
* **What it is:** The "Who" or "What" the fact is about (Node 1 in the Knowledge Graph).
* **Example:** `"Robot"` or `"Box_01"`

### `predicate` (TEXT)
* **What it is:** The relationship or action connecting the subject to the object (The Arrow in the Knowledge Graph).
* **Example:** `"located_at"` or `"color_is"` or `"path_to_door"`

### `object` (TEXT)
* **What it is:** The target or value of the fact (Node 2 in the Knowledge Graph).
* **Example:** `"Room_101"` or `"Red"` or `"Blocked"`

### `source_agent` (TEXT)
* **What it is:** The most important column for this project. It tracks *who* asserted this fact. This allows the LLM to decide who to trust.
* **Example:** `"lidar_sensor"`, `"camera_vision"`, `"user_prompt"`, or `"default_static_map"`.

### `confidence_score` (REAL)
* **What it is:** A decimal number from `0.0` to `1.0` representing how much the AI trusts this memory.
* **Example:** `0.9` (90% confident). If the map says the path is clear but the sensor says it is blocked, the AI will lower the map's confidence to `0.1` and log the sensor's fact at `0.99`.

### `created_at` (TIMESTAMP)
* **What it is:** The exact millisecond the fact was recorded.
* **Example:** `"2026-09-05T10:04:22.123"`

### `superseded_by` (TEXT, Optional)
* **What it is:** The "Time Travel" column. If the Robot moves from Room 101 to Room 102, we **do not delete** the old memory. We keep it, but we put the new `fact_id` in this column.
* **Example:** If the old fact is ID `111` and the new fact is ID `222`, row `111` will have `"222"` in its `superseded_by` column. This lets the AI trace exactly how and when its beliefs changed over time!

---

## Example Row in the Database
If the LiDAR sensor detects that the path is blocked at 10:00 AM, the database row will look like this:

| fact_id | subject | predicate | object | source_agent | confidence_score | created_at | superseded_by |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- |
| `abc-123` | `path_ahead` | `status_is` | `blocked` | `lidar_sensor` | `0.95` | `10:00:00` | `NULL` |
