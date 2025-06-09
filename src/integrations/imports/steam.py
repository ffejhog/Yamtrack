import logging
import time
from collections import defaultdict

import requests
from django.conf import settings

import app
from app.models import MediaTypes, Sources, Status
from app.providers import services
from integrations.imports import helpers
from integrations.imports.helpers import MediaImportError, MediaImportUnexpectedError

logger = logging.getLogger(__name__)

STEAM_API_BASE_URL = "http://api.steampowered.com"


def importer(steam_id, user, mode):
    """Import the user's games from Steam."""
    steam_importer = SteamImporter(steam_id, user, mode)
    return steam_importer.import_data()


class SteamImporter:
    """Class to handle importing user game data from Steam."""

    def __init__(self, steam_id, user, mode):
        """Initialize the importer with user details and mode.

        Args:
            steam_id (str): Steam user ID (64-bit SteamID) to import from
            user: Django user object to import data for
            mode (str): Import mode ("new" or "overwrite")
        """
        self.steam_id = steam_id
        self.user = user
        self.mode = mode
        self.warnings = []
        self.api_key = getattr(settings, "STEAM_API_KEY", None)

        if not self.api_key:
            msg = "Steam API key not configured in settings"
            raise MediaImportError(msg)

        # Track existing media to handle "new" mode correctly
        self.existing_media = helpers.get_existing_media(user)

        # Track media IDs to delete in overwrite mode
        self.to_delete = defaultdict(lambda: defaultdict(set))

        # Track bulk creation lists for each media type
        self.bulk_media = defaultdict(list)

    def import_data(self):
        """Import user's Steam game library."""
        try:
            # Get owned games from Steam API
            owned_games = self._get_owned_games()

            if not owned_games:
                logger.info("No games found for Steam user %s", self.steam_id)
                return {}, ""

            # Process each game
            for game_data in owned_games:
                self._process_game(game_data)

            # Clean up existing media if in overwrite mode
            helpers.cleanup_existing_media(self.to_delete, self.user)

            # Bulk create all media
            helpers.bulk_create_media(self.bulk_media, self.user)

            # Count imported media
            imported_counts = {
                media_type: len(media_list)
                for media_type, media_list in self.bulk_media.items()
            }

            logger.info(
                "Steam import completed for user %s: %s",
                self.user.username,
                imported_counts,
            )

            return imported_counts, "\n".join(self.warnings) if self.warnings else ""

        except requests.RequestException as e:
            logger.exception("Network error during Steam import")
            msg = "Network error occurred"
            raise MediaImportUnexpectedError(msg) from e
        except Exception as e:
            logger.exception("Unexpected error during Steam import")
            msg = "An unexpected error occurred"
            raise MediaImportUnexpectedError(msg) from e

    def _get_owned_games(self):
        """Fetch owned games from Steam API with retry logic for rate limiting."""
        url = f"{STEAM_API_BASE_URL}/IPlayerService/GetOwnedGames/v0001/"
        params = {
            "key": self.api_key,
            "steamid": self.steam_id,
            "include_appinfo": 1,
            "include_played_free_games": 1,
            "format": "json",
        }

        max_retries = 3
        base_delay = 15

        for attempt in range(max_retries):
            try:
                response = requests.get(url, params=params, timeout=30)
                response.raise_for_status()

                data = response.json()

                if "response" not in data:
                    msg = "Invalid response from Steam API"
                    raise MediaImportError(msg)

                if "games" not in data["response"]:
                    # User might have private profile or no games
                    logger.warning("No games found in Steam response for user %s", self.steam_id)
                    return []

                games = data["response"]["games"]
                logger.info("Found %d games for Steam user %s", len(games), self.steam_id)
                return games

            except requests.HTTPError as e:
                if e.response.status_code == 429:
                    # Rate limited - implement exponential backoff
                    if attempt < max_retries - 1:
                        delay = base_delay * (2 ** attempt)
                        logger.warning(
                            "Steam API rate limited (429). "
                            "Retrying in %d seconds (attempt %d/%d)",
                            delay, attempt + 1, max_retries,
                        )
                        time.sleep(delay)
                        continue
                    msg = "Steam API rate limit exceeded. Please try again later."
                    raise MediaImportError(msg) from e
                elif e.response.status_code == 403:
                    msg = "Steam profile is private or API key is invalid"
                    raise MediaImportError(msg) from e
                elif e.response.status_code == 500:
                    msg = "Steam API returned an internal error"
                    raise MediaImportError(msg) from e
                else:
                    msg = f"Steam API error: {e.response.status_code}"
                    raise MediaImportError(msg) from e
            except requests.RequestException as e:
                logger.exception("Request error when fetching Steam games")
                msg = "Failed to connect to Steam API"
                raise MediaImportError(msg) from e

        # If we reach here, all retries failed
        msg = "Steam API request failed after all retries"
        raise MediaImportError(msg)

    def _process_game(self, game_data):
        """Process a single game from Steam API response."""
        appid = str(game_data["appid"])
        name = game_data.get("name", f"Unknown Game {appid}")
        playtime_forever = game_data.get("playtime_forever", 0)  # in minutes
        playtime_2weeks = game_data.get("playtime_2weeks", 0)  # in minutes

        # Get game image URL if available
        img_icon_url = game_data.get("img_icon_url", "")
        image_url = ""
        if img_icon_url:
            image_url = f"http://media.steampowered.com/steamcommunity/public/images/apps/{appid}/{img_icon_url}.jpg"

        # Check if we should process this game
        if not helpers.should_process_media(
            self.existing_media,
            self.to_delete,
            MediaTypes.GAME.value,
            Sources.IGDB.value,  # Use IGDB as source since Steam isn't in Sources enum
            appid,
            self.mode,
        ):
            return

        try:
            # Try to match with IGDB for better metadata
            igdb_game = self._match_with_igdb(name, appid)

            if igdb_game:
                # Use IGDB data if found
                item, _ = app.models.Item.objects.get_or_create(
                    media_id=str(igdb_game["media_id"]),
                    source=Sources.IGDB.value,
                    media_type=MediaTypes.GAME.value,
                    defaults={
                        "title": igdb_game.get("title", name),
                        "image": igdb_game.get("image", image_url),
                    },
                )
            else:
                # Create manual entry if no IGDB match
                item, _ = app.models.Item.objects.get_or_create(
                    media_id=f"steam_{appid}",
                    source=Sources.MANUAL.value,
                    media_type=MediaTypes.GAME.value,
                    defaults={
                        "title": name,
                        "image": image_url,
                    },
                )

            # Determine status based on playtime
            if playtime_forever > 0:
                if playtime_2weeks > 0:
                    status = Status.IN_PROGRESS
                else:
                    status = Status.PAUSED
            else:
                status = Status.PLANNING

            # Create game object
            game = app.models.Game(
                item=item,
                user=self.user,
                status=status,
                score=None,  # Steam doesn't provide ratings
                progress=playtime_forever,  # Store total playtime in progress field
                notes=(
                    f"Imported from Steam. Total playtime: "
                    f"{playtime_forever // 60}h {playtime_forever % 60}m"
                ),
                start_date=None,
                end_date=None,
            )

            self.bulk_media[MediaTypes.GAME.value].append(game)

        except (ValueError, KeyError, TypeError) as e:
            logger.warning("Failed to process Steam game %s (%s): %s", name, appid, e)
            self.warnings.append(f"{name} ({appid}): {e!s}")

    def _match_with_igdb(self, game_name, steam_appid):
        """Try to match Steam game with IGDB for better metadata."""
        try:
            # Use the existing IGDB provider to search for the game
            search_results = services.search(
                MediaTypes.GAME.value,
                game_name,
                1,  # page number
            )

            if search_results and search_results.get("results"):
                # Return the first match
                logger.debug(
                    "Matched Steam game %s (appid: %s) with IGDB",
                    game_name,
                    steam_appid,
                )
                return search_results["results"][0]

        except (ValueError, KeyError, TypeError) as e:
            logger.debug(
                "Failed to match Steam game %s (appid: %s) with IGDB: %s",
                game_name,
                steam_appid,
                e,
            )

        return None

