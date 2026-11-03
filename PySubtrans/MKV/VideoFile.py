from pathlib import Path

import regex as re


class VideoFile:
    """
    Represents a video file with season/episode parsing for sorting
    """

    # Pre-compile regex for better performance
    _SEASON_EPISODE_PATTERN = re.compile(
        r"[Ss](\d{1,2})[Ee](\d{1,2})|(\d{1,2})x(\d{1,2})|(\d{1,2})\.(\d{1,2})"
    )

    def __init__(self, path: Path):
        self.path = path
        season, episode = self._parse_season_episode(path.name)
        self.season = season
        self.episode = episode

    @classmethod
    def _parse_season_episode(cls, filename: str) -> tuple[int, int]:
        """Parse season and episode from filename using compiled regex"""
        match = cls._SEASON_EPISODE_PATTERN.search(filename)
        if match:
            # Check which pattern matched and extract groups accordingly
            groups = match.groups()
            if groups[0] is not None:  # S01E02 pattern
                return int(groups[0]), int(groups[1])
            elif groups[2] is not None:  # 1x02 pattern
                return int(groups[2]), int(groups[3])
            else:  # 1.02 pattern
                return int(groups[4]), int(groups[5])

        # If no pattern matches, return (0, 0) to put at start of sort
        return (0, 0)

    def __lt__(self, other):
        return (self.season, self.episode) < (other.season, other.episode)
