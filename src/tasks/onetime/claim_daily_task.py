from __future__ import annotations

from functools import cached_property

from src.core.base_game_task import BaseGameTask
from src.data.page import (
    page_main,
    page_mail,
    page_shop,
    page_active_daily,
    page_active_weekly,
    page_big_month_card,
)
from src.data.feature_list import FeatureList
from src.icons import Icons
from src.image.hsv_config import HSVRange
from src.image.frame_processes import isolate_by_hsv_ranges, make_hsv_isolator


# 红色感叹号 HSV 范围（提取感叹号红色底色部分，跨 0° 两端）
EXCLAMATION_RED_HSV = (
    ((0, 60, 100), (10, 255, 255)),
    ((170, 60, 100), (180, 255, 255)),
)


def active_exclamation_mark_mask(frame):
    return isolate_by_hsv_ranges(frame, EXCLAMATION_RED_HSV, invert=False, kernel_size=2)


class ClaimDailyTask(BaseGameTask):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.name = "每日收菜"
        self.icon = Icons.Task
        self.description = "按顺序领取邮件、每日惊喜盒子、日常活跃、周常活跃、大月卡任务、大月卡奖励。"
        self.default_config = {
            "删除已读邮件": True,
        }

    @cached_property
    def safe_box(self):
        return self.box_of_screen(0.4786, 0.8722, 0.5245, 0.9120)

    def claim_mail(self):
        self.ui_ensure(page_mail)

        end_flag = False
        for _ in self.loop():
            if box := self.find_one(FeatureList.mail_button_claim_all, mask_function=make_hsv_isolator(HSVRange.WHITE, invert=False)):
                if end_flag:
                    break
                self.click(box)
                self.sleep(0.1)
                continue
            if box := self.find_one(FeatureList.confirm_button_3):
                end_flag = True
                self.click(box)
                self.sleep(0.1)
                continue
            if self.find_one(FeatureList.claim_popup):
                end_flag = True
                self.click(self.safe_box)
                self.sleep(0.1)
                continue

        if self.config["删除已读邮件"]:
            end_flag = False
            for _ in self.loop():
                if self.find_one(FeatureList.mail_button_claim_all, mask_function=make_hsv_isolator(HSVRange.WHITE, invert=False)):
                    if end_flag:
                        break
                    self.click(self.box_of_screen(0.2401, 0.7546, 0.2484, 0.7750))
                    self.sleep(0.1)
                    continue
                if box := self.find_one(FeatureList.confirm_button_3):
                    end_flag = True
                    self.click(box)
                    self.sleep(0.1)
                    continue

    def claim_shop(self):
        self.ui_ensure(page_shop)

        for _ in self.loop():
            if not self.find_one(FeatureList.shop_click_1_check):
                self.click(self.box_of_screen(0.3625, 0.9435, 0.3802, 0.9685))
                self.sleep(0.1)
                continue
            if not self.find_one(FeatureList.shop_click_2_check):
                self.click(self.box_of_screen(0.8099, 0.1315, 0.8271, 0.1509))
                self.sleep(0.1)
                continue
            break

        self.sleep(0.5)
        frame_count = 0
        for _ in self.loop():
            if box := self.find_one(FeatureList.shop_button_claim_daily):
                frame_count = 0
                self.click(box)
                self.sleep(0.1)
                continue
            if box := self.find_one(FeatureList.shop_button_claim_daily_confirm):
                frame_count = 0
                self.click(box)
                self.sleep(0.1)
                continue
            if self.find_one(FeatureList.claim_popup):
                frame_count = 0
                self.click(self.safe_box)
                self.sleep(0.1)
                continue
            if self.find_one(FeatureList.shop_check):
                frame_count += 1
                if frame_count >= 5:
                    break

    def claim_active(self):
        self.ui_ensure(page_active_daily)
        frame_count = 0
        for _ in self.loop():
            if box := self.find_one(FeatureList.active_daily_claim_task, mask_function=make_hsv_isolator(HSVRange.WHITE, invert=False)):
                frame_count = 0
                self.click(box)
                self.sleep(0.1)
                continue
            if box := self.find_one(
                FeatureList.active_daily_exclamation_mark,
                mask_function=active_exclamation_mark_mask,
                box=self.box_of_screen(0.4224, 0.8843, 0.8802, 0.9148)
            ):
                frame_count = 0
                self.click(box)
                self.sleep(0.1)
                continue
            if self.find_one(FeatureList.claim_popup):
                frame_count = 0
                self.click(self.safe_box)
                self.sleep(0.1)
                continue
            if self.find_one(FeatureList.active_daily_check) and not self.find_one(
                FeatureList.active_daily_exclamation_mark,
                mask_function=active_exclamation_mark_mask,
            ):
                frame_count += 1
                if frame_count >= 5:
                    break

        self.ui_ensure(page_active_weekly)
        frame_count = 0
        for _ in self.loop():
            if box := self.find_one(FeatureList.active_weekly_claim_task, mask_function=make_hsv_isolator(HSVRange.WHITE, invert=False)):
                frame_count = 0
                self.click(box)
                self.sleep(0.1)
                continue
            if box := self.find_one(
                FeatureList.active_weekly_exclamation_mark,
                mask_function=active_exclamation_mark_mask,
                box=self.box_of_screen(0.4224, 0.8843, 0.8802, 0.9148)
            ):
                frame_count = 0
                self.click(box)
                self.sleep(0.1)
                continue
            if self.find_one(FeatureList.claim_popup):
                frame_count = 0
                self.click(self.safe_box)
                self.sleep(0.1)
                continue
            if self.find_one(FeatureList.active_weekly_check) and not self.find_one(
                FeatureList.active_daily_exclamation_mark,
                mask_function=active_exclamation_mark_mask,
            ):
                frame_count += 1
                if frame_count >= 5:
                    break

    def claim_big_month_card(self):
        self.ui_ensure(page_big_month_card)

        for _ in self.loop():
            if self.find_one(FeatureList.big_month_card_task_check):
                break
            self.click(self.box_of_screen(0.6089, 0.9611, 0.6219, 0.9815))
            self.sleep(0.1)
        frame_count = 0
        for _ in self.loop():
            if box := self.find_one(FeatureList.big_month_card_button_claim_all):
                frame_count = 0
                self.click(box)
                self.sleep(0.1)
                continue
            if self.find_one(FeatureList.big_month_card_levelup_popup):
                frame_count = 0
                self.click(self.safe_box)
                self.sleep(0.1)
                continue
            if self.find_one(FeatureList.big_month_card_task_check):
                frame_count += 1
                if frame_count >= 5:
                    break

        for _ in self.loop():
            if self.find_one(FeatureList.big_month_card_reward_check):
                break
            self.click(self.box_of_screen(0.4375, 0.9611, 0.4500, 0.9815))
            self.sleep(0.1)
        frame_count = 0
        for _ in self.loop():
            if box := self.find_one(FeatureList.big_month_card_button_claim_all):
                frame_count = 0
                self.click(box)
                self.sleep(0.1)
                continue
            if self.find_one(FeatureList.claim_popup):
                frame_count = 0
                self.click(self.safe_box)
                self.sleep(0.1)
                continue
            if self.find_one(FeatureList.big_month_card_reward_check):
                frame_count += 1
                if frame_count >= 5:
                    break

    def run(self):
        self.claim_mail()
        self.claim_shop()
        self.claim_active()
        self.claim_big_month_card()
        self.ui_ensure(page_main)
