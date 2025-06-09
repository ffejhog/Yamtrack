# Steam Import Integration

This document describes the newly implemented Steam import integration for Yamtrack, which allows users to import their Steam game library into the application.

## Overview

The Steam integration uses the Steam Web API to fetch a user's owned games and automatically import them into Yamtrack. Games are matched with IGDB when possible for better metadata, or created as manual entries when no match is found.

## Features

- **Automatic Game Import**: Fetches all owned games from a user's Steam library
- **Smart Status Detection**: Automatically sets game status based on playtime:
  - `Plan to Play`: Games with 0 minutes played
  - `Playing`: Games played in the last 2 weeks
  - `Completed`: Games with playtime but not played recently
- **IGDB Integration**: Attempts to match Steam games with IGDB for better metadata
- **Playtime Tracking**: Stores total playtime in the progress field
- **Periodic Imports**: Supports scheduled imports (daily or every 2 days)
- **Import Modes**: Supports both "new only" and "overwrite existing" modes

## Implementation Files

### Core Integration Module
- `src/integrations/imports/steam.py`: Main Steam import logic

### Task and View Integration  
- `src/integrations/tasks.py`: Celery task for Steam imports
- `src/integrations/views.py`: Django view for handling Steam import requests
- `src/integrations/urls.py`: URL routing for Steam import endpoint

### Configuration
- `src/config/settings.py`: Steam API key configuration
- `src/templates/users/import_data.html`: UI for Steam import

## Configuration

### Environment Variables

Add the following environment variable to your `.env` file or environment:

```bash
STEAM_API_KEY=your_steam_api_key_here
```

### Obtaining a Steam API Key

1. Visit the [Steam Web API Key Registration](https://steamcommunity.com/dev/apikey) page
2. Log in with your Steam account
3. Fill in a domain name (can be localhost for development)
4. Copy the generated API key
5. Add it to your environment configuration

## Usage

### For Users

1. **Find Your Steam ID**: 
   - Visit [steamid.io](https://steamid.io) and enter your Steam profile URL
   - Copy the 64-bit Steam ID (e.g., `76561197960435530`)

2. **Import Games**:
   - Navigate to Settings → Import Data
   - Find the Steam section
   - Enter your 64-bit Steam ID
   - Choose import mode and frequency
   - Click "Import"

### Import Modes

- **Only Sync New Items**: Adds new games without affecting existing entries
- **Sync New Items and Overwrite Existing**: Updates existing games and adds new ones

### Periodic Imports

Users can set up automatic imports that run:
- **Daily**: Every day at specified time
- **Every 2 Days**: Every 2 days at specified time

## API Integration

### Steam Web API Endpoints Used

1. **GetOwnedGames**: 
   - Endpoint: `http://api.steampowered.com/IPlayerService/GetOwnedGames/v0001/`
   - Purpose: Retrieve user's game library with metadata

### Error Handling

The integration handles various error scenarios:

- **Invalid API Key**: Clear error message about API configuration
- **Private Profile**: Informs user that Steam profile must be public
- **Network Errors**: Graceful handling of connection issues
- **Invalid Steam ID**: Validation of Steam ID format

## Data Mapping

### Steam to Yamtrack Mapping

| Steam Field | Yamtrack Field | Notes |
|-------------|----------------|-------|
| `appid` | `media_id` | Used for IGDB matching or manual entry ID |
| `name` | `title` | Game title |
| `playtime_forever` | `progress` | Total playtime in minutes |
| `playtime_2weeks` | Used for status | Determines if currently playing |
| `img_icon_url` | `image` | Constructed Steam CDN URL |

### Status Logic

```python
if playtime_forever > 0:
    if playtime_2weeks > 0:
        status = "Playing"
    else:
        status = "Completed"
else:
    status = "Plan to Play"
```

## Database Impact

### New Games
- Creates `Item` records with appropriate source (IGDB or Manual)
- Creates `Game` records linked to user
- Preserves playtime data in progress field

### Source Priority
1. **IGDB**: When game is successfully matched
2. **Manual**: When no IGDB match found, uses `steam_{appid}` as media_id

## Limitations

- Requires public Steam profile for API access
- Limited to games owned by the user (no wishlist support)
- Playtime data is read-only from Steam
- No achievement import (could be added in future)

## Security Considerations

- Steam API key is stored securely using Django's secret management
- User Steam IDs are not stored permanently
- API requests are made server-side to protect API keys

## Testing

To test the integration:

1. Set up a test Steam API key
2. Use a known public Steam profile
3. Verify games are imported correctly
4. Test both import modes
5. Verify error handling with invalid inputs

## Future Enhancements

Potential improvements for future versions:

- **Achievement Import**: Using `GetPlayerAchievements` endpoint
- **Wishlist Support**: Import Steam wishlist as "Plan to Play"
- **Game Categories**: Import Steam categories/tags
- **Friends Integration**: Import games from friends' libraries
- **Recent Activity**: Track recent gaming activity
- **Game Reviews**: Import Steam reviews if available

## Troubleshooting

### Common Issues

1. **"Steam API key not configured"**
   - Ensure `STEAM_API_KEY` environment variable is set
   - Restart the application after setting the key

2. **"Steam profile is private"**
   - User must set Steam profile to public
   - Check Steam privacy settings

3. **"No games found"**
   - Verify Steam ID is correct (64-bit format)
   - Ensure user has games in their library
   - Check if profile allows public access to game details

4. **Import shows warnings**
   - Check application logs for specific game import failures
   - Some games may fail IGDB matching but still import as manual entries

### Debug Mode

Enable Django debug mode and check logs for detailed error information during imports.