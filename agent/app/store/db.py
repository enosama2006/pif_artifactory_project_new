"""Per-stage persistence (stdlib sqlite3; swap for Postgres via DB_URL later).

Every stage writes when it completes → a crash never loses upstream work and
partial re-runs resume from the last completed stage. The actors table is the
org-level dictionary: reused across documents of the same organisation.
Every human intervention is a durable row — a correction never repeats.
"""
import json
import sqlite3
from pathlib import Path

_SCHEMA = """
CREATE TABLE IF NOT EXISTS runs(
  run_id TEXT PRIMARY KEY, document_id TEXT, status TEXT, created_at TEXT DEFAULT CURRENT_TIMESTAMP);
CREATE TABLE IF NOT EXISTS leaves(
  run_id TEXT, leaf_id TEXT, kind TEXT, text TEXT, section TEXT, row TEXT, col TEXT,
  PRIMARY KEY(run_id, leaf_id));
CREATE TABLE IF NOT EXISTS actors(
  run_id TEXT, actor_id TEXT, name TEXT, kind TEXT, roles TEXT, variants TEXT,
  placeholder TEXT, status TEXT, PRIMARY KEY(run_id, actor_id));
CREATE TABLE IF NOT EXISTS surface_links(
  run_id TEXT, leaf_id TEXT, actor_id TEXT, surface TEXT, start INT, end INT);
CREATE TABLE IF NOT EXISTS decisions(
  run_id TEXT, leaf_id TEXT, decision TEXT, placeholder TEXT, reason TEXT,
  PRIMARY KEY(run_id, leaf_id));
CREATE TABLE IF NOT EXISTS interventions(
  id INTEGER PRIMARY KEY AUTOINCREMENT, run_id TEXT, type TEXT, target TEXT,
  payload TEXT, note TEXT, created_at TEXT DEFAULT CURRENT_TIMESTAMP);
"""


class Store:
    def __init__(self, path: str | Path = "anonymizer.db"):
        self.conn = sqlite3.connect(str(path))
        self.conn.executescript(_SCHEMA)

    def save_run(self, run_id, document_id, status="running"):
        self.conn.execute("INSERT OR REPLACE INTO runs VALUES(?,?,?,CURRENT_TIMESTAMP)",
                          (run_id, document_id, status))
        self.conn.commit()

    def save_leaves(self, run_id, leaves):
        self.conn.executemany(
            "INSERT OR REPLACE INTO leaves VALUES(?,?,?,?,?,?,?)",
            [(run_id, l.leaf_id, l.kind, l.text, l.section, l.row, l.col) for l in leaves])
        self.conn.commit()

    def save_actors(self, run_id, actors):
        self.conn.executemany(
            "INSERT OR REPLACE INTO actors VALUES(?,?,?,?,?,?,?,?)",
            [(run_id, a.actor_id, a.name, a.kind, json.dumps(a.roles, ensure_ascii=False),
              json.dumps(a.variants, ensure_ascii=False), a.placeholder, a.status)
             for a in actors.values()])
        self.conn.commit()

    def save_links(self, run_id, links):
        self.conn.executemany(
            "INSERT INTO surface_links VALUES(?,?,?,?,?,?)",
            [(run_id, l.leaf_id, l.actor_id, l.surface, l.start, l.end) for l in links])
        self.conn.commit()

    def save_decisions(self, run_id, decisions):
        self.conn.executemany(
            "INSERT OR REPLACE INTO decisions VALUES(?,?,?,?,?)",
            [(run_id, d.leaf_id, d.decision, d.placeholder, d.reason) for d in decisions])
        self.conn.commit()

    def add_intervention(self, run_id, type_, target, payload=None, note=""):
        self.conn.execute(
            "INSERT INTO interventions(run_id,type,target,payload,note) VALUES(?,?,?,?,?)",
            (run_id, type_, target, json.dumps(payload or {}, ensure_ascii=False), note))
        self.conn.commit()
