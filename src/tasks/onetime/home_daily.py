from __future__ import annotations

import time

from ok import WaitFailedException

from src.core.base_game_task import BaseGameTask
from src.data.page import page_main, page_home_building, page_home_building_pot, page_home_restaurant
from src.data.feature_list import FeatureList
from src.core.detector.template_detector import TemplateDetector
from src.core.detector.ocr_detector import OcrDetector
from src.icons import Icons


class HomeDaily(BaseGameTask):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.name = "家园每日"
        self.icon = Icons.Task
        self.description = "完成家园每日任务"

    def claim(self):
        self.ui_ensure(page_home_building)
        self.wait_action_result(
            action=lambda: self.click(self.box_of_screen(0.0333, 0.8093, 0.0656, 0.8676)),
            expect=TemplateDetector(FeatureList.home_claim_cross),
            max_attempts=3
        )
        self.wait_action_result(
            condition=TemplateDetector(FeatureList.home_claim_cross),
            action=lambda hit: self.click(hit),
            expect=TemplateDetector(FeatureList.home_building_check),
            while_condition=TemplateDetector(FeatureList.home_levelup_popup),
            repeat_action=lambda: self.click(self.box_of_screen(0.4818, 0.6907, 0.5198, 0.7269)),
            max_attempts=3
        )
        

    def make_food(self):
        self.ui_ensure(page_home_building_pot)
        self.wait_action_result(
            action=lambda: self.click(self.box_of_screen(0.4573, 0.1593, 0.4661, 0.1750)),
            expect=TemplateDetector(FeatureList.pot_category_all_activated),
            max_attempts=3
        )
        food_box = self.find_with_scroll(FeatureList.food_little_bobo, self.box_of_screen(0.2865, 0.2176, 0.6188, 0.8667))
        if not self.wait_action_result(
            action=lambda: self.click(food_box),
            expect=OcrDetector(
                match=self.lang.home.little_bobo,
                box=self.box_of_screen(0.6797, 0.1852, 0.7786, 0.2241)
            ),
            max_attempts=3
        ):
            raise RuntimeError('Failed to find food.')

        self.click(self.box_of_screen(0.8792, 0.7639, 0.8896, 0.7806))
        self.sleep(0.1)
        self.click(self.box_of_screen(0.7849, 0.8167, 0.8380, 0.8519))

    def feed(self):
        self.ui_ensure(page_home_restaurant)
        start_time = time.monotonic()
        while time.monotonic() - start_time < 10:
            self.next_frame()
            boxes = self.ocr(box=self.box_of_screen(0.4557, 0.2389, 0.5427, 0.2611))
            if not boxes or not (result := boxes[0].name):
                continue
            if result.count('/') != 1:
                continue

            result = result.split('/')
            current = int(result[0].strip())
            total = int(result[1].strip())

            if total - current < 100:
                return

            self.click(self.box_of_screen(0.8375, 0.8667, 0.8630, 0.9157))
            self.sleep(0.2)
        raise WaitFailedException('Feed task of HomeDaily timeout.')

    def run(self):
        # 收菜
        self.claim()

        # 做饭
        self.make_food()

        # 喂饭
        self.feed()

        # 回主页面
        self.ui_ensure(page_main)
