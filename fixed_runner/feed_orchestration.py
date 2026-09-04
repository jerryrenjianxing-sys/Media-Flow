from __future__ import annotations

from dataclasses import dataclass, replace
from random import Random


@dataclass(frozen=True)
class FeedPhase:
    """One immutable snapshot of the current hybrid-feed segment."""

    name: str
    target: int
    processed: int
    total_processed: int


class HybridFeedPlanner:
    """Alternate deterministic search/home segments without resampling on recovery."""

    def __init__(
        self,
        *,
        total_videos: int,
        rng: Random,
        search_min: int = 7,
        search_max: int = 14,
        home_min: int = 5,
        home_max: int = 10,
    ) -> None:
        if total_videos < 1:
            raise ValueError("total_videos must be positive")
        for minimum, maximum, label in (
            (search_min, search_max, "search"),
            (home_min, home_max, "home"),
        ):
            if minimum < 1 or maximum < minimum:
                raise ValueError(f"invalid {label} segment range")
        self.total_videos = int(total_videos)
        self.rng = rng
        self.bounds = {
            "search": (int(search_min), int(search_max)),
            "home": (int(home_min), int(home_max)),
        }
        self.total_processed = 0
        self._next_name = "search"
        self._current: FeedPhase | None = None

    def current_or_start(self) -> FeedPhase | None:
        """Return the current segment, drawing a new target only after completion."""
        if self.total_processed >= self.total_videos:
            return None
        if self._current is not None and self._current.processed < self._current.target:
            return self._current

        name = self._next_name
        minimum, maximum = self.bounds[name]
        sampled = self.rng.randint(minimum, maximum)
        target = min(sampled, self.total_videos - self.total_processed)
        self._current = FeedPhase(
            name=name,
            target=target,
            processed=0,
            total_processed=self.total_processed,
        )
        self._next_name = "home" if name == "search" else "search"
        return self._current

    def consume_valid_video(self) -> FeedPhase:
        """Advance only after one verified video was actually processed."""
        current = self.current_or_start()
        if current is None:
            raise RuntimeError("hybrid feed plan is already complete")
        self.total_processed += 1
        self._current = replace(
            current,
            processed=current.processed + 1,
            total_processed=self.total_processed,
        )
        return self._current


__all__ = ["FeedPhase", "HybridFeedPlanner"]
