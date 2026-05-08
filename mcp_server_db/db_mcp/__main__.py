"""MCP server exposing MySQL and Redis to Claude Code for debugging.

Run with: `python -m db_mcp` (over stdio).
"""
import asyncio
import os
import re
import time
from contextlib import asynccontextmanager
from typing import Any

import aiomysql
import redis.asyncio as aioredis
from mcp.server.fastmcp import FastMCP

MYSQL_HOST = os.getenv("MYSQL_HOST", "127.0.0.1")
MYSQL_PORT = int(os.getenv("MYSQL_PORT", "3306"))
MYSQL_USER = os.getenv("MYSQL_USER", "root")
MYSQL_PASSWORD = os.getenv("MYSQL_PASSWORD", "")
MYSQL_DB = os.getenv("MYSQL_DB", "demoapp")
MYSQL_POOL_MIN = int(os.getenv("MYSQL_POOL_MIN", "1"))
MYSQL_POOL_MAX = int(os.getenv("MYSQL_POOL_MAX", "3"))
REDIS_URL = os.getenv("REDIS_URL", "redis://127.0.0.1:6379")

_mysql_pool: aiomysql.Pool | None = None
_redis: aioredis.Redis | None = None
_pool_lock = asyncio.Lock()


@asynccontextmanager
async def lifespan(server):
    yield
    if _mysql_pool is not None:
        try:
            _mysql_pool.close()
            await _mysql_pool.wait_closed()
        except Exception:
            pass
    if _redis is not None:
        try:
            await _redis.aclose()
        except Exception:
            pass


mcp = FastMCP("database", lifespan=lifespan)


async def _get_mysql_pool() -> aiomysql.Pool:
    global _mysql_pool
    async with _pool_lock:
        if _mysql_pool is None:
            _mysql_pool = await aiomysql.create_pool(
                host=MYSQL_HOST, port=MYSQL_PORT,
                user=MYSQL_USER, password=MYSQL_PASSWORD, db=MYSQL_DB,
                charset="utf8mb4", autocommit=True,
                minsize=MYSQL_POOL_MIN, maxsize=MYSQL_POOL_MAX,
                connect_timeout=5, pool_recycle=3600,
            )
    return _mysql_pool


async def _get_redis() -> aioredis.Redis:
    global _redis
    if _redis is None:
        _redis = aioredis.from_url(
            REDIS_URL, decode_responses=True,
            socket_connect_timeout=3, socket_timeout=5,
        )
    return _redis


# ─── MySQL ──────────────────────────────────────────────────────────────────

@mcp.tool()
async def mysql_query(sql: str, params: list | None = None, limit: int = 100) -> dict[str, Any]:
    """Run a raw SQL query against MySQL and return rows as dicts.

    For SELECT, appends LIMIT if none is present at the end. Non-SELECT
    statements (SHOW, EXPLAIN, DESCRIBE, WITH, ...) are passed through
    unchanged.

    For INSERT/UPDATE/DELETE use mysql_execute() instead.

    params: positional parameters for parameterised queries (%s placeholders).
    limit: max rows returned for SELECT queries without an existing LIMIT.

    Returns {rows, row_count, elapsed_ms}.

    Examples:
      mysql_query('SELECT * FROM users')
      mysql_query('SELECT * FROM users WHERE name LIKE %s', params=['A%'])
      mysql_query('SHOW VARIABLES LIKE "%innodb%"')
      mysql_query('SELECT u.id, p.id FROM users u JOIN posts p')
    """
    sql = sql.strip().rstrip(";")
    upper = sql.upper().lstrip()

    if upper.startswith("SELECT") and not re.search(
        r"\bLIMIT\s+\d+(\s*,\s*\d+|\s+OFFSET\s+\d+)?\s*$", sql, re.IGNORECASE
    ):
        sql = f"{sql} LIMIT {limit}"

    pool = await _get_mysql_pool()
    t0 = time.perf_counter()
    try:
        async with pool.acquire() as conn:
            async with conn.cursor(aiomysql.DictCursor) as cursor:
                await cursor.execute(sql, params or ())
                rows = await cursor.fetchall()
        dt_ms = (time.perf_counter() - t0) * 1000
        return {
            "rows": [dict(r) for r in rows],
            "row_count": len(rows),
            "elapsed_ms": round(dt_ms, 2),
        }
    except Exception as e:
        return {"error": str(e), "sql": sql[:500]}


@mcp.tool()
async def mysql_execute(sql: str, params: list | None = None) -> dict[str, Any]:
    """Run a mutation SQL statement (INSERT/UPDATE/DELETE/ALTER/...) against MySQL.

    Returns {affected_rows, lastrowid, elapsed_ms, sql_preview}.

    Examples:
      mysql_execute("INSERT INTO users (name, email) VALUES (%s, %s)", params=['Alice', 'a@x.com'])
      mysql_execute("UPDATE users SET email = %s WHERE id = %s", params=['b@x.com', 1])
      mysql_execute("DELETE FROM users WHERE id = %s", params=[99])
    """
    sql = sql.strip()
    pool = await _get_mysql_pool()
    t0 = time.perf_counter()
    try:
        async with pool.acquire() as conn:
            async with conn.cursor() as cursor:
                count = await cursor.execute(sql, params or ())
                last_id = cursor.lastrowid
        dt_ms = (time.perf_counter() - t0) * 1000
        return {
            "affected_rows": count,
            "lastrowid": last_id,
            "elapsed_ms": round(dt_ms, 2),
            "sql_preview": sql[:500],
        }
    except Exception as e:
        return {"error": str(e), "sql_preview": sql[:500]}


@mcp.tool()
async def mysql_show_tables() -> dict[str, Any] | list[str]:
    """List all tables in the connected MySQL database."""
    pool = await _get_mysql_pool()
    try:
        async with pool.acquire() as conn:
            async with conn.cursor() as cursor:
                await cursor.execute("SHOW TABLES")
                rows = await cursor.fetchall()
                return [r[0] for r in rows]
    except Exception as e:
        return {"error": str(e)}


@mcp.tool()
async def mysql_describe_table(table: str) -> dict[str, Any]:
    """Show the schema of a MySQL table (columns + types + keys).

    Returns both DESCRIBE output and SHOW CREATE TABLE output.
    """
    if not re.fullmatch(r"[a-zA-Z_][a-zA-Z0-9_]*", table):
        return {"error": f"Invalid table name {table!r}"}

    pool = await _get_mysql_pool()
    try:
        async with pool.acquire() as conn:
            async with conn.cursor(aiomysql.DictCursor) as cursor:
                await cursor.execute(f"DESCRIBE `{table}`")
                columns = await cursor.fetchall()
            async with conn.cursor() as cursor:
                await cursor.execute(f"SHOW CREATE TABLE `{table}`")
                create = await cursor.fetchone()
        return {
            "table": table,
            "columns": [dict(r) for r in columns],
            "create_statement": create[1] if create and len(create) > 1 else None,
        }
    except Exception as e:
        return {"error": str(e), "table": table}


@mcp.tool()
async def mysql_pool_status() -> dict[str, Any]:
    """Return MySQL connection pool statistics (size, free, used)."""
    pool = await _get_mysql_pool()
    return {
        "minsize": pool.minsize,
        "maxsize": pool.maxsize,
        "size": pool.size,
        "freesize": pool.freesize,
    }


# ─── Redis ──────────────────────────────────────────────────────────────────

@mcp.tool()
async def redis_hget(key: str, field: str) -> dict[str, Any]:
    """Get a single field from a Redis hash.

    Example:
      redis_hget('user:1', 'email')
    """
    r = await _get_redis()
    try:
        val = await r.hget(key, field)
        return {"key": key, "field": field, "value": val}
    except Exception as e:
        return {"key": key, "field": field, "error": str(e)}


@mcp.tool()
async def redis_get(key: str, max_items: int = 100) -> dict[str, Any]:
    """Get a Redis key's value. Returns type + value.

    Works for strings, hashes (returns dict), lists (returns list),
    sets (returns list), sorted sets (returns list). Use redis_type() to
    inspect the type first.

    max_items caps how many elements are returned for hash/list/set/zset.
    Returns truncated: true when the actual size exceeds max_items.
    """
    r = await _get_redis()
    try:
        t = await r.type(key)
        if t == "none":
            return {"key": key, "type": "none", "value": None}
        elif t == "string":
            return {"key": key, "type": "string", "value": await r.get(key)}
        elif t == "hash":
            full = await r.hlen(key)
            if full <= max_items:
                return {"key": key, "type": "hash", "value": await r.hgetall(key), "size": full}
            vals: dict[str, str] = {}
            cursor = 0
            while len(vals) < max_items:
                cursor, batch = await r.hscan(key, cursor)
                vals.update(batch)
                if cursor == 0:
                    break
            return {"key": key, "type": "hash",
                    "value": dict(list(vals.items())[:max_items]),
                    "size": full, "truncated": True}
        elif t == "list":
            full = await r.llen(key)
            vals = await r.lrange(key, 0, max_items - 1)
            result: dict[str, Any] = {"key": key, "type": "list", "value": vals, "size": full}
            if full > max_items:
                result["truncated"] = True
            return result
        elif t == "set":
            full = await r.scard(key)
            if full <= max_items:
                vals = await r.smembers(key)
                return {"key": key, "type": "set", "value": list(vals), "size": full}
            vals_set: set[str] = set()
            cursor = 0
            while len(vals_set) < max_items:
                cursor, batch = await r.sscan(key, cursor)
                vals_set.update(batch)
                if cursor == 0:
                    break
            return {"key": key, "type": "set", "value": list(vals_set)[:max_items],
                    "size": full, "truncated": True}
        elif t == "zset":
            full = await r.zcard(key)
            vals = await r.zrange(key, 0, max_items - 1, withscores=True)
            result: dict[str, Any] = {"key": key, "type": "zset", "value": vals, "size": full}
            if full > max_items:
                result["truncated"] = True
            return result
        else:
            return {"key": key, "type": t, "value": None}
    except Exception as e:
        return {"key": key, "error": str(e)}


@mcp.tool()
async def redis_set(key: str, value: str, ttl: int | None = None) -> dict[str, Any]:
    """Set a Redis string key.

    ttl: optional expiry in seconds.

    Examples:
      redis_set('debug:flag', '1', ttl=60)
      redis_set('user:123', '{"name":"alice"}')
    """
    r = await _get_redis()
    try:
        if ttl:
            await r.setex(key, ttl, value)
        else:
            await r.set(key, value)
        return {"key": key, "ok": True, "ttl": ttl}
    except Exception as e:
        return {"key": key, "error": str(e)}


@mcp.tool()
async def redis_keys(pattern: str, limit: int = 200) -> dict[str, Any]:
    """List Redis keys matching a glob pattern using SCAN (safe for production).

    Examples:
      redis_keys('*')
      redis_keys('user:*')
      redis_keys('cache:*:hits')
    """
    r = await _get_redis()
    try:
        keys: list[str] = []
        cursor = 0
        while True:
            cursor, batch = await r.scan(cursor, match=pattern, count=100)
            keys.extend(batch)
            if cursor == 0 or len(keys) >= limit:
                break
        return {"pattern": pattern, "count": len(keys[:limit]), "keys": keys[:limit]}
    except Exception as e:
        return {"pattern": pattern, "error": str(e)}


@mcp.tool()
async def redis_delete(keys: list[str]) -> dict[str, Any]:
    """Delete one or more Redis keys.

    Example:
      redis_delete(['debug:flag', 'cache:stale'])
    """
    r = await _get_redis()
    try:
        count = await r.delete(*keys)
        return {"deleted": count, "keys": keys}
    except Exception as e:
        return {"keys": keys, "error": str(e)}


@mcp.tool()
async def redis_exists(keys: list[str]) -> dict[str, Any]:
    """Check if Redis keys exist. Returns count.

    Example:
      redis_exists(['user:1', 'user:999'])
    """
    r = await _get_redis()
    try:
        count = await r.exists(*keys)
        return {"exists_count": count, "keys": keys}
    except Exception as e:
        return {"keys": keys, "error": str(e)}


@mcp.tool()
async def redis_ttl(key: str) -> dict[str, Any]:
    """Get the TTL (time-to-live) of a Redis key in seconds.
    Returns -2 if key doesn't exist, -1 if no expiry.
    """
    r = await _get_redis()
    try:
        ttl_val = await r.ttl(key)
        return {"key": key, "ttl_seconds": ttl_val}
    except Exception as e:
        return {"key": key, "error": str(e)}


@mcp.tool()
async def redis_type(key: str) -> dict[str, Any]:
    """Get the Redis data type of a key (string, hash, list, set, zset, none)."""
    r = await _get_redis()
    try:
        return {"key": key, "type": await r.type(key)}
    except Exception as e:
        return {"key": key, "error": str(e)}


@mcp.tool()
async def redis_info(section: str = "server") -> dict[str, Any]:
    """Run Redis INFO command. section: server, clients, memory, stats, replication, cpu, keyspace, all."""
    r = await _get_redis()
    try:
        raw = await r.info(section)
        return {"section": section, "info": raw}
    except Exception as e:
        return {"section": section, "error": str(e)}


@mcp.tool()
async def redis_hset(key: str, field: str, value: str) -> dict[str, Any]:
    """Set a single field in a Redis hash.

    Example:
      redis_hset('user:1', 'email', 'alice@example.com')
    """
    r = await _get_redis()
    try:
        count = await r.hset(key, field, value)
        return {"key": key, "field": field, "new_field": count == 1, "ok": True}
    except Exception as e:
        return {"key": key, "error": str(e)}


@mcp.tool()
async def redis_lrange(key: str, start: int = 0, stop: int = 99) -> dict[str, Any]:
    """Get a range of elements from a Redis list.

    Defaults to first 100 elements. Use explicit start/stop for paging.

    Example:
      redis_lrange('queue:tasks', 0, 9)
    """
    r = await _get_redis()
    try:
        vals = await r.lrange(key, start, stop)
        full_len = await r.llen(key)
        return {
            "key": key, "start": start, "stop": stop,
            "values": vals, "length": len(vals), "total": full_len,
        }
    except Exception as e:
        return {"key": key, "error": str(e)}


@mcp.tool()
async def redis_smembers(key: str, max_items: int = 100) -> dict[str, Any]:
    """Get members of a Redis set.

    max_items caps how many members are returned (uses SSCAN for large sets).
    Returns truncated: true when the actual size exceeds max_items.
    """
    r = await _get_redis()
    try:
        full = await r.scard(key)
        if full <= max_items:
            vals = await r.smembers(key)
            return {"key": key, "type": "set", "members": list(vals), "size": full}
        vals_set: set[str] = set()
        cursor = 0
        while len(vals_set) < max_items:
            cursor, batch = await r.sscan(key, cursor)
            vals_set.update(batch)
            if cursor == 0:
                break
        return {"key": key, "type": "set", "members": list(vals_set)[:max_items],
                "size": full, "truncated": True}
    except Exception as e:
        return {"key": key, "error": str(e)}


if __name__ == "__main__":
    mcp.run()
