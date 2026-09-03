import sqlite3
from config import CONFIG

def get_connection():
    """Establish a connection to the SQLite database."""
    return sqlite3.connect(CONFIG["DATABASE_PATH"])

def execute_query(query, params=()):
    """Execute a query with parameterized inputs."""
    conn = get_connection()
    cursor = conn.cursor()
    try:
        cursor.execute(query, params)
        conn.commit()
        return cursor.fetchall()
    except sqlite3.Error as e:
        print(f"Database error: {e}")
        return []
    finally:
        conn.close()

def get_traffic_statistics():
    """Fetch the latest traffic statistics."""
    query = """
        SELECT vehicle_count, avg_speed, traffic_flow, traffic_density
        FROM traffic_statistics
        ORDER BY timestamp DESC LIMIT 1
    """
    result = execute_query(query)
    if result:
        row = result[0]
        return {
            "vehicle_count": row[0],
            "avg_speed": row[1],
            "traffic_flow": row[2],
            "traffic_density": row[3]
        }
    return {}

def get_traffic_violations():
    """Fetch recent traffic violations."""
    query = """
        SELECT vehicle_id, speed, violation
        FROM traffic_violations
        ORDER BY timestamp DESC
    """
    rows = execute_query(query)
    return [{"vehicle_id": r[0], "speed": r[1], "violation": r[2]} for r in rows]

def get_traffic_overview():
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("""
        SELECT timestamp, vehicle_count, avg_speed
        FROM traffic_overview
        ORDER BY timestamp ASC
    """)
    rows = cursor.fetchall()
    conn.close()
    return {
        "timestamps": [r[0] for r in rows],
        "vehicle_counts": [r[1] for r in rows],
        "avg_speeds": [r[2] for r in rows]
    }

def get_heatmap_data():
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("""
        SELECT heatmap_json
        FROM heatmap_data
        ORDER BY timestamp DESC LIMIT 1
    """)
    row = cursor.fetchone()
    conn.close()
    return {"heatmap": row[0]} if row else {"message": "No data available for the heatmap."}
