"""Smoke test: run from the project root with  python -m scripts.check_connection

Verifies: .env loads, driver/connection works, rules load, view returns rows,
and the calculation layer produces sensible output on real data.
Prints the connection string with the password masked.
"""
from app.config import load_settings
from app.db import connect, query
from app.repository import load_inventory_position, load_rules
from app.calc.metrics import enrich_inventory


def main() -> None:
    settings = load_settings()
    print("Connecting with:", settings.safe_connection_string())
    conn = connect(settings)
    try:
        print("Server:", query(conn, "SELECT @@VERSION AS v").iloc[0, 0].splitlines()[0])
        print("Sales rows:", int(query(conn, "SELECT COUNT(*) AS n FROM dbo.Sales").iloc[0, 0]))

        rules = load_rules(conn)
        print("Rules:", rules)

        inv = enrich_inventory(load_inventory_position(conn), rules)
        print("\nInventory positions:", len(inv))
        print("\nStock status (from stored reorder/max levels):")
        print(inv["stock_status"].value_counts().to_string())
        print("\nStockout risk (days of inventory vs supplier lead time):")
        print(inv["stockout_risk"].value_counts().to_string())
        print("\nExcess by days-of-inventory rule:", int(inv["is_excess"].sum()), "positions")
        print("Total inventory value:", round(float(inv["inventory_value"].sum()), 2))
    finally:
        conn.close()


if __name__ == "__main__":
    main()
