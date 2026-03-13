# Setting Up Smithery Google Maps MCP Server

## What is Smithery?

Smithery (https://smithery.ai) provides pre-built MCP servers for popular APIs. You're using their Google Maps MCP server at `https://google_maps.run.tools`.

## Prerequisites

1. **Google Maps API Key**
   - You still need a Google Maps API key
   - The MCP server uses your key to make requests to Google Maps
   - Get one at: https://console.cloud.google.com

2. **Enable Required APIs**
   In Google Cloud Console, enable:
   - ✅ Directions API
   - ✅ Geocoding API
   - ✅ Places API
   - ✅ Distance Matrix API

## Configuration

### 1. Update your `.env` file:
```bash
# Required
GOOGLE_MAPS_API_KEY=AIzaSyXXXXXXXXXXXXXXXXXX

# Smithery MCP Server
MCP_GOOGLE_MAPS_ENABLED=True
MCP_GOOGLE_MAPS_URL=https://google_maps.run.tools
```

### 2. Test the connection:
```bash
python test_mcp_connection.py
```

You should see:
```
✅ ALL TESTS PASSED! MCP is working correctly.
```

## How It Works

### Traditional Approach (what we replaced):
```python
import googlemaps

gmaps = googlemaps.Client(key='YOUR_KEY')
directions = gmaps.directions(
    "Rajiv Chowk",
    "Cyber City",
    mode="transit"
)
```

### Smithery MCP Approach (what we're using):
```python
from mcp.google_maps_client import mcp_maps

routes = await mcp_maps.get_directions(
    origin="Rajiv Chowk",
    destination="Cyber City",
    mode="transit"
)
```

**Benefits:**
- ✅ Standardized interface across different APIs
- ✅ Built-in error handling
- ✅ Async support out of the box
- ✅ No need to manage Google Maps client library versions
- ✅ Easy to swap providers if needed

## Available MCP Tools

The Smithery Google Maps MCP server provides these tools:

1. **maps_geocode**: Address → Coordinates
2. **maps_reverse_geocode**: Coordinates → Address
3. **maps_get_directions**: Route planning
4. **maps_get_distance_matrix**: Multiple origins/destinations
5. **maps_search_places**: Search for places
6. **maps_get_place_details**: Detailed place info

## Usage Examples

### Get Directions:
```python
routes = await mcp_maps.get_directions(
    origin="Rajiv Chowk Metro Station, Delhi",
    destination="Cyber City, Gurugram",
    mode="transit",  # transit, driving, walking, bicycling
    alternatives=True,  # Get multiple route options
    transit_mode=["bus", "subway", "train"],  # Preferred transit
    transit_routing_preference="fewer_transfers"  # or "less_walking"
)

# Access route details
for route in routes:
    print(f"Duration: {route['duration_text']}")
    print(f"Distance: {route['distance_text']}")
    
    for step in route['steps']:
        if 'transit' in step:
            transit = step['transit']
            print(f"Take {transit['line_name']} from {transit['departure_stop']}")
```

### Get Traffic Info:
```python
traffic = await mcp_maps.get_traffic_conditions(
    origin="Connaught Place",
    destination="Gurugram",
    mode="driving"
)

print(f"Traffic level: {traffic['traffic_level']}")  # light, moderate, heavy, severe
print(f"Delay: {traffic['traffic_delay_seconds']} seconds")
```

### Geocoding:
```python
location = await mcp_maps.geocode("Rajiv Chowk Metro Station")
print(f"Coordinates: {location['lat']}, {location['lng']}")
```

## Troubleshooting

### Error: "MCP tool error: Invalid API key"
- Check your `GOOGLE_MAPS_API_KEY` in `.env`
- Verify the key is enabled for the required APIs

### Error: "Connection timeout"
- Check your internet connection
- Verify the URL: `https://google_maps.run.tools`

### Error: "ZERO_RESULTS"
- Your addresses might be too vague
- Try more specific locations: "Rajiv Chowk Metro Station, Delhi" instead of "Rajiv Chowk"

### No transit routes found
- Transit may not be available for that route
- Try with `mode="driving"` to test if the locations are valid

## Rate Limits

Google Maps API has usage limits:
- **Free tier**: $200 credit/month
- **Directions API**: $5 per 1,000 requests
- **Geocoding API**: $5 per 1,000 requests

For this hackathon (500-1000 requests), you'll stay well within the free tier.

## Next Steps

Once MCP is working, you can:
1. ✅ Proceed to agent core files
2. ✅ Integrate Delhi Metro static data with Google Maps routes
3. ✅ Build the decision engine