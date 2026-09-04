from __future__ import annotations

import random
import unittest

from feed_orchestration import HybridFeedPlanner


class HybridFeedPlannerTests(unittest.TestCase):
    def test_segments_alternate_and_stay_inside_configured_ranges(self) -> None:
        planner = HybridFeedPlanner(total_videos=40, rng=random.Random(20260902))
        segments: list[tuple[str, int]] = []
        while (phase := planner.current_or_start()) is not None:
            segments.append((phase.name, phase.target))
            for _ in range(phase.target):
                planner.consume_valid_video()
        self.assertEqual(
            [name for name, _ in segments],
            ["search" if index % 2 == 0 else "home" for index in range(len(segments))],
        )
        for name, target in segments[:-1]:
            minimum, maximum = (7, 14) if name == "search" else (5, 10)
            self.assertGreaterEqual(target, minimum)
            self.assertLessEqual(target, maximum)
        self.assertEqual(sum(target for _, target in segments), 40)

    def test_same_seed_has_same_plan(self) -> None:
        def plan(seed: int) -> list[tuple[str, int]]:
            planner = HybridFeedPlanner(total_videos=55, rng=random.Random(seed))
            output: list[tuple[str, int]] = []
            while (phase := planner.current_or_start()) is not None:
                output.append((phase.name, phase.target))
                for _ in range(phase.target):
                    planner.consume_valid_video()
            return output

        self.assertEqual(plan(19), plan(19))

    def test_recovery_read_does_not_resample_or_advance(self) -> None:
        planner = HybridFeedPlanner(total_videos=20, rng=random.Random(7))
        first = planner.current_or_start()
        self.assertIsNotNone(first)
        for _ in range(5):
            self.assertEqual(planner.current_or_start(), first)
        self.assertEqual(planner.total_processed, 0)

    def test_final_segment_is_truncated_to_remaining_videos(self) -> None:
        planner = HybridFeedPlanner(total_videos=3, rng=random.Random(1))
        phase = planner.current_or_start()
        self.assertEqual((phase.name, phase.target), ("search", 3))
        for _ in range(3):
            planner.consume_valid_video()
        self.assertIsNone(planner.current_or_start())


if __name__ == "__main__":
    unittest.main()
