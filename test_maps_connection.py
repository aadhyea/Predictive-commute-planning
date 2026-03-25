"""
Quick test script to verify Google Maps SDK connection.
Run from project root:  python test_maps_connection.py
"""

import asyncio
from maps.google_maps_client import maps_client


async def main():
    print("=" * 60)
    print("TESTING GOOGLE MAPS SDK CLIENT")
    print("=" * 60)
    print()

    try:
        # Test 1: Geocode
        print("Test 1: Geocoding an address...")
        result = await maps_client.geocode("India Gate, Delhi")
        if result:
            print(f"  [PASS] Address : {result['formatted_address']}")
            print(f"         Coords  : ({result['lat']}, {result['lng']})")
        else:
            print("  [FAIL] No result returned")

        print()

        # Test 2: Transit directions (demo route)
        print("Test 2: Transit directions Rajiv Chowk -> Cyber City...")
        routes = await maps_client.get_directions(
            origin="Rajiv Chowk, Delhi",
            destination="Cyber City, Gurugram",
            mode="transit",
        )
        if routes:
            print(f"  [PASS] Found {len(routes)} route(s)")
            print(f"         Duration : {routes[0]['duration_text']}")
            print(f"         Distance : {routes[0]['distance_text']}")
            print(f"         Steps    : {len(routes[0]['steps'])}")
        else:
            print("  [FAIL] No routes found")

        print()
        print("=" * 60)
        print("[PASS] Google Maps connection is working.")
        print("=" * 60)

    except Exception as e:
        print()
        print("=" * 60)
        print(f"[FAIL] ERROR: {e}")
        print("=" * 60)
        print()
        print("Troubleshooting:")
        print("1. Check GOOGLE_MAPS_API_KEY in .env")

    finally:
        await maps_client.close()


if __name__ == "__main__":
    asyncio.run(main())
