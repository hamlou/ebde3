import sqlite3

try:
    conn = sqlite3.connect('bot/apex.db')
    c = conn.cursor()
    try:
        c.execute("ALTER TABLE active_trades ADD COLUMN risk_pct TEXT DEFAULT '1.0'")
    except Exception as e:
        print(f"risk_pct: {e}")
    try:
        c.execute("ALTER TABLE active_trades ADD COLUMN mt5_status TEXT DEFAULT 'N/A'")
    except Exception as e:
        print(f"mt5_status: {e}")
    conn.commit()
    conn.close()
    print("Migration successful")
except Exception as e:
    print(f"Failed to connect: {e}")
