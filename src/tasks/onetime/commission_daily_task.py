from __future__ import annotations

from functools import cached_property

from src.core.base_game_task import BaseGameTask
from src.data.page import page_main, page_commission_daily_material, page_commission_daily_boss, page_commission_daily_equipment
from src.data.feature_list import FeatureList
from src.core.detector.template_detector import TemplateDetector
from src.core.detector.ocr_detector import OcrDetector
from src.icons import Icons
from src.image.hsv_config import HSVRange
from src.image.frame_processes import make_hsv_isolator

ALL_COMISSIONS = {
    'daily_material': [
        '银光闪闪',
        '结晶萃取',
        '千锤百炼',
    ],
    'daily_boss': [
        '无惧之战砧',
        '凋零的挽歌',
        '温和的雷鸣',
        '深巢梦魇',
        '林间幻梦',
        '吞噬之渊',
        '焚灼之域',
    ],
    'daily_equipment': [
        '苍雷之卫',
        '烈炎之佑',
        '常青之庇',
        '湍流之守',
        '丘薮之陲',
        '长风之护',
        '厚岩之盾',
        '严寒之屏',
        '日月之捍',
        '急炽之御',
    ]
}

COMMISSION_PAGES = {
    'daily_material': page_commission_daily_material,
    'daily_boss': page_commission_daily_boss,
    'daily_equipment': page_commission_daily_equipment,
}

COMMISSION_COSTS = {
    'daily_material': 30,
    'daily_boss': 30,
    'daily_equipment': 40,
}

class CommissionDailyTask(BaseGameTask):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.name = "委托每日"
        self.icon = Icons.Task
        self.description = "完成委托家园每日任务，需要在游戏设置中打开“自动托管领取原初之流宝箱”并且至少通关过副本一遍解锁自动战斗。"
        self.default_config = {
            "选择委托": "银光闪闪",
        }
        self.config_type = {
            "选择委托": {
                "type": "cascade_drop_down",
                "options": ALL_COMISSIONS,
                "labels": {
                    'daily_material': '每日委托-基础材料',
                    'daily_boss': '每日委托-首领挑战',
                    'daily_equipment': '每日委托-武备获取',
                }
            },
        }

        self.commission_types = {}
        for type in ALL_COMISSIONS:
            for name in ALL_COMISSIONS[type]:
                self.commission_types[name] = type

    @property
    def commission_name(self):
        return self.config["选择委托"]

    @property
    def commission_type(self):
        return self.commission_types[self.commission_name]

    @cached_property
    def scroll_box(self):
        return self.box_of_screen(0.0906, 0.5611, 0.6156, 0.6917)

    def _get_power(self):
        for _ in self.loop(2):
            boxes = self.ocr(box=self.box_of_screen(0.8635, 0.0417, 0.9187, 0.0593))
            if not boxes or not (result := boxes[0].name):
                continue
            if result.count('/') != 1:
                continue
            return int(result.split('/')[0])

    def run_once(self):
        self.ui_ensure(COMMISSION_PAGES[self.commission_type])
        result = self.detect_with_scroll(
            detector=OcrDetector(
                match=self.commission_name,
                box=self.scroll_box,
            ),
            box=self.scroll_box,
            scroll_count=10,
        )
        self.wait_action_result(
            action=lambda: self.click(result),
            expect=OcrDetector(
                match=self.commission_name,
                box=self.box_of_screen(0.7562, 0.2741, 0.9240, 0.3167),
            ),
            max_attempts=5,
        )
        self.wait_action_result(
            action=lambda: self.click(self.box_of_screen(0.8870, 0.7204, 0.9307, 0.7417)),
            expect=TemplateDetector(FeatureList.commission_button_start_commission),
            max_attempts=5,
        )
        self.wait_action_result(
            condition=TemplateDetector(FeatureList.commission_button_start_commission),
            action=lambda hit: self.click(hit),
            expect=TemplateDetector(FeatureList.loading_check),
            max_attempts=5,
        )
        for _ in self.loop(120):
            if self.find_one(FeatureList.auto_combat_setting):
                break
            if self.find_one(FeatureList.auto_combat_check):
                self.send_key('f1')
                self.sleep(self.once_sleep_time)
        for _ in self.loop(480):
            if self.find_one(FeatureList.auto_combat_setting):
                self.sleep(self.once_sleep_time)
                continue
            if self.find_one(FeatureList.bond_levelup_popup):
                self.click(self.box_of_screen(0.4568, 0.8324, 0.5448, 0.8824))
                self.sleep(0.1)
                continue
            if box := self.find_one(FeatureList.combat_button_return):
                self.click(box)
                self.sleep(0.1)
                continue
            if any(self.ui_page_appear(page) for page in COMMISSION_PAGES.values()):
                break

    def run(self):
        self.ui_ensure(COMMISSION_PAGES[self.commission_type])
        while self._get_power() >= COMMISSION_COSTS[self.commission_type]:
            self.run_once()
        self.ui_ensure(page_main)
