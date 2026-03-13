"""
Generate sample Delhi Metro data for testing
Run this if you don't have actual metro data yet
"""

from pathlib import Path
import csv

# Sample Delhi Metro stations (major interchange stations)
SAMPLE_STATIONS = [
    ("DM001", "Rajiv Chowk", "Blue Line", 28.6328, 77.2197, True, "Yellow Line"),
    ("DM002", "Kashmere Gate", "Red Line", 28.6671, 77.2293, True, "Yellow Line|Violet Line"),
    ("DM003", "Central Secretariat", "Yellow Line", 28.6147, 77.2107, True, "Violet Line"),
    ("DM004", "Hauz Khas", "Yellow Line", 28.5434, 77.2074, True, "Magenta Line"),
    ("DM005", "Cyber City", "Rapid Metro", 28.4955, 77.0883, False, ""),
    ("DM006", "MG Road", "Yellow Line", 28.4818, 77.0868, False, ""),
    ("DM007", "IFFCO Chowk", "Yellow Line", 28.4732, 77.0604, False, ""),
    ("DM008", "HUDA City Centre", "Yellow Line", 28.4595, 77.0709, True, ""),
    ("DM009", "Dwarka Sector 21", "Blue Line", 28.5523, 77.0582, False, ""),
    ("DM010", "Botanical Garden", "Blue Line", 28.5645, 77.3342, True, "Magenta Line"),
]

SAMPLE_LINES = [
    ("Blue Line", "#0066CC", "DM009|DM001|DM010", 3, 6, "05:30", "23:00"),
    ("Yellow Line", "#FFFF00", "DM002|DM001|DM003|DM004|DM006|DM007|DM008", 3, 6, "05:30", "23:00"),
    ("Red Line", "#FF0000", "DM002|...", 4, 7, "05:45", "23:00"),
    ("Violet Line", "#9933CC", "DM002|DM003|...", 4, 7, "05:45", "23:00"),
    ("Magenta Line", "#FF00FF", "DM004|DM010|...", 5, 8, "06:00", "23:00"),
    ("Rapid Metro", "#FF9900", "DM005|...", 5, 10, "06:00", "22:00"),
]

# Sample connections (simplified)
SAMPLE_TIMINGS = [
    ("DM001", "DM002", "Blue Line", 8, 5.2),
    ("DM001", "DM003", "Yellow Line", 3, 2.1),
    ("DM003", "DM004", "Yellow Line", 12, 8.5),
    ("DM004", "DM006", "Yellow Line", 15, 11.2),
    ("DM006", "DM007", "Yellow Line", 4, 3.1),
    ("DM007", "DM008", "Yellow Line", 3, 2.4),
    ("DM004", "DM005", "Magenta Line", 18, 12.5),
]

# Sample fares (distance-based)
SAMPLE_FARES = [
    ("DM001", "DM002", 30),
    ("DM001", "DM003", 20),
    ("DM001", "DM004", 40),
    ("DM001", "DM005", 60),
    ("DM001", "DM008", 60),
    ("DM003", "DM004", 30),
    ("DM004", "DM005", 50),
    ("DM004", "DM008", 40),
]


def generate_sample_data():
    """Generate all sample data files"""
    # Get data directory
    data_dir = Path(__file__).parent.parent / "data" / "delhi_metro"
    data_dir.mkdir(parents=True, exist_ok=True)
    
    # Generate stops.txt
    stops_file = data_dir / "stops.txt"
    with open(stops_file, 'w', newline='', encoding='utf-8') as f:
        writer = csv.writer(f)
        writer.writerow(['station_id', 'name', 'line', 'lat', 'lng', 'interchange', 'connecting_lines'])
        writer.writerows(SAMPLE_STATIONS)
    print(f"✅ Created {stops_file}")
    
    # Generate routes.txt
    routes_file = data_dir / "routes.txt"
    with open(routes_file, 'w', newline='', encoding='utf-8') as f:
        writer = csv.writer(f)
        writer.writerow(['line_name', 'color', 'stations', 'frequency_peak', 'frequency_offpeak', 'start_time', 'end_time'])
        writer.writerows(SAMPLE_LINES)
    print(f"✅ Created {routes_file}")
    
    # Generate timings.txt
    timings_file = data_dir / "timings.txt"
    with open(timings_file, 'w', newline='', encoding='utf-8') as f:
        writer = csv.writer(f)
        writer.writerow(['from_station', 'to_station', 'line', 'duration_minutes', 'distance_km'])
        writer.writerows(SAMPLE_TIMINGS)
    print(f"✅ Created {timings_file}")
    
    # Generate fares.txt
    fares_file = data_dir / "fares.txt"
    with open(fares_file, 'w', newline='', encoding='utf-8') as f:
        writer = csv.writer(f)
        writer.writerow(['from_station', 'to_station', 'fare'])
        writer.writerows(SAMPLE_FARES)
    print(f"✅ Created {fares_file}")
    
    print("\n✅ Sample Delhi Metro data generated successfully!")
    print(f"📁 Location: {data_dir}")
    print("\n⚠️  Note: This is sample data for testing.")
    print("Replace with actual Delhi Metro data for production use.")


if __name__ == "__main__":
    generate_sample_data()