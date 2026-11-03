from copy import deepcopy

from PySubtrans.Helpers.ContextHelpers import GetBatchContext
from PySubtrans.Helpers.TestCases import PrepareSubtitles, SubtitleTestCase
from PySubtrans.Helpers.Tests import log_info, log_test_name
from PySubtrans.SubtitleBatcher import SubtitleBatcher
from PySubtrans.SubtitleEditor import SubtitleEditor
from PySubtrans.Subtitles import Subtitles
from PySubtrans.SubtitleScene import SubtitleScene

from ..TestData.chinese_dinner import chinese_dinner_data


class LargeContextTests(SubtitleTestCase):
    def __init__(self, methodName):
        super().__init__(
            methodName,
            custom_options={
                "large_context_mode": True,
                "max_batch_size": 2000,  # Explicitly set high to match default logic
                "scene_threshold": 300.0,
            },
        )

    def test_LargeContextBatching(self):
        """
        Verify that large context mode produces larger batches (fewer scenes)
        """
        test_data = [chinese_dinner_data]

        for data in test_data:
            log_test_name(f"Testing large context batching of {data.get('movie_name')}")

            # Standard batcher (small context)
            standard_options = deepcopy(self.options)
            standard_options["large_context_mode"] = False
            standard_options["max_batch_size"] = 30
            standard_options["scene_threshold"] = 30.0

            standard_batcher = SubtitleBatcher(standard_options)
            standard_subtitles: Subtitles = PrepareSubtitles(data, "original")
            with SubtitleEditor(standard_subtitles) as editor:
                editor.AutoBatch(standard_batcher)

            # Large context batcher
            large_batcher = SubtitleBatcher(self.options)
            large_subtitles: Subtitles = PrepareSubtitles(data, "original")
            with SubtitleEditor(large_subtitles) as editor:
                editor.AutoBatch(large_batcher)

            log_info(f"Standard scenes: {len(standard_subtitles.scenes)}")
            log_info(f"Large context scenes: {len(large_subtitles.scenes)}")

            # We expect fewer scenes/batches with large context mode
            self.assertLessEqual(
                len(large_subtitles.scenes), len(standard_subtitles.scenes)
            )

            # Check context generation for the first batch
            if large_subtitles.scenes:
                scene = large_subtitles.scenes[0]
                if scene.batches:
                    batch = scene.batches[0]

                    # Get context for the current batch
                    context = GetBatchContext(
                        large_subtitles, scene.number, batch.number
                    )

                    self.assertIsInstance(context, dict)
                    # History should be empty for the first batch
                    self.assertFalse(context.get("history"))

    def test_DetailedHistory(self):
        """
        Verify that GetDetailedHistory returns actual lines
        """
        data = chinese_dinner_data
        originals: Subtitles = PrepareSubtitles(data, "original")
        lines = originals.originals or []

        # Manually create a scene with translated lines
        scene = SubtitleScene()
        scene.number = 1
        originals.scenes.append(scene)

        batch1 = scene.AddNewBatch()
        batch1.originals = lines[:10]
        batch1.translated = lines[:10]  # Fake translation
        batch1.number = 1

        batch2 = scene.AddNewBatch()
        batch2.originals = lines[10:20]
        batch2.number = 2

        # Enable large context mode
        originals.settings["large_context_mode"] = True

        context = GetBatchContext(originals, scene.number, 2)

        history = context.get("history", [])
        log_info(f"History lines: {len(history)}")

        # We expect history to contain the lines from batch 1
        self.assertTrue(len(history) > 0)
        self.assertIn("--- Batch 1 ---", history[0])
