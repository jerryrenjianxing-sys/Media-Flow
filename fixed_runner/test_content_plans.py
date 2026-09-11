from __future__ import annotations

import tempfile
import json
import threading
import unittest
import urllib.request
import urllib.error
from http.server import ThreadingHTTPServer
from pathlib import Path

from content_plans import normalize_content_plan, round_snapshot
from control_config import DEFAULT_CONFIG, build_scheduled_plan, normalized_config, save_preset, submit_scheduled_rounds
from task_store import TaskStore
from control_api import Handler


def plan_document() -> dict:
    return {
        "name": "产业主题轮换",
        "comment_template": "专业、简洁，不使用夸张语气",
        "common_comment_pool": ["这个角度很有启发", "关键点讲得很清楚"],
        "themes": [
            {
                "id": "a",
                "name": "人工智能",
                "topic_prompt": "主要内容必须直接讨论人工智能技术",
                "search_query": "人工智能",
                "comment_template": "关注实际应用",
                "comment_pool": ["技术落地值得持续关注"],
                "enabled": True,
            },
            {
                "id": "disabled",
                "name": "停用主题",
                "topic_prompt": "不参与队列",
                "search_query": "停用",
                "enabled": False,
            },
            {
                "id": "b",
                "name": "智能制造",
                "topic_prompt": "主要内容必须直接讨论智能制造",
                "search_query": "智能制造",
                "comment_pool": ["现场应用更能说明价值"],
                "enabled": True,
            },
            {
                "id": "c",
                "name": "塑料包装",
                "topic_prompt": "主要内容必须直接讨论塑料包装",
                "search_query": "塑料包装",
                "enabled": True,
            },
        ],
    }


class ContentPlansTest(unittest.TestCase):
    def test_segment_continuation_preserves_content_and_inspection_rounds(self):
        with tempfile.TemporaryDirectory() as directory:
            store = TaskStore(Path(directory) / 'tasks.db')
            revision = store.save_content_plan(plan_document())
            config = normalized_config({**DEFAULT_CONFIG, 'device_ids': ['one', 'two'],
                'content_mode': 'search', 'search_query': 'placeholder',
                'round_count': 18, 'content_round_start': 3,
                'engagement_inspection_enabled': True, 'inspection_every_rounds': 3,
                'inspection_mode': 'home_badge'})
            plan = build_scheduled_plan(config, plan_revision=revision)
            for device in config['device_ids']:
                videos = [x for x in plan.tasks if x.device_id == device and x.task_type == 'douyin_topic_session']
                checks = [x for x in plan.tasks if x.device_id == device and x.task_type == 'douyin_engagement_inspection']
                self.assertEqual([x.payload['round_index'] for x in videos], list(range(3, 21)))
                self.assertEqual([x.payload['submission_round_index'] for x in videos], list(range(1, 19)))
                self.assertEqual(videos[0].payload['search_query'], '塑料包装')
                self.assertEqual(videos[-1].payload['content_plan_snapshot']['theme_queue_index'], 2)
                self.assertEqual([x.payload['after_round_index'] for x in checks], [3, 6, 9, 12, 15, 18])
                self.assertTrue(all(x.payload['inspection_workflow_version'] == 'home_badge' for x in checks))
            self.assertEqual(plan.video_task_count, 36)

    def test_content_round_start_is_strict_and_not_a_preset_setting(self):
        for value in [0, 21, -1, True, 2.5, '2', None]:
            with self.subTest(value=value), self.assertRaisesRegex(ValueError, 'content_round_start'):
                normalized_config({**DEFAULT_CONFIG, 'content_round_start': value})
        with self.assertRaisesRegex(ValueError, 'content_round_start'):
            normalized_config({**DEFAULT_CONFIG, 'content_round_start': 3, 'round_count': 19})
        with tempfile.TemporaryDirectory() as directory:
            store = TaskStore(Path(directory) / 'tasks.db')
            preset = save_preset(store, '续段参数', {**DEFAULT_CONFIG, 'content_round_start': 3})
            self.assertNotIn('content_round_start', preset['config'])
        self.assertEqual(normalized_config({**DEFAULT_CONFIG})['content_round_start'], 1)

    def test_single_device_split_keeps_full_round_random_seed(self):
        common = {**DEFAULT_CONFIG, 'device_ids': ['one'], 'seed': 900}
        whole = build_scheduled_plan(normalized_config({**common, 'round_count': 20}))
        tail = build_scheduled_plan(normalized_config({**common, 'content_round_start': 3, 'round_count': 18}))
        self.assertEqual([x.payload['seed'] for x in whole.tasks[2:]], [x.payload['seed'] for x in tail.tasks])

    def test_content_plan_http_endpoints_create_list_and_archive(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            class TestHandler(Handler):
                store = TaskStore(Path(directory) / "api.db")
                def log_message(self, format: str, *args) -> None:
                    return

            server = ThreadingHTTPServer(("127.0.0.1", 0), TestHandler)
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            base = f"http://127.0.0.1:{server.server_address[1]}"
            try:
                payload = json.dumps({"document": plan_document()}).encode()
                request = urllib.request.Request(
                    base + "/api/content-plans", data=payload,
                    headers={"Content-Type": "application/json"}, method="POST"
                )
                created = json.loads(urllib.request.urlopen(request, timeout=3).read())
                plan_id = created["content_plan"]["plan_id"]
                listed = json.loads(
                    urllib.request.urlopen(base + "/api/content-plans", timeout=3).read()
                )
                self.assertEqual(listed["content_plans"][0]["plan_id"], plan_id)
                archive = urllib.request.Request(
                    base + "/api/content-plans/archive",
                    data=json.dumps({"plan_id": plan_id}).encode(),
                    headers={"Content-Type": "application/json"}, method="POST"
                )
                urllib.request.urlopen(archive, timeout=3).read()
                listed_after = json.loads(
                    urllib.request.urlopen(base + "/api/content-plans", timeout=3).read()
                )
                self.assertEqual(listed_after["content_plans"], [])
                listed_archived = json.loads(
                    urllib.request.urlopen(
                        base + "/api/content-plans?include_archived=true", timeout=3
                    ).read()
                )
                self.assertEqual(listed_archived["content_plans"][0]["plan_id"], plan_id)
                invalid = urllib.request.Request(
                    base + "/api/content-plans",
                    data=json.dumps({"document": {"name": "invalid"}}).encode(),
                    headers={"Content-Type": "application/json"}, method="POST"
                )
                with self.assertRaises(urllib.error.HTTPError) as failure:
                    urllib.request.urlopen(invalid, timeout=3)
                self.assertEqual(failure.exception.code, 400)
            finally:
                server.shutdown(); server.server_close(); thread.join(timeout=3)

    def test_normalization_deduplicates_pool_and_requires_enabled_theme(self) -> None:
        document = plan_document()
        document["common_comment_pool"].append("这个角度很有启发")
        normalized = normalize_content_plan(document)
        self.assertEqual(len(normalized["common_comment_pool"]), 2)
        for theme in document["themes"]:
            theme["enabled"] = False
        with self.assertRaisesRegex(ValueError, "至少需要一个启用主题"):
            normalize_content_plan(document)

    def test_store_creates_immutable_revisions_and_archive_keeps_history(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = TaskStore(Path(directory) / "tasks.db")
            first = store.save_content_plan(plan_document())
            changed = plan_document()
            changed["name"] = "产业主题轮换新版"
            second = store.save_content_plan(changed, plan_id=first["plan_id"])
            self.assertEqual(second["revision_number"], 2)
            self.assertEqual(
                store.get_content_plan_revision(first["revision_id"])["document"]["name"],
                "产业主题轮换",
            )
            self.assertTrue(store.archive_content_plan(first["plan_id"]))
            self.assertEqual(store.list_content_plans(), [])
            self.assertTrue(
                store.get_content_plan_revision(first["revision_id"])["archived"]
            )

    def test_round_snapshot_uses_only_enabled_topics(self) -> None:
        revision = {
            "plan_id": "p",
            "id": "r",
            "revision_number": 1,
            "document": normalize_content_plan(plan_document()),
        }
        names = [round_snapshot(revision, index)["theme"]["name"] for index in range(1, 6)]
        self.assertEqual(names, ["人工智能", "智能制造", "塑料包装", "人工智能", "智能制造"])

    def test_submission_freezes_same_theme_per_round_across_devices(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = TaskStore(Path(directory) / "tasks.db")
            revision = store.save_content_plan(plan_document())
            config = normalized_config(
                {
                    **DEFAULT_CONFIG,
                    "device_ids": ["device-1", "device-2"],
                    "content_mode": "search",
                    "topic_prompt": "temporary",
                    "search_query": "temporary",
                    "round_count": 5,
                    "content_plan_id": revision["plan_id"],
                    "content_plan_revision_id": revision["revision_id"],
                }
            )
            task_ids = submit_scheduled_rounds(store, config)
            tasks = [store.get(task_id) for task_id in task_ids]
        first = [task.payload["content_plan_snapshot"]["theme"]["name"] for task in tasks[:5]]
        second = [task.payload["content_plan_snapshot"]["theme"]["name"] for task in tasks[5:]]
        self.assertEqual(first, ["人工智能", "智能制造", "塑料包装", "人工智能", "智能制造"])
        self.assertEqual(first, second)
        self.assertEqual(tasks[0].payload["topic_prompt"], "主要内容必须直接讨论人工智能技术")

    def test_queued_snapshot_survives_plan_edit_and_new_submission_restarts_at_first(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = TaskStore(Path(directory) / "tasks.db")
            first_revision = store.save_content_plan(plan_document())
            config = normalized_config(
                {
                    **DEFAULT_CONFIG,
                    "device_ids": ["device-1"],
                    "content_mode": "search",
                    "topic_prompt": "temporary",
                    "search_query": "temporary",
                    "round_count": 2,
                    "content_plan_id": first_revision["plan_id"],
                    "content_plan_revision_id": first_revision["revision_id"],
                }
            )
            first_submission = submit_scheduled_rounds(store, config)
            changed = plan_document()
            changed["themes"][0]["name"] = "人工智能新版"
            second_revision = store.save_content_plan(
                changed, plan_id=first_revision["plan_id"]
            )
            second_config = normalized_config(
                {
                    **config,
                    "content_plan_revision_id": second_revision["revision_id"],
                }
            )
            second_submission = submit_scheduled_rounds(store, second_config)

            self.assertEqual(
                store.get(first_submission[0]).payload["content_plan_snapshot"]["theme"]["name"],
                "人工智能",
            )
            self.assertEqual(
                store.get(second_submission[0]).payload["content_plan_snapshot"]["theme"]["name"],
                "人工智能新版",
            )

    def test_archived_revision_remains_usable_from_historical_preset(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = TaskStore(Path(directory) / "tasks.db")
            revision = store.save_content_plan(plan_document())
            store.archive_content_plan(revision["plan_id"])
            config = normalized_config(
                {
                    **DEFAULT_CONFIG,
                    "device_ids": ["device-1"],
                    "content_mode": "mixed",
                    "content_plan_id": revision["plan_id"],
                    "content_plan_revision_id": revision["revision_id"],
                }
            )
            task_id = submit_scheduled_rounds(store, config)[0]
            self.assertEqual(
                store.get(task_id).payload["content_plan_snapshot"]["content_plan_revision_id"],
                revision["revision_id"],
            )

    def test_general_mode_and_legacy_preset_remain_compatible(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = TaskStore(Path(directory) / "tasks.db")
            config = normalized_config({**DEFAULT_CONFIG, "device_ids": ["device-1"]})
            task_id = submit_scheduled_rounds(store, config)[0]
            self.assertNotIn("content_plan_snapshot", store.get(task_id).payload)
            preset = save_preset(store, "旧式单主题", DEFAULT_CONFIG)
        self.assertIsNone(preset["config"]["content_plan_id"])
        self.assertIsNone(preset["config"]["content_plan_revision_id"])

    def test_invalid_revision_is_rejected_before_tasks_are_created(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = TaskStore(Path(directory) / "tasks.db")
            config = normalized_config(
                {
                    **DEFAULT_CONFIG,
                    "content_mode": "mixed",
                    "topic_prompt": "人工智能",
                    "content_plan_id": "missing-plan",
                    "content_plan_revision_id": "missing-revision",
                }
            )
            with self.assertRaisesRegex(ValueError, "版本不存在"):
                submit_scheduled_rounds(store, config)
            self.assertEqual(store.list_all(), [])


if __name__ == "__main__":
    unittest.main()
