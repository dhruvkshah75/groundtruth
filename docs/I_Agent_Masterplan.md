# I, Agent: The Complete Beginner-to-Master Execution Guide

Welcome to the **I, Agent** project! Since you and your team are still learning the concepts of AI, this document is written to explain everything from the ground up. It will serve as your ultimate textbook, roadmap, and task manager for the entire semester.

---

## Part 1: "Explain It Like I'm 5" - What Are We Building?

Imagine you are driving a car using a GPS navigation app. 
* Your **Map (The Knowledge/Declarative Layer)** says the road ahead is completely clear.
* Your **Eyes (The Sensorimotor Layer)** see a giant fallen tree blocking the road.
* Your **Brain (The Procedural LLM Layer)** has to make a decision. 

If your brain blindly trusts the map and ignores your eyes, you crash. If your brain trusts your eyes, it needs to update the map to remember the tree is there so you don't make the same mistake tomorrow.

**The Problem with standard AI (like ChatGPT):**
Standard AI models don't have "eyes." They only have the "map" (their training data and memory). When put into a robot, if their memory says the road is clear, they will hallucinate and insist the road is clear, even if the robot's sensors are screaming about an obstacle. This is a lack of **Semantic Grounding**.

**The Goal of This Project:**
You are building an AI system split into three distinct, isolated parts that must talk to each other to solve conflicts between what the AI *remembers* (the map), what the AI *senses* (the eyes), and what the *user tells it* (the passenger).

---

## Part 2: Core Concepts Explained for Beginners

Before writing any code, your team needs to understand the "Knowledge Stack" of this project.

### 1. The ReAct Pattern (Reason + Act)
Standard AI just takes a prompt and spits out an answer. In this project, the AI is an "Agent." An Agent uses a loop called **ReAct**.
* **Thought:** The AI thinks out loud. *"The user asked me if the path is clear. I should check my map first."*
* **Action:** The AI calls a Python function: `check_map("path_ahead")`
* **Observation:** The function returns `True`.
* **Thought:** *"The map says it is clear. But I need to be sure. I will check my physical sensors."*
* **Action:** `read_lidar_sensor()`
* **Observation:** `Obstacle detected at 12cm`.
* **Thought:** *"Uh oh! Conflict detected. My sensors override my map. I will update the map."*

### 2. Knowledge Graphs (NetworkX)
Instead of storing memories as text, you will store them as a **Graph**. 
Think of a graph as circles (Nodes) connected by arrows (Edges).
* Node 1: `Robot`
* Node 2: `Room_101`
* Edge linking them: `located_at`
In Python, we use a free library called **NetworkX** to easily draw and search these circles and arrows.

### 3. Data Provenance (SQLite Database)
"Provenance" is just a fancy word for **History/Origin**. 
When the AI remembers something, it shouldn't just remember the fact; it needs to remember *who* said it, *when*, and *how confident* it is. We store this in a standard SQL database (SQLite).

### 4. Doxastic Logic & Belief Revision
This is the logic of how beliefs change. If the AI is 100% confident the path is clear (because of the map), and 100% confident the path is blocked (because of the sensor), it has a conflict. Belief revision is the mathematical rule you write to say: *"Always trust the live sensor more than the static map."*

---

## Part 3: The "Interface-First" Architecture

Since this is a 2-3 month long project with 4 developers, you cannot throw all your code into one file. You must use **Interface-First Design** (Separation of Concerns).

No one is allowed to directly access someone else's code. For example, the Procedural (LLM) Layer should never write raw SQL to update the database. Instead, the Declarative team provides a clean method called `.update_belief()`, and the LLM team only calls that. This prevents merge conflicts and spaghetti code.

All data passed between layers must be strictly defined using **Pydantic** data contracts.

---

## Part 4: Team Roles & Assignments (For 4 Members)

### Role 1: The Orchestrator (Tier 2 Lead)
* **Your Job:** You are building the "Brain." You will write the Python script that talks to the Large Language Model (like Llama-3 via the free Groq API) and sets up the ReAct Loop.
* **Beginner Homework:** Learn how to get an API key from Groq (console.groq.com) and write a small script to send a message. Research "OpenAI Function Calling".

### Role 2: The Epistemologist (Tier 1 Lead)
* **Your Job:** You are building the "Map." You manage the Database (SQLite) and the Knowledge Graph (NetworkX). 
* **Beginner Homework:** Follow a 10-minute beginner tutorial on Python `sqlite3` to learn how to create a table and insert rows. Follow a tutorial on Python `networkx`.

### Role 3: The Simulation Engineer (Tier 3 Lead)
* **Your Job:** You are building the "Eyes" and the "World." You don't use real robots; you will write a Python class (a mock environment) that fakes a robot moving in a grid.
* **Beginner Homework:** Learn Python Object-Oriented Programming (Classes). Create a `MockEnvironment` class that stores a 2D grid and can return what is in front of the robot.

### Role 4: The Integration & UI Lead (Testing & Dashboard)
* **Your Job:** You are the glue. You write the Pydantic data contracts that Roles 1, 2, and 3 must follow. You also build the visual dashboard to make the project look amazing for grading.
* **Beginner Homework:** Learn the basics of `pydantic` in Python. Check out **Streamlit** (streamlit.io).

---

## Part 5: Step-by-Step Semester Roadmap

* **Phase 1: Setup & Mocking (Weeks 1-3):** Build the dummy environment and the strict data rules. No AI is used yet. Role 3 finishes the Fake Robot Environment, Role 2 finishes the SQLite Database schema.
* **Phase 2: Building the Brain (Weeks 4-7):** Get the LLM to talk to the fake robot and the database. Role 1 writes the ReAct loop and "Tools".
* **Phase 3: Solving the Conflicts (Weeks 8-11):** Solve Scenario A (Groundedness conflict between map and sensor) and Scenario B (Perspective conflict between user, sensor, and database).
* **Phase 4: Final Polish & Dashboard (Weeks 12-14):** Get an A+ on the project. Role 4 finishes the Streamlit live dashboard.

---

## Part 6: Elevating the Plan (Standout A+ Features)

To ensure this project outshines everything else in the class, we will bake these concepts into the design from Day 1:

1. **Time-Decaying Memory (Doxastic Fading):**
   Instead of believing a fact with 100% confidence forever, confidence mathematically decays over time. If the agent saw a red box 2 hours ago, its confidence drops to 60%. If it sees it again, it refreshes to 100%. This simulates real biological memory.
2. **The "Why Do You Believe This?" Audit Trail:**
   Build an "Audit Mode." When the user asks *"Why do you believe the path is blocked?"*, the agent queries the SQLite database and generates a step-by-step trace: *"I believe this because at 10:04 AM, my LiDAR sensor returned a 12cm reading, overriding my 09:00 AM map file."*
3. **Environment "Perturbations" (Tier 3):**
   Add a "Flickering Lights" or "Sensor Noise" mode that causes the simulated camera to occasionally report the wrong colors. The LLM then has to use statistical reasoning to figure out the truth.
4. **Time-Travel Graphs:** We will save a history of every graph state so the AI can time-travel and say *"I used to think the path was clear at 10:00 AM, but I changed my mind at 10:05 AM."*
5. **The Live UI:** The Streamlit dashboard will visually blow the evaluators away by showing all 3 layers running live side-by-side in the browser.

---

## Part 7: Your Very First Commit (Day 1)

For your very first commit, do not touch the AI, the LLM, or the NetworkX graph. Start at the very bottom of the foundation: **The Database Connector and Provenance Schema**. 

Connecting to the database is the perfect Day 1 task. 

**What you should build:**
Create a file (e.g., `src/declarative/db_manager.py`) and design a robust class to handle your SQLite connection.

**1. Create a `DatabaseManager` Class:** 
It should take a database path when initialized (so during testing, you can pass a temporary test database, and during production, you use the real one).

**2. Write an `initialize_tables()` method:**
When the class starts, it should safely create your tables if they don't exist. 

**3. Design the `provenance_log` SQL Table:**
Define a table with these exact columns to support the advanced features:
* `fact_id` (Primary Key, unique string)
* `subject` (Text, e.g., "Robot")
* `predicate` (Text, e.g., "located_at")
* `object` (Text, e.g., "Room_101")
* `source_agent` (Text, e.g., "lidar_sensor", "user_prompt", "static_map")
* `confidence_score` (Float, from 0.0 to 1.0)
* `created_at` (Timestamp, defaults to current time)
* `superseded_by` (Text, optional. If a belief is overwritten, we don't delete it; we just point this to the new `fact_id` so we keep the history!)

**4. Write a quick test block at the bottom:**
Write a small `if __name__ == "__main__":` block that initializes the class, creates the database file, inserts one fake fact, and prints it out to prove it worked.
