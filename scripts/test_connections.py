"""
Connection & smoke test for all major components.
Run from project root:  python scripts/test_connections.py
"""

import asyncio
import io
import sys
import traceback
from pathlib import Path

# Add project root to path so imports work when run from scripts/
PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

# Force UTF-8 output on Windows terminals
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

PASS = "[PASS]"
FAIL = "[FAIL]"
WARN = "[WARN]"


# ============================================================
# 1. CONFIG
# ============================================================

def test_config():
    print("\n--- 1. Config / Environment ---")
    try:
        from config import settings
        checks = {
            "ANTHROPIC_API_KEY":        bool(settings.ANTHROPIC_API_KEY),
            "GOOGLE_MAPS_API_KEY":      bool(settings.GOOGLE_MAPS_API_KEY),
            "OPENWEATHER_API_KEY":      bool(settings.OPENWEATHER_API_KEY),
            "SUPABASE_URL":             bool(settings.SUPABASE_URL),
            "SUPABASE_KEY":             bool(settings.SUPABASE_KEY),
            "SUPABASE_SERVICE_ROLE_KEY":bool(settings.SUPABASE_SERVICE_ROLE_KEY),
        }
        all_ok = True
        for key, ok in checks.items():
            icon    = PASS if ok else FAIL
            val     = getattr(settings, key, None)
            preview = f"{str(val)[:12]}..." if val else "MISSING"
            print(f"  {icon} {key}: {preview}")
            if not ok:
                all_ok = False
        return all_ok
    except Exception as e:
        print(f"  {FAIL} Config failed to load: {e}")
        traceback.print_exc()
        return False


# ============================================================
# 2. DELHI METRO SERVICE (GTFS)
# ============================================================

def test_metro_service():
    print("\n--- 2. Delhi Metro Service (GTFS) ---")
    try:
        from services.metro_service import delhi_metro

        n_stations    = len(delhi_metro.stations)
        n_lines       = len(delhi_metro.lines)
        n_conns       = len(delhi_metro.connections)
        n_interchange = len(delhi_metro.get_interchange_stations())

        print(f"  {PASS} Stations loaded  : {n_stations}")
        print(f"  {PASS} Lines loaded     : {n_lines} -> {list(delhi_metro.lines.keys())}")
        print(f"  {PASS} Connections built: {n_conns}")
        print(f"  {PASS} Interchange stns : {n_interchange}")

        rchowk = delhi_metro.find_station_by_name("Rajiv Chowk")
        if rchowk:
            print(f"  {PASS} find_station_by_name('Rajiv Chowk') -> {rchowk.name} ({rchowk.line})")
        else:
            print(f"  {WARN} 'Rajiv Chowk' not found - check stops.txt")

        nearest = delhi_metro.find_nearest_station(28.6129, 77.2295)
        if nearest:
            print(f"  {PASS} Nearest station to India Gate -> {nearest.name}")

        return n_stations > 0 and n_lines > 0
    except Exception as e:
        print(f"  {FAIL} Metro service error: {e}")
        traceback.print_exc()
        return False


# ============================================================
# 3. SUPABASE
# ============================================================

def test_supabase():
    print("\n--- 3. Supabase Connection ---")
    try:
        from database.supabase_client import get_client
        db = get_client()

        resp  = db.client.table("user_preferences").select("user_id").limit(1).execute()
        count = len(resp.data) if resp.data else 0
        print(f"  {PASS} Supabase connected successfully")
        print(f"  {PASS} user_preferences table reachable (rows: {count})")
        if count == 0:
            print(f"  {WARN} Table empty - run 001_initial_schema.sql in Supabase SQL Editor first")
        return True
    except Exception as e:
        msg = str(e)
        if "relation" in msg and "does not exist" in msg:
            print(f"  {WARN} Supabase connected but tables not created yet")
            print(f"       -> Run database/migrations/001_initial_schema.sql in Supabase SQL Editor")
            return True   # connection itself works
        print(f"  {FAIL} Supabase error: {e}")
        traceback.print_exc()
        return False


# ============================================================
# 4. GOOGLE MAPS
# ============================================================

async def test_maps():
    print("\n--- 4. Google Maps ---")
    try:
        from maps.google_maps_client import maps_client

        result = await maps_client.geocode("India Gate, New Delhi")
        if result:
            print(f"  {PASS} Geocode OK: {result.get('formatted_address')}")
            print(f"       Coords: ({result.get('lat'):.4f}, {result.get('lng'):.4f})")
        else:
            print(f"  {FAIL} Geocode returned no result")
            return False

        routes = await maps_client.get_directions(
            origin="Rajiv Chowk Metro Station, Delhi",
            destination="Cyber City, Gurugram",
            mode="transit",
        )
        if routes:
            r = routes[0]
            print(f"  {PASS} Directions OK: {r.get('duration_text')} / {r.get('distance_text')}")
            print(f"       Steps: {len(r.get('steps', []))}")
        else:
            print(f"  {WARN} Directions returned no routes (check API key / quota)")

        return True
    except Exception as e:
        print(f"  {FAIL} Google Maps error: {e}")
        traceback.print_exc()
        return False
    finally:
        try:
            from maps.google_maps_client import maps_client
            await maps_client.close()
        except Exception:
            pass


# ============================================================
# MAIN
# ============================================================

async def main():
    print("=" * 55)
    print("  DELHI COMMUTE AGENT - CONNECTION TESTS")
    print("=" * 55)

    results = {
        "Config":        test_config(),
        "Metro Service": test_metro_service(),
        "Supabase":      test_supabase(),
        "Google Maps":   await test_maps(),
    }

    print("\n" + "=" * 55)
    print("  SUMMARY")
    print("=" * 55)
    all_pass = True
    for name, ok in results.items():
        icon = PASS if ok else FAIL
        print(f"  {icon}  {name}")
        if not ok:
            all_pass = False

    print()
    if all_pass:
        print("  All checks passed - ready to build the agent!")
    else:
        print("  Fix the failing checks above, then re-run.")
    print()
    return 0 if all_pass else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
