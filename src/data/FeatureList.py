from enum import Enum


class FeatureList(str, Enum):
    account_switch = 'account_switch'
    char_button = 'char_button'
    close_button = 'close_button'
    confirm_button = 'confirm_button'
    confirm_button_2 = 'confirm_button_2'
    crafting_table_check = 'crafting_table_check'
    home_building_check = 'home_building_check'
    home_building_crafting_table = 'home_building_crafting_table'
    home_kibi_manage = 'home_kibi_manage'
    home_restaurant_check = 'home_restaurant_check'
    login_in = 'login_in'
    login_out = 'login_out'
    menu_backpack = 'menu_backpack'
    skip_confirm = 'skip_confirm'
    skip_dialog = 'skip_dialog'
    star_link_icon = 'star_link_icon'
    treasure_icon = 'treasure_icon'
    treasure_key_icon = 'treasure_key_icon'
