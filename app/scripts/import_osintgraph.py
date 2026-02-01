#!/usr/bin/env python3
import argparse
import json
import os
import sys
from datetime import datetime
from zoneinfo import ZoneInfo
from urllib.request import Request, urlopen

from neo4j import GraphDatabase

CREDENTIALS_PATH = "/home/apps/.local/share/pipx/venvs/osintgraph/lib/python3.11/site-packages/osintgraph/credentials.json"


def _read_neo4j_credentials():
    if os.path.exists(CREDENTIALS_PATH):
        try:
            with open(CREDENTIALS_PATH, "r", encoding="utf-8") as fh:
                data = json.load(fh)
            return data.get("NEO4J_URI"), data.get("NEO4J_USERNAME"), data.get("NEO4J_PASSWORD")
        except Exception:
            pass
    auth = None
    if os.path.exists("/srv/secrets/neo4j.env"):
        with open("/srv/secrets/neo4j.env", "r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if line.startswith("NEO4J_AUTH="):
                    auth = line.split("=", 1)[1].strip()
                    break
    if auth and "/" in auth:
        user, password = auth.split("/", 1)
        return "bolt://127.0.0.1:7687", user, password
    return None, None, None


def _fetch_users(driver, query, username):
    with driver.session(database="neo4j") as session:
        result = session.run(query, username=username)
        return [record["username"] for record in result if record.get("username")]


def main():
    parser = argparse.ArgumentParser(description="Import osintgraph followers/following into InstaLab")
    parser.add_argument("--target", required=True, help="Target username")
    parser.add_argument("--login", required=True, help="InstaLab login_username to attribute")
    parser.add_argument("--api-base", default="http://127.0.0.1:5000", help="InstaLab API base")
    parser.add_argument("--neo4j-uri", default=None, help="Neo4j bolt URI")
    parser.add_argument("--neo4j-user", default=None, help="Neo4j username")
    parser.add_argument("--neo4j-pass", default=None, help="Neo4j password")
    parser.add_argument("--allow-incomplete", action="store_true", help="Import even if followers/followees are incomplete")
    parser.add_argument("--timestamp", default=None, help="Run timestamp (YYYY-MM-DD_HH-MM-SS). Defaults to now ET.")
    args = parser.parse_args()

    uri, user, password = _read_neo4j_credentials()
    uri = args.neo4j_uri or uri or "bolt://127.0.0.1:7687"
    user = args.neo4j_user or user or "neo4j"
    password = args.neo4j_pass or password
    if not password:
        raise SystemExit("Neo4j password missing. Run osintgraph setup neo4j first.")

    driver = GraphDatabase.driver(uri, auth=(user, password))
    try:
        driver.verify_connectivity()
    except Exception as exc:
        raise SystemExit(f"Neo4j connection failed: {exc}")

    target = args.target.strip().lstrip("@")
    followers_query = """
    MATCH (f:Person)-[:FOLLOWS]->(u:Person {username: $username})
    RETURN f.username AS username
    """
    followees_query = """
    MATCH (u:Person {username: $username})-[:FOLLOWS]->(f:Person)
    RETURN f.username AS username
    """
    if not args.allow_incomplete:
        with driver.session(database="neo4j") as session:
            result = session.run(
                """
                MATCH (p:Person {username: $username})
                RETURN p._followers_complete AS followers,
                       p._followees_complete AS followees
                LIMIT 1
                """,
                username=target,
            )
            record = result.single()
            if not record or not record.get("followers") or not record.get("followees"):
                print(json.dumps({"skipped": True, "reason": "incomplete_followers_or_followees"}))
                return

    followers = _fetch_users(driver, followers_query, target)
    followees = _fetch_users(driver, followees_query, target)

    tz = ZoneInfo("America/New_York")
    timestamp = args.timestamp or datetime.now(tz).strftime("%Y-%m-%d_%H-%M-%S")

    payload = json.dumps(
        {
            "target_username": target,
            "login_username": args.login.strip(),
            "timestamp": timestamp,
            "followers": followers,
            "followees": followees,
        }
    ).encode("utf-8")

    req = Request(
        f"{args.api_base.rstrip('/')}/api/import/osintgraph",
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urlopen(req, timeout=30) as resp:
            body = resp.read().decode("utf-8")
            print(body)
    except Exception as exc:
        raise SystemExit(f"Import failed: {exc}")


if __name__ == "__main__":
    main()
