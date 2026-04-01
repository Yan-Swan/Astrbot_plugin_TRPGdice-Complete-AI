import random
import datetime
import hashlib
import ast
from typing import Optional

from astrbot.api.event import filter, AstrMessageEvent
from astrbot.api.star import Context, Star, register
from astrbot.api import message_components as Comp
from astrbot.api.all import *
from astrbot.api import logger
from astrbot.core.agent.message import TextPart, UserMessageSegment, AssistantMessageSegment

# ========== SYSTEM IMPORT ========== #
import json
import re
import time
import os
import uuid
import sqlite3
from faker import Faker

# ========== MODULE IMPORT ========== #
from .component import character as charmod
from .component import dice as dice_mod
from .component import sanity
from .component.output import get_output
from .component.utils import generate_names, roll_character, format_character, roll_dnd_character, format_dnd_character, SYNONYMS
from .component.rules import modify_coc_great_sf_rule_command
from .component.log import JSONLoggerCore

logger_core = JSONLoggerCore()

PLUGIN_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_FOLDER = PLUGIN_DIR + "/chara_data/"  # 存储人物卡的文件夹

#先攻表
init_list = {}
current_index = {}

DEFAULT_DICE = 100

COMMANDS = [
    "show",
    "del",
    "clr",
    "export"
]

log_help_str = '''.log 指令一览：
    .log on -- 开启log记录。亚托莉会记录之后所有的对话，并保存在“群名+时间”文件夹内。（施工中）
    .log off -- 暂停log记录。在同一群聊内再次使用.log on，可以继续记录未完成的log。（施工中）
    .log end -- 结束log记录。亚托莉会在群聊内发送“群名+时间.txt”的log文件。（施工中）
'''

async def get_sender_nickname(client, group_id, sender_id) :
    payloads = {
        "group_id": group_id,
        "user_id": sender_id,
        "no_cache": True
    }
    
    ret = await client.api.call_action("get_group_member_info", **payloads)
    
    return ret["card"]

async def init():
    await logger_core.initialize()

@register("astrbot_plugin_TRPG", "shiroling", "TRPG玩家用骰", "1.0.3")
class DicePlugin(Star):
    def __init__(self, context: Context):
        self.wakeup_prefix = [".", "。", "/"]
        self.uni_cache = {}
        self.llm_post_enabled = True
        self.llm_post_max_chars = 4000
        
        super().__init__(context)

    async def save_log(self, group_id, content) :
        ok, info = await logger_core.add_message(
            group_id=group_id,
            user_id="Bot",
            nickname="风铃Velinithra",
            timestamp=int(time.time()),
            text=content,
            isDice = True
        )

    async def _postprocess_with_llm(self, event: AstrMessageEvent, plugin_result: str) -> Optional[str]:
        if not self.llm_post_enabled:
            return None

        try:
            umo = event.unified_msg_origin
            provider_id = await self.context.get_current_chat_provider_id(umo)
            if not provider_id:
                return None

            conv_mgr = self.context.conversation_manager
            curr_cid = await conv_mgr.get_curr_conversation_id(umo)
            conversation = await conv_mgr.get_conversation(umo, curr_cid)
            history = conversation.history if conversation else ""

            # Avoid oversized prompts that may hurt latency and stability.
            history = str(history)[-self.llm_post_max_chars:]
            plugin_result = str(plugin_result)[:self.llm_post_max_chars]
            user_query = str(event.message_str)
            user_name = event.get_sender_name()

            prompt = f"""
            以下是对话历史：
            {history}

            用户掷骰指令：{user_query}
            用户名：{user_name}
            骰子结果：{plugin_result}

            你是一个TRPG（桌面角色扮演）游戏主持人，擅长根据玩家的掷骰结果和游戏情境提供生动、有趣的反馈。
            请结合以上所有信息，给出简洁、准确的最终回复。无需额外给出数值。
            """
            
            llm_resp = await self.context.llm_generate(
                chat_provider_id=provider_id,
                prompt=prompt,
            )

            output_text = plugin_result+"\n"+llm_resp.completion_text if '.rh' not in user_query else "Hidden result"

            await conv_mgr.add_message_pair(
                cid=curr_cid,
                user_message=UserMessageSegment(content=[TextPart(text=user_query)]),
                assistant_message=AssistantMessageSegment(
                content=[TextPart(text=output_text)]
                ),
            )
            await self.save_log(group_id = event.get_group_id(), content = output_text)
            return llm_resp.completion_text if llm_resp else None
        except Exception as e:
            logger.error(f"LLM 后处理失败: {e}")
            return None

    async def _send_group_text(self, event: AstrMessageEvent, text: str):
        if not text:
            return

        client = event.bot
        payloads = {
            "group_id": event.get_group_id(),
            "message": [
                {"type": "reply", "data": {"id": event.message_obj.message_id}},
                {"type": "at", "data": {"qq": event.get_sender_id()}},
                {"type": "text", "data": {"text": "\n" + text}}
            ]
        }
        await client.api.call_action("send_group_msg", **payloads)

    async def _send_private_text(self, event: AstrMessageEvent, text: str):
        if not text:
            return

        client = event.bot
        payloads = {
            "user_id": event.get_sender_id(),
            "message": [
                {"type": "text", "data": {"text": text}}
            ]
        }
        await client.api.call_action("send_private_msg", **payloads)

    
    # @filter.command("r")
    async def handle_roll_dice(self, event: AstrMessageEvent, message: str = None, remark : str = None):
        """普通掷骰：改为直接调用 dice.handle_roll_dice，输出由 get_output 管理（无 fallback）"""
        
        message = message.strip() if message else None

        user_id = event.get_sender_id()
        group_id = event.get_group_id()
        client = event.bot
        
        ret = await get_sender_nickname(client, group_id, user_id)
        ret = event.get_sender_name() if ret == "" else ret

        # 让 dice 模块处理表达式并返回由 get_output 格式化好的文本（或错误文本）
        result_text = dice_mod.handle_roll_dice(message if message else f"1d{dice_mod.DEFAULT_DICE}", name = ret, remark = remark)
        message_id = event.message_obj.message_id
        payloads = {
            "group_id": group_id,
            "message": [
                {"type": "reply", "data": {"id": message_id}},
                {"type": "at", "data": {"qq": user_id}},
                {"type": "text", "data": {"text": "\n" + result_text}}
            ]
        }
        
        await self.save_log(group_id = event.get_group_id(), content = result_text)
        
        await client.api.call_action("send_group_msg", **payloads)

        llm_text = await self._postprocess_with_llm(event, result_text)
        if llm_text:
            await self._send_group_text(event, llm_text)

    @filter.command("rv")
    async def roll_dice_vampire(self, event: AstrMessageEvent, dice_count: str = "1", difficulty: str = "6"):
        """吸血鬼掷骰：使用 dice.roll_dice_vampire 得到内部结果，然后通过 get_output 输出模板文本（无 fallback）"""
        # 验证参数
        try:
            int_dice_count = int(dice_count)
            int_difficulty = int(difficulty)
        except ValueError:
            err = get_output("dice.vampire.error", error="非法数值")
            yield event.plain_result(err)
            return

        result_body = dice_mod.roll_dice_vampire(int_dice_count, int_difficulty)

        user_id = event.get_sender_id()
        group_id = event.get_group_id()
        client = event.bot

        ret = await get_sender_nickname(client, group_id, user_id)
        ret = event.get_sender_name() if ret == "" else ret
        text = get_output("dice.vampire.success", result=result_body, name = ret)
        
        message_id = event.message_obj.message_id
        payloads = {
            "group_id": group_id,
            "message": [
                {"type": "reply", "data": {"id": message_id}},
                {"type": "at", "data": {"qq": user_id}},
                {"type": "text", "data": {"text": "\n" + text}}
            ]
        }

        await self.save_log(group_id = event.get_group_id(), content = text)
        
        await client.api.call_action("send_group_msg", **payloads)

        llm_text = await self._postprocess_with_llm(event, text)
        if llm_text:
            await self._send_group_text(event, llm_text)
            
    async def roll_hidden(self, event: AstrMessageEvent, message: str = None):
        """私聊发送掷骰结果 —— 所有文本由 get_output 管理（无 fallback）"""
        sender_id = event.get_sender_id()
        message = message.strip() if message else f"1d{dice_mod.DEFAULT_DICE}"

        notice_text = get_output("dice.hidden.group")
        yield event.plain_result(notice_text)

        private_text = dice_mod.roll_hidden(message)

        # 3) 发送私聊（使用平台 API）
        client = event.bot
        payloads = {
            "user_id": sender_id,
            "message": [
                {
                    "type": "text",
                    "data": {
                        "text": private_text
                    }
                }
            ]
        }
        
        await self.save_log(group_id = event.get_group_id(), content = "[Private Roll Result]" + private_text)
        
        await client.api.call_action("send_private_msg", **payloads)

        llm_text = await self._postprocess_with_llm(event, private_text)
        if llm_text:
            await self._send_private_text(event, llm_text)


    @filter.command("st")
    async def status(self, event: AstrMessageEvent, attributes: str = None, exp : str = None):
        """人物卡属性更新 / 掷骰 (V4 - 区分单/多重赋值)"""
        if not attributes:
            return

        if attributes in COMMANDS :
            return

        user_id = event.get_sender_id()
        group_id = event.get_group_id()
        chara_id = charmod.get_current_character_id(group_id, user_id)
        
        if not chara_id:
            yield get_output("pc.show.no_active")
            return

        chara_data = charmod.load_character(group_id, user_id, chara_id)
        full_expr = (str(attributes) if attributes else "") + (str(exp) if exp else "")
        attributes_clean = re.sub(r'\s+', '', full_expr)

        # --- 
        # ⬇️ 多重赋值逻辑 ⬇️
        # ---
        multi_assign_pattern = r"^((?:[\u4e00-\u9fa5a-zA-Z/_]+)(\d+))+$"

        # 1. 检查是否符合“严格”的多重赋值模式
        if re.match(multi_assign_pattern, attributes_clean):
            
            matches = re.findall(r"([\u4e00-\u9fa5a-zA-Z/_]+)(\d+)", attributes_clean)
            
            derived_tips = None
            
            if len(matches) > 1:
                
                for match_pair in matches:
                    attribute = match_pair[0]
                    value_num = int(match_pair[1])

                    if attribute not in chara_data["attributes"]:
                        chara_data["attributes"][attribute] = 1
                    
                    new_value = value_num
                    derived_tips = charmod.sync_derived_attributes(chara_data)
                    
                    chara_data["attributes"][attribute] = max(0, new_value)
                    
                # (循环外) 一次性保存并发送汇总回执
                charmod.save_character(group_id, user_id, chara_id, chara_data)
                if event.get_platform_name() == "aiocqhttp":
                    await self._update_user_nickname_card(event.bot, group_id, user_id)
                
                num_updates = len(matches)
                response = get_output("pc.update.batch_success", count=num_updates)
                
                if derived_tips:
                        response += "\n自动更新: " + ", ".join(derived_tips)
                
                await self.save_log(group_id=event.get_group_id(), content=response)
                yield event.plain_result(response)
                return # --- 多重更新结束 ---

            # 3. 如果 len(matches) == 1 (例如 .st 力量100)
            #    我们什么也不做 (pass)，让代码 *故意*
            #    掉入下面的“旧的单一属性逻辑”中去处理，
            #    以便它能输出 "力量: 0 -> 100" 的详细信息。

        # --- 
        # ⬆️ 多重赋值逻辑结束 ⬆️
        # ---

        # --- 
        # ⬇️ 旧的单一属性逻辑 (现在也会处理 .st 力量100) ⬇️
        # ---
        
        match = re.match(r"([\u4e00-\u9fa5a-zA-Z]+)\s*([+\-*]?)\s*(\d+(?:d\d+)?|\d*)", attributes_clean)
        if not match:
            yield get_output("pc.update.error_format")
            return

        attribute = match.group(1)
        operator = match.group(2) if match.group(2) else None
        value_expr = match.group(3) if match.group(3) else None

        derived_tips = None

        if attribute not in chara_data["attributes"]:
            chara_data["attributes"][attribute] = 1

        current_value = chara_data["attributes"][attribute]

        value_num = 0
        roll_detail = ""
        
        if value_expr and 'd' in value_expr.lower():
            dice_match = re.match(r"(\d*)d(\d+)", value_expr.lower())
            if dice_match:
                dice_count = int(dice_match.group(1)) if dice_match.group(1) else 1
                dice_faces = int(dice_match.group(2))
                rolls = dice_mod.roll_dice(dice_count, dice_faces)
                value_num = sum(rolls)
                roll_detail = get_output("dice.detail", detail=f"[{' + '.join(map(str, rolls))}] = {value_num}")
        elif value_expr:
            try:
                value_num = int(value_expr)
            except ValueError:
                yield get_output("pc.show.invalid_value", value=value_expr)
                return
        
        # (如果 value_expr 为空，例如 ".st san-")
        # 此时 value_num 保持为 0，这在COC中可能是有效的 (e.g. 减去0)
        # 如果你不希望这样，可以在这里加一个检查：
        if operator and not value_expr:
             yield get_output("pc.update.error_format_no_value") # 缺少值
             return

        # 根据运算符计算新值
        if operator == "+":
            new_value = current_value + value_num
        elif operator == "-":
            new_value = current_value - value_num
        elif operator == "*":
            new_value = current_value * value_num
        else:  # 无运算符 (例如 .st 力量100)，直接赋值
            new_value = value_num

        ### NEW : 更新所有的同义词
        target_group = [attribute]
        for group in SYNONYMS.SYNONYM:
            if attribute in group:
                target_group = group
                break
            
        new_val_final = max(0, new_value)
        for attr_name in target_group:
            if attr_name in chara_data["attributes"]:
                chara_data["attributes"][attr_name] = new_val_final

        derived_tips = charmod.sync_derived_attributes(chara_data)
        charmod.save_character(group_id, user_id, chara_id, chara_data)
        # 触发静默更新名片
        if event.get_platform_name() == "aiocqhttp":
            await self._update_user_nickname_card(event.bot, group_id, user_id)

        response = get_output("pc.update.success", attr=attribute, old=current_value, new=new_value)
        if roll_detail:
            response += "\n" + roll_detail
            
        if derived_tips:
            response += "\n自动更新: " + ", ".join(derived_tips)

        await self.save_log(group_id=event.get_group_id(), content=response)
        yield event.plain_result(response)

    @command_group("st")
    async def st(self, event: AstrMessageEvent, attributes: str = None, exp : str = None):
        """人物卡属性更新 / 掷骰"""
        pass


    @st.command("show")
    async def pc_show_character(self, event: AstrMessageEvent, *, args_str: str = ""):
        """
        .st show [属性...] [@某人] / .st show [数字] / .st show
        (V4 - 支持 @ 其他玩家查看其属性，智能过滤群名片残留)
        """
        group_id = str(event.get_group_id())
        
        # --- 1. 解析目标用户 (识别是否 @ 了别人) ---
        target_user_id = str(event.get_sender_id())
        for comp in event.message_obj.message:
            if isinstance(comp, Comp.At):  
                target_user_id = str(comp.qq)
                break
                
        chara_id = charmod.get_current_character_id(group_id, target_user_id)
        
        if not chara_id:
            if target_user_id == str(event.get_sender_id()):
                yield event.plain_result(get_output("pc.show.no_active"))
            else:
                yield event.plain_result("该玩家尚未在当前群组绑定人物卡哦。")
            return

        chara_data = charmod.load_character(group_id, target_user_id, chara_id)
        if not chara_data:
            yield event.plain_result(get_output("pc.show.load_fail", id=chara_id))
            return
        
        chara_attrs = chara_data.get("attributes", {})
        if not chara_attrs:
            yield event.plain_result(get_output("pc.show.attr_missing"))
            return

        base_str = args_str.split('@')[0] if '@' in args_str else args_str
        
        # B. 进一步剔除可能存在的 CQ 码（AstrBot 有时会将 @ 转为 CQ 码字符串）
        clean_text = re.sub(r'\[CQ:at.*?\]', ' ', base_str)
        
        # C. 拆分单词并过滤掉群名片生成的“噪音”
        raw_words = clean_text.split()
        valid_words = []
        
        for w in raw_words:
            upper_w = w.upper()
            # 1. 过滤掉 @ 符号本身或可能的残留
            if w.startswith('@'): continue
            # 2. 过滤掉由 .sn 指令生成的群名片残影 (HP:10/10 SAN:50 等)
            if 'HP:' in upper_w or 'SAN:' in upper_w or 'DEX:' in upper_w: continue
            # 3. 过滤掉格式化的数值（如 13/13）
            if re.match(r'^\d+/\d+$', w): continue 
            # 4. 过滤掉被 @ 者的角色名本身（如果名字恰好在文本里）
            if w == chara_data.get('name'): continue
            
            valid_words.append(w)

        # --- 3. 显示逻辑 ---

        # 场景 A: 如果没有有效参数 ( .st show ) -> 显示 *PRIMARY* 属性
        if not valid_words:
            primary_attributes = {} 
            
            for attr, value in chara_attrs.items():
                primary_name = SYNONYMS.SYNONYM_MAP.get(attr, attr)
                if primary_name not in primary_attributes:
                    primary_attributes[primary_name] = (attr, value)
                else:
                    current_stored_attr_name = primary_attributes[primary_name][0]
                    if current_stored_attr_name != primary_name and attr == primary_name:
                        primary_attributes[primary_name] = (attr, value)

            output_list = []
            for primary_name, (original_attr, value) in sorted(primary_attributes.items()):
                output_list.append(f"{primary_name}: {value}")
                
            attributes_str = "\n".join(output_list)
            
            yield event.plain_result(get_output("pc.show.all", name=chara_data['name'], attributes=attributes_str))
            return

        # 场景 B: 尝试转为数字 ( .st show 30 ) 
        if len(valid_words) == 1 and valid_words[0].isdigit():
            threshold = int(valid_words[0])
            output_parts = []
            for attr, value in chara_attrs.items():
                if value > threshold:
                    output_parts.append(f"· {attr}: {value}")
            
            if not output_parts:
                yield event.plain_result(get_output("pc.show.none_above", num=threshold))
            else:
                header = get_output("pc.show.above_threshold_header", num=threshold)
                response = header + "\n" + "\n".join(output_parts)
                if target_user_id != str(event.get_sender_id()):
                    response = f"【{chara_data['name']}】的属性：\n" + response
                yield event.plain_result(response)
            return

        # 场景 C: 按属性名处理 ( .st show 力量 敏捷 )
        found_attrs = []
        not_found_attrs = []
        
        for key in valid_words:
            if key in chara_attrs:
                val = chara_attrs[key]
                found_attrs.append(get_output("pc.show.attr", attr=key, value=val))
            else:
                # 二次防误伤：如果残留的名片名字带有空格（如 John Doe）被切碎了
                # 只要这个碎词属于对方名字的一部分，我们就默默包容它，不报错
                if key in chara_data.get('name', ''): 
                    continue
                not_found_attrs.append(key)
        
        output_parts = []
        if target_user_id != str(event.get_sender_id()):
            output_parts.append(f"【{chara_data['name']}】的属性：")
            
        if found_attrs:
            output_parts.append("\n".join(found_attrs))
        if not_found_attrs:
            missing_str = ", ".join(not_found_attrs)
            output_parts.append(get_output("pc.show.attr_missing", attribute=missing_str))

        if output_parts:
            yield event.plain_result("\n".join(output_parts))
        
    @st.command("del")
    async def st_del(self, event: AstrMessageEvent, *, args_str: str = ""):
        """ 
        .st del <属性1> <属性2> ... 
        (V2 - 支持删除同义词组, 并保护核心属性)
        """
        
        user_id = event.get_sender_id()
        group_id = event.get_group_id()
        chara_id = charmod.get_current_character_id(group_id, user_id)
        
        
        if not chara_id:
            yield event.plain_result(get_output("pc.show.no_active"))
            return

        chara_data = charmod.load_character(group_id, user_id, chara_id)
        if not chara_data:
            yield event.plain_result(get_output("pc.show.load_fail", id=chara_id))
            return
            
        args_str = args_str.strip()
        if not args_str:
            yield event.plain_result(get_output("pc.del.no_args"))
            return

        # --- 
        # ⬇️ 全新的核心逻辑 ⬇️
        # ---
        
        keys_to_del_input = args_str.split() # 用户输入的词
        
        # 跟踪结果
        protected_keys_requested = [] # 跟踪用户试图删除的受保护词
        deleted_groups_primary = set()  # 跟踪已删除的 *主名* (防重复删除)
        deleted_keys_actual = []      # 跟踪实际从卡上删除的 *所有* 词
        not_found_keys_input = []   # 跟踪用户输入了，但卡上(及其同义词)都不存在的词

        chara_attrs = chara_data.get("attributes", {})

        for requested_key in keys_to_del_input:
            
            # 1. 检查是否受保护
            if requested_key in SYNONYMS.PROTECTED_ATTRIBUTES:
                protected_keys_requested.append(requested_key)
                continue

            # 2. 找到这个词的 "主名" (e.g. "str" -> "力量"; "临时" -> "临时")
            primary_name = SYNONYMS.SYNONYM_MAP.get(requested_key, requested_key)
            
            # 3. 如果这个 *组* 已经被处理过，就跳过
            if primary_name in deleted_groups_primary:
                continue

            # 4. 找到这个主名对应的 *所有同义词* (e.g. "力量" -> {"力量", "str"})
            synonyms_in_group = SYNONYMS.PRIMARY_TO_ALL_MAP.get(primary_name, {primary_name})

            # 5. 遍历角色卡，删除这个组的所有同义词
            found_at_least_one = False
            for syn in synonyms_in_group:
                if syn in chara_attrs:
                    del chara_attrs[syn] # 从字典中删除
                    deleted_keys_actual.append(syn)
                    found_at_least_one = True

            # 6. 记录结果
            if found_at_least_one:
                deleted_groups_primary.add(primary_name) # 标记这个主名组已处理
            else:
                # 如果卡上一个同义词都没有，记为 "未找到"
                not_found_keys_input.append(requested_key)

        # --- 组合输出 ---
        response_parts = []
        if protected_keys_requested:
            response_parts.append(get_output("st.del.protected", attr=", ".join(set(protected_keys_requested))))
            
        if deleted_keys_actual:
            response_parts.append(get_output("st.del.success", attr=", ".join(deleted_keys_actual)))
            
        if not_found_keys_input:
            response_parts.append(get_output("st.del.not_found", attr=", ".join(not_found_keys_input)))
        
        response = "\n".join(response_parts)

        # --- 保存并响应 ---
        if deleted_groups_primary:
            charmod.save_character(group_id, user_id, chara_id, chara_data)
            await self.save_log(group_id=event.get_group_id(), content=response)
            
        yield event.plain_result(response)


    @st.command("clr")
    async def st_clr(self, event: AstrMessageEvent):
        """ .st clr 清除所有非核心属性 (V2 - 统一 get_output 键) """
        
        user_id = event.get_sender_id()
        client = event.bot
        group_id = event.get_group_id()
        ret = await get_sender_nickname(client, group_id, user_id)
        ret = event.get_sender_name() if ret == "" else ret
        
        chara_id = charmod.get_current_character_id(group_id, user_id)
        
        if not chara_id:
            yield event.plain_result(get_output("st.show.no_active")) # 复用
            return

        chara_data = charmod.load_character(group_id, user_id, chara_id)
        if not chara_data:
            yield event.plain_result(get_output("st.show.load_fail", id=chara_id)) # 复用
            return
            
        chara_attrs = chara_data.get("attributes", {})
        if not chara_attrs:
            # 统一: pc. -> st.
            yield event.plain_result(get_output("st.clr.nothing", name = ret))
            return

        old_count = len(chara_attrs)
        
        new_attrs = {
            key: value for key, value in chara_attrs.items() 
            # 统一: 使用 self.
            if key in SYNONYMS.PROTECTED_ATTRIBUTES
        }
        
        new_count = len(new_attrs)
        deleted_count = old_count - new_count

        if deleted_count == 0:
            # 统一: pc. -> st.
            yield event.plain_result(get_output("st.clr.nothing"))
            return
        
        chara_data["attributes"] = new_attrs
        charmod.save_character(group_id, user_id, chara_id, chara_data)
        
        # 统一: pc. -> st. (参数 'count' 保持不变，因为它不是属性列表)
        response = get_output("st.clr.success", name=ret)
        
        await self.save_log(group_id=event.get_group_id(), content=response)
        yield event.plain_result(response)
        
    @st.command("export")
    async def st_export(self, event: AstrMessageEvent):
        """ 
        .st export 
        直接导出当前卡内所有属性，格式为：属性1数值1属性2数值2...
        不进行任何去重或同义词处理。
        """
        user_id = event.get_sender_id()
        group_id = event.get_group_id()
        chara_id = charmod.get_current_character_id(group_id, user_id)
        

        if not chara_id:
            yield event.plain_result(get_output("pc.show.no_active"))
            return

        chara_data = charmod.load_character(group_id, user_id, chara_id)
        # 获取属性字典，如果不存在则为空字典
        chara_attrs = chara_data.get("attributes", {})
        
        if not chara_attrs:
            yield event.plain_result(get_output("pc.show.attr_missing"))
            return

        # --- 核心逻辑：直接拼接 ---
        # 直接遍历字典，不跳过任何同义词
        export_parts = []
        for attr_name, value in chara_attrs.items():
            export_parts.append(f"{attr_name}{value}")

        # 组合成长字符串
        export_str = "".join(export_parts)

        # 构建输出文案
        # 建议在 get_output 对应的模板中加入类似 "导出数据为：\n{data}" 的格式
        response = get_output("pc.export.success", name=chara_data.get('name', '未命名'), data=export_str)
        
        yield event.plain_result(response)

    @filter.command("fire")
    async def handle_pistol_fire(self, event: AstrMessageEvent, arg1: str = "", arg2: str = ""):
        """手枪三连发：.fire [p1/p2] [技能值]"""
        
        user_id = event.get_sender_id()
        group_id = event.get_group_id()
        client = event.bot
        
        # 1. 环境准备：获取昵称和角色数据
        nickname = await get_sender_nickname(client, group_id, user_id)
        nickname = event.get_sender_name() if nickname == "" else nickname
        
        chara_data = charmod.get_current_character(group_id, user_id)
        
        # 2. 调用后端获取结果文本
        full_args = (str(arg1) + str(arg2)).lower().strip()
        result_text = dice_mod.handle_pistol_fire(full_args, name=nickname, chara_data=chara_data)
        
        # 3. 构造复合消息 payloads
        message_id = event.message_obj.message_id
        payloads = {
            "group_id": group_id,
            "message": [
                {"type": "reply", "data": {"id": message_id}},
                {"type": "at", "data": {"qq": user_id}},
                {"type": "text", "data": {"text": "\n" + result_text}}
            ]
        }
        
        # 4. 日志与发送
        await self.save_log(group_id=group_id, content=result_text)
        await client.api.call_action("send_group_msg", **payloads)

    @command_group("pc") # type: ignore
    def pc(self):
        pass

    # ----------------- pc create (新建并自动绑定) -----------------
    @pc.command("new")
    async def pc_new_character(self, event, name: str):
        """
        .pc new <角色名>
        仅通过名字在当前群路径下新建角色并绑定。
        """
        user_id = event.get_sender_id()
        group_id = str(event.get_group_id())
        
        # 1. 检查当前群路径下是否已经存在同名角色
        characters = charmod.get_all_characters(group_id, user_id)
        if name in characters:
            yield event.plain_result(get_output("pc.create.duplicate", name=name))
            return

        # 2. 创建一个完全空白的属性字典
        attributes_dict = {}

        # 3. 调用 charmod 创建角色
        # charmod.create_character 内部逻辑：
        # - 生成 UUID
        # - 保存到 chara_data/{group_id}/{user_id}/{uuid}.json
        # - 在 metadata/{user_id}/bindings.json 中记录该群绑定此 UUID
        chara_id = charmod.create_character(group_id, user_id, name, attributes_dict)
        
        response = get_output("pc.create.success", name=name, id=chara_id)
        yield event.plain_result(response)
        await self.save_log(group_id=group_id, content=response)

    # ----------------- pc list (列出当前群角色) -----------------
    @pc.command("list")
    async def pc_list_characters(self, event):
        user_id = event.get_sender_id()
        group_id = str(event.get_group_id())
        
        # 获取有序列表，方便以后实现序号操作
        characters = charmod.get_all_characters(group_id, user_id)
        if not characters:
            yield event.plain_result(get_output("pc.list.empty"))
            return

        # 关键：从 bindings 获取当前群绑定的角色
        current = charmod.get_current_character_id(group_id, user_id)
        
        # 排序以保证序号稳定
        sorted_chars = sorted(characters.items(), key=lambda x: x[0])
        chara_list = []
        for i, (name, ch_id) in enumerate(sorted_chars, 1):
            tag = "(当前)" if ch_id == current else ""
            chara_list.append(f"{i}. {name} {tag}")
            
        response = get_output("pc.list.result", list="\n".join(chara_list))
        yield event.plain_result(response)

    # ----------------- pc tag (绑定/切换) -----------------
    @pc.command("tag")
    async def pc_tag_character(self, event, identifier: str = None):
        user_id = event.get_sender_id()
        group_id = str(event.get_group_id())
        
        if not identifier:
            # 如果不填，执行解除绑定
            charmod.set_binding_info(user_id, group_id, None)
            yield event.plain_result("已解除当前群组的角色绑定。")
            return

        # 解析名字或序号
        chara_id = charmod.resolve_identifier(group_id, user_id, identifier)
        if not chara_id:
            yield event.plain_result(f"未找到角色: {identifier}")
            return

        charmod.set_binding_info(user_id, group_id, chara_id)
        yield event.plain_result(f"已将角色绑定到当前群组。")
        
    @pc.command("rename")
    async def pc_rename_character(self, event, arg1: str, arg2: str = None):
        """
        .pc rename <新角色名>
        .pc rename <角色名|序号> <新角色名>
        """
        user_id = event.get_sender_id()
        group_id = str(event.get_group_id())

        # 逻辑分流：判断是修改当前还是修改指定
        if arg2 is None:
            # 场景：.pc rename <新名字>
            new_name = arg1
            chara_id = charmod.get_current_character_id(group_id, user_id)
            if not chara_id:
                yield event.plain_result("当前群组未绑定角色，请指定要改名的角色。")
                return
        else:
            # 场景：.pc rename <旧名|序号> <新名字>
            identifier = arg1
            new_name = arg2
            chara_id = charmod.resolve_identifier(group_id, user_id, identifier)
            if not chara_id:
                yield event.plain_result(f"未找到角色: {identifier}")
                return

        # 执行重命名
        success, info = charmod.rename_character(group_id, user_id, chara_id, new_name)

        if success:
            response = f"已将角色「{info}」重命名为「{new_name}」。"
        else:
            if info == "duplicate":
                response = f"重命名失败：当前群已存在名为「{new_name}」的角色。"
            else:
                response = "重命名失败：角色档案加载异常。"

        yield event.plain_result(response)
        await self.save_log(group_id=group_id, content=response)

    # ----------------- pc delete (删除并解绑) -----------------
    @pc.command("delete")
    async def pc_delete_character(self, event, identifier: str):
        user_id = event.get_sender_id()
        group_id = str(event.get_group_id())
        
        # 1. 先通过名字或序号解析出 chara_id
        chara_id = charmod.resolve_identifier(group_id, user_id, identifier)
        if not chara_id:
            yield event.plain_result("未找到该角色。")
            return
            
        # 2. 获取名字用于显示
        data = charmod.load_character(group_id, user_id, chara_id)
        name = data['name'] if data else "未知"

        # 3. 执行删除（内部含 bindings 清理）
        success, _ = charmod.delete_character(group_id, user_id, name)
        
        if not success:
            yield event.plain_result(get_output("pc.delete.fail", name=name))
            return
        yield event.plain_result(get_output("pc.delete.success", name=name))
        
    @pc.command("show")
    async def pc_show(self, event, *, args_str: str = ""):
        """
        .st show [属性...] / .st show [数字] / .st show
        (V3 - "show all" 支持同义词合并)
        """
        user_id = event.get_sender_id()
        group_id = event.get_group_id()
        chara_id = charmod.get_current_character_id(group_id, user_id)
        

        if not chara_id:
            yield event.plain_result(get_output("pc.show.no_active"))
            return

        chara_data = charmod.load_character(group_id, user_id, chara_id)
        if not chara_data:
            yield event.plain_result(get_output("pc.show.load_fail", id=chara_id))
            return
        
        chara_attrs = chara_data.get("attributes", {})
        if not chara_attrs:
            yield event.plain_result(get_output("pc.show.attr_missing"))
            return

        args_str = args_str.strip()

        # --- 
        # ⬇️ 关键修改在这里 ⬇️
        # ---

        # 1. 如果没有参数 ( .st show ) -> 显示 *PRIMARY* 属性 (合并同义词)
        if not args_str:
            primary_attributes = {} 
            
            for attr, value in chara_attrs.items():
                primary_name = SYNONYMS.SYNONYM_MAP.get(attr, attr)
                if primary_name not in primary_attributes:
                    primary_attributes[primary_name] = (attr, value)
                else:
                    current_stored_attr_name = primary_attributes[primary_name][0]
                    
                    if current_stored_attr_name != primary_name and attr == primary_name:
                        primary_attributes[primary_name] = (attr, value)

            # 4. 格式化输出
            # 现在 primary_attributes 包含了去重后的主属性列表
            output_list = []
            # 按主属性名排序
            for primary_name, (original_attr, value) in sorted(primary_attributes.items()):
                output_list.append(f"{primary_name}: {value}")
                
            attributes_str = "\n".join(output_list)
            
            yield event.plain_result(get_output("pc.show.all", name=chara_data['name'], attributes=attributes_str))
            return

        # --- 
        # ⬆️ 修改结束 ⬆️
        # ---

        # 2. 尝试转为数字 ( .st show 30 ) - (此部分逻辑保持不变)
        try:
            threshold = int(args_str)
            output_parts = []
            for attr, value in chara_attrs.items():
                if value > threshold:
                    output_parts.append(f"· {attr}: {value}")
            
            if not output_parts:
                yield event.plain_result(get_output("pc.show.none_above", num=threshold))
            else:
                header = get_output("pc.show.above_threshold_header", num=threshold)
                response = header + "\n" + "\n".join(output_parts)
                yield event.plain_result(response)
            return
        except ValueError:
            pass

        # 3. 按属性名处理 ( .st show 力量 敏捷 ) - (此部分逻辑保持不变)
        attr_keys_to_show = args_str.split()
        found_attrs = []
        not_found_attrs = []
        
        for key in attr_keys_to_show:
            if key in chara_attrs:
                val = chara_attrs[key]
                found_attrs.append(get_output("pc.show.attr", attr=key, value=val))
            else:
                not_found_attrs.append(key)
        
        output_parts = []
        if found_attrs:
            output_parts.append("\n".join(found_attrs))
        if not_found_attrs:
            missing_str = ", ".join(not_found_attrs)
            output_parts.append(get_output("pc.show.attr_missing", attribute=missing_str))

        if output_parts:
            yield event.plain_result("\n".join(output_parts))

    # ----------------- .pc push -----------------
    @pc.command("push")
    async def pc_push_character(self, event):
        """
        手动 Push：将当前群组的角色档案标记为全域最新版。
        """
        user_id = str(event.get_sender_id())
        group_id = str(event.get_group_id())
        
        # 1. 获取当前群绑定的角色 ID
        chara_id = charmod.get_current_character_id(group_id, user_id)
        if not chara_id:
            # 这里的 get_output 可以在语言包里写：“本群未绑定角色，无法执行 Push。”
            yield event.plain_result(get_output("pc.push.no_pc"))
            return

        # 2. 调用后端 touch 逻辑，强行刷新 mtime 字段
        if charmod.touch_character(group_id, user_id, chara_id):
            yield event.plain_result(get_output("pc.push.success"))
        else:
            yield event.plain_result(get_output("pc.push.fail"))

    # ----------------- .pc fetch -----------------
    @pc.command("fetch")
    async def pc_fetch_list(self, event):
        user_id = str(event.get_sender_id())
        
        all_chars = charmod.get_all_universal_characters(user_id)
        self.uni_cache[user_id] = all_chars # 更新序号缓存
        
        if not all_chars:
            yield event.plain_result(get_output("pc.uni.no_pc"))
            return
            
        char_lines = []
        for i, char in enumerate(all_chars, 1):
            # 格式化显示时间
            logger.info(char)
            time_str = time.strftime("%m-%d %H:%M", time.localtime(char['mtime']))
            # 标记来源（如果是 Vault 则高亮）
            source_label = f"{char['group_id']}"
            
            char_lines.append(f"{i}. {char['name']} (最新来源:{source_label} | {time_str})")
        
        msg = "\n".join(char_lines)
        yield event.plain_result(get_output("pc.uni.success", msg=msg))

    # ----------------- .pc pull -----------------
    @pc.command("pull")
    async def pc_pull_character(self, event, index: int):
        user_id = str(event.get_sender_id())
        group_id = str(event.get_group_id())
        
        # 1. 获取包含 mtime 信息的最新全域列表
        all_chars = charmod.get_all_universal_characters(user_id)
        self.uni_cache[user_id] = all_chars
        
        cache = self.uni_cache.get(user_id)
        if not cache or index > len(cache) or index <= 0:
            yield event.plain_result(get_output("pc.pull.no_pc"))
            return
            
        target = cache[index-1] # 这是全域中最热/最新的那个版本信息
        target_uuid = target['uuid']

        # 2. 获取本群当前该角色的信息（如果存在）
        local_exists = charmod.check_character_file_exists(group_id, user_id, target_uuid)
        
        needs_copy = True
        if local_exists:
            # 获取本地文件的修改时间进行对比
            local_mtime = charmod.get_local_file_mtime(group_id, user_id, target_uuid)
            # 如果本地的时间 >= 全域最热的时间，说明本地已经是最新，无需拷贝
            if local_mtime >= target['mtime']:
                needs_copy = False

        # 3. 执行逻辑分支
        if not needs_copy:
            # 文件已经是最新，仅检查绑定
            current_bound = charmod.get_current_character_id(group_id, user_id)
            if current_bound == target_uuid:
                yield event.plain_result(get_output("pc.pull.exist")) # 完全一致，无需操作
            else:
                charmod.set_binding_info(user_id, group_id, target_uuid)
                yield event.plain_result(get_output("pc.pull.success", name=target['name']))
            return

        # 4. 需要拷贝（本地不存在，或者远程版本更新）
        # 调用 clone，内部执行物理覆盖
        success = charmod.clone_character_to_group(user_id, target['group_id'], group_id, target_uuid)
        
        if success:
            charmod.set_binding_info(user_id, group_id, target_uuid)
            if local_exists : 
                yield event.plain_result(get_output("pc.pull.sync", name=target['name']))
            else :
                yield event.plain_result(get_output("pc.pull.success", name=target['name']))
        else:
            yield event.plain_result(get_output("pc.pull.fail"))

    # ----------------- filter sn -----------------
    @filter.command("sn")
    async def filter_set_nickname(self, event):
        if event.get_platform_name() != "aiocqhttp":
            yield event.plain_result(get_output("nick.platform_unsupported"))
            return

        from astrbot.core.platform.sources.aiocqhttp.aiocqhttp_message_event import AiocqhttpMessageEvent # type: ignore
        client = event.bot
        user_id = event.get_sender_id()
        group_id = event.get_group_id()

        chara_id = charmod.get_current_character_id(group_id, user_id)
        chara_data = charmod.load_character(group_id, user_id, chara_id)
        if not chara_data:
            yield event.plain_result(get_output("nick.no_character", id=chara_id))
            return

        max_hp = (chara_data['attributes'].get('con', 0) + chara_data['attributes'].get('siz', 0)) // 10
        name = chara_data['name']
        hp = chara_data['attributes'].get('hp', 0)
        san = chara_data['attributes'].get('san', 0)
        dex = chara_data['attributes'].get('dex', 0)
        new_card = f"{name} HP:{hp}/{max_hp} SAN:{san} DEX:{dex}"

        payloads = {"group_id": group_id, "user_id": user_id, "card": new_card}
        await client.api.call_action("set_group_card", **payloads)

        yield event.plain_result(get_output("nick.success"))
        
        
    async def _update_user_nickname_card(self, client, group_id: str, user_id: str):
        """静默更新玩家群名片，不返回任何群消息提示"""
        chara_id = charmod.get_current_character_id(group_id, user_id)
        if not chara_id:
            return False
            
        chara_data = charmod.load_character(group_id, user_id, chara_id)
        if not chara_data:
            return False

        max_hp = (chara_data['attributes'].get('con', 0) + chara_data['attributes'].get('siz', 0)) // 10
        name = chara_data['name']
        hp = chara_data['attributes'].get('hp', 0)
        san = chara_data['attributes'].get('san', 0)
        dex = chara_data['attributes'].get('dex', 0)
        
        new_card = f"{name} HP:{hp}/{max_hp} SAN:{san} DEX:{dex}"
        payloads = {"group_id": group_id, "user_id": user_id, "card": new_card}
        
        try:
            await client.api.call_action("set_group_card", **payloads)
            return True
        except Exception as e:
            logger.error(f"自动更新群名片失败: {e}")
            return False

    
    # ========================================================= #
    async def roll_attribute(self, event: AstrMessageEvent, skill_name: str, skill_value: str = None, roll_times = 1, target_user_id: str = None):
        group_id = event.get_group_id()
        # 决定最终查询的 user_id
        actual_user_id = target_user_id if target_user_id else event.get_sender_id()

        if skill_value is None:
            skill_value = charmod.get_skill_value(group_id, actual_user_id, skill_name)
            
        if skill_name == "" :
            skill_name = str(skill_value)

        client = event.bot
        
        # 获取目标的名字
        ret = await get_sender_nickname(client, group_id, actual_user_id)
        if ret == "":
            ret = event.get_sender_name() if actual_user_id == event.get_sender_id() else str(actual_user_id)
            
        # 如果是代投，在名字上做个小标记
        if target_user_id and target_user_id != event.get_sender_id():
            ret = f"{ret} (由 <{event.get_sender_name()}> 代投)"

        logger.info(ret)
        
        result_message = dice_mod.roll_attribute(roll_times, skill_name, skill_value, str(group_id), ret)

        payloads = {
            "group_id": group_id,
            "message": [
                {"type": "reply", "data": {"id": event.message_obj.message_id}},
                # 依然 @ 触发指令的人，提醒他结果出来了
                {"type": "at", "data": {"qq": event.get_sender_id()}}, 
                {"type": "text", "data": {"text": "\n" + result_message}}
            ]
        }
        await self.save_log(group_id = event.get_group_id(), content = result_message)
        await client.api.call_action("send_group_msg", **payloads)

        llm_text = await self._postprocess_with_llm(event, result_message)
        if llm_text:
            await self._send_group_text(event, llm_text)

    # 惩罚骰技能判定
    async def roll_attribute_penalty(self, event: AstrMessageEvent, dice_count: str = "1", skill_name: str = "", skill_value: str = None, roll_times = 1, target_user_id: str = None):
        group_id = event.get_group_id()
        actual_user_id = target_user_id if target_user_id else event.get_sender_id()

        if skill_value is None:
            skill_value = charmod.get_skill_value(group_id, actual_user_id, skill_name)
            
        if skill_name == "" :
            skill_name = str(skill_value)

        client = event.bot
        ret = await get_sender_nickname(client, group_id, actual_user_id)
        if ret == "":
            ret = event.get_sender_name() if actual_user_id == event.get_sender_id() else str(actual_user_id)
            
        if target_user_id and target_user_id != event.get_sender_id():
            ret = f"{ret} (由 <{event.get_sender_name()}> 代投)"

        result_message = dice_mod.roll_attribute_penalty(roll_times, dice_count, skill_name, skill_value, str(group_id), ret)

        payloads = {
            "group_id": group_id,
            "message": [
                {"type": "reply", "data": {"id": event.message_obj.message_id}},
                {"type": "at", "data": {"qq": event.get_sender_id()}},
                {"type": "text", "data": {"text": "\n" + result_message}}
            ]
        }

        await self.save_log(group_id = event.get_group_id(), content = result_message)
        await client.api.call_action("send_group_msg", **payloads)

        llm_text = await self._postprocess_with_llm(event, result_message)
        if llm_text:
            await self._send_group_text(event, llm_text)

    # 奖励骰技能判定
    async def roll_attribute_bonus(self, event: AstrMessageEvent, dice_count: str = "1", skill_name: str = "", skill_value: str = None, roll_times = 1, target_user_id: str = None):
        group_id = event.get_group_id()
        actual_user_id = target_user_id if target_user_id else event.get_sender_id()

        if skill_value is None:
            skill_value = charmod.get_skill_value(group_id, actual_user_id, skill_name)
            
        if skill_name == "" :
            skill_name = str(skill_value)

        client = event.bot
        ret = await get_sender_nickname(client, group_id, actual_user_id)
        if ret == "":
            ret = event.get_sender_name() if actual_user_id == event.get_sender_id() else str(actual_user_id)
            
        if target_user_id and target_user_id != event.get_sender_id():
            ret = f"{ret} (由 <{event.get_sender_name()}> 代投)"

        result_message = dice_mod.roll_attribute_bonus(roll_times, dice_count, skill_name, skill_value, str(group_id), ret)

        payloads = {
            "group_id": group_id,
            "message": [
                {"type": "reply", "data": {"id": event.message_obj.message_id}},
                {"type": "at", "data": {"qq": event.get_sender_id()}},
                {"type": "text", "data": {"text": "\n" + result_message}}
            ]
        }

        await self.save_log(group_id = event.get_group_id(), content = result_message)
        await client.api.call_action("send_group_msg", **payloads)

        llm_text = await self._postprocess_with_llm(event, result_message)
        if llm_text:
            await self._send_group_text(event, llm_text)

        
    # @filter.command("en")
    async def pc_grow_up(self, event: AstrMessageEvent, skill_name: str, skill_value: str = None):
        """
        .en 技能成长判定
        调用 character 模块的 grow_up 生成结果文本，再通过 event 发送给用户。
        """
        user_id = event.get_sender_id()
        group_id = event.get_group_id()

        # 调用 character.py 中同步逻辑函数，不传入额外函数引用
        result_str = charmod.grow_up(
            group_id,
            user_id,
            skill_name=skill_name,
            skill_value=skill_value
        )

        # 构造发送消息

        user_name = event.get_sender_name()
        client = event.bot  # 获取机器人 Client
        message_id = event.message_obj.message_id

        payloads = {
            "group_id": group_id,
            "message": [
                {
                    "type": "reply",
                    "data": {
                        "id": message_id
                    }
                },
                {
                    "type": "at",
                    "data": {
                        "qq": user_id
                    }
                },
                {
                    "type": "text",
                    "data": {
                        "text": "\n" + result_str
                    }
                }
            ]
        }

        await self.save_log(group_id = event.get_group_id(), content = result_str)
        await client.api.call_action("send_group_msg", **payloads)


    # ========================================================= #
    # san check
    # @filter.command("sc")
    async def pc_san_check(self, event: AstrMessageEvent, loss_formula: str):
        """理智检定"""
        user_id = event.get_sender_id()
        group_id = event.get_group_id()
        chara_data = charmod.get_current_character(group_id, user_id)
        client = event.bot
        
        if not chara_data:
            yield event.plain_result(get_output("pc.show.no_active"))
            return

        roll_result, san_value, result_msg, loss, new_san, expr = sanity.san_check(chara_data, loss_formula)

        # 更新人物卡
        chara_data["attributes"]["san"] = new_san
        charmod.save_character(group_id, user_id, chara_data["id"], chara_data)
        
        if event.get_platform_name() == "aiocqhttp":
            await self._update_user_nickname_card(client, group_id, user_id)

        if new_san == 0 :
            text = get_output(
                    "san.check_result.zero",
                    name=chara_data["name"],
                    roll_result=roll_result,
                    san_value=san_value,
                    result_msg=result_msg,
                    loss=loss,
                    new_san=new_san,
                    expr = expr
                )

        elif loss == 0 :
            text = get_output(
                "san.check_result.no_loss",
                name=chara_data["name"],
                roll_result=roll_result,
                san_value=san_value,
                result_msg=result_msg,
                loss=loss,
                new_san=new_san,
                expr = expr
            )
        elif loss < 5 :
            text = get_output(
                "san.check_result.loss",
                name=chara_data["name"],
                roll_result=roll_result,
                san_value=san_value,
                result_msg=result_msg,
                loss=loss,
                new_san=new_san,
                expr = expr
            )
        else :
            text = get_output(
                "san.check_result.great_loss",
                name=chara_data["name"],
                roll_result=roll_result,
                san_value=san_value,
                result_msg=result_msg,
                loss=loss,
                new_san=new_san,
                expr = expr
            )

        payloads = {
            "group_id": group_id,
            "message": [
                {"type": "reply", "data": {"id": event.message_obj.message_id}},
                {"type": "at", "data": {"qq": user_id}},
                {"type": "text", "data": {"text": "\n" + text}}
            ]
        }
        
        await self.save_log(group_id = event.get_group_id(), content = text)
        
        await client.api.call_action("send_group_msg", **payloads)

        llm_text = await self._postprocess_with_llm(event, text)
        if llm_text:
            await self._send_group_text(event, llm_text)


    async def pc_temporary_insanity(self, event: AstrMessageEvent):
        """临时疯狂"""
        result = sanity.get_temporary_insanity(sanity.phobias, sanity.manias)
        text = get_output("san.temporary_insanity", result=result, name=event.get_sender_name())
        await self.save_log(group_id = event.get_group_id(), content = text)
        yield event.plain_result(text)


    async def pc_long_term_insanity(self, event: AstrMessageEvent):
        """长期疯狂"""
        result = sanity.get_long_term_insanity(sanity.phobias, sanity.manias)
        text = get_output("san.long_term_insanity", result=result, name=event.get_sender_name())
        await self.save_log(group_id = event.get_group_id(), content = text)
        yield event.plain_result(text)

    # ========================================================= #
    #先攻相关
    class InitiativeItem:
        def __init__(self, name: str, init_value: int, player_id: int):
            self.name = name
            self.init_value = init_value
            self.player_id = player_id  # 用于区分同名不同玩家

    def add_item(self, item: InitiativeItem, group_id: str):
        """添加先攻项并排序"""
        init_list[group_id].append(item)
        self.sort_list(group_id)
    
    def remove_by_name(self, name: str, group_id: str):
        """按名字删除先攻项"""
        try:
            init_list[group_id] = [item for item in init_list[group_id] if item.name != name]
        except:
            init_list[group_id] = []
            current_index[group_id] = 0
    
    def remove_by_player(self, player_id: int, group_id: str):
        """按玩家ID删除先攻项"""
        init_list[group_id] = [item for item in init_list[group_id] if item.player_id != player_id]
    
    def init_clear(self, group_id: str):
        """清空先攻表"""
        init_list[group_id].clear()
        current_index[group_id] = -1
    
    def sort_list(self, group_id: str):
        """按先攻值降序排序 (稳定排序)"""
        init_list[group_id].sort(key=lambda x: x.init_value, reverse=True)
    
    def next_turn(self, group_id: str):
        """移动到下一回合并返回当前项"""
        if not init_list[group_id]:
            return None
        
        if current_index[group_id] < 0:
            current_index[group_id] = 0
        else:
            current_index[group_id] = (current_index[group_id] + 1) % len(init_list[group_id])
        
        return init_list[group_id][current_index[group_id]]
    
    def format_list(self, group_id: str) -> str:
        """格式化先攻表输出"""
        try:
            fl = init_list[group_id]
        except:
            init_list[group_id] = []
            return "先攻列表为空"

        if not fl:
            return "先攻列表为空"
        
        lines = []
        for i, item in enumerate(fl):
            prefix = "-> " if i == current_index[group_id] else "   "
            lines.append(f"{prefix}{item.name}: {item.init_value}")
        return "\n".join(lines)

    @filter.command("init")
    async def initiative(self , event: AstrMessageEvent , instruction: str = None, player_name: str = None):
        group_id = event.get_group_id()
        user_id = event.get_sender_id()
        user_name = event.get_sender_name()
        if not instruction:
            yield event.plain_result("当前先攻列表为：\n"+self.format_list(group_id))
        elif instruction == "clr":
            self.init_clear(group_id)
            yield event.plain_result("已清空先攻列表")
        elif instruction == "del":
            if not player_name:
                player_name = user_name
            self.remove_by_name(player_name, group_id)
            yield event.plain_result(f"已删除角色{player_name}的先攻")

    # @filter.command("ri")
    async def roll_initiative(self , event: AstrMessageEvent, expr: str = None):

        group_id = event.get_group_id()
        user_id = event.get_sender_id()
        user_name = event.get_sender_name()

        if not expr:
            init_value = random.randint(1, 20)
            player_name = user_name
        elif expr[0] == "+":
            match = re.match(r"\+(\d+)", expr)
            init_value = random.randint(1, 20) + int(match.group(1))
            player_name = user_name
        elif expr[0] == "-":
            match = re.match(r"\-(\d+)", expr)
            init_value = random.randint(1, 20) - int(match.group(1))
            player_name = user_name
        else:
            match = re.match(r"(\d+)", expr)
            init_value = int(match.group(1))
            player_name = expr[match.end():]
            if not player_name:
                player_name = user_name

        item = self.InitiativeItem(player_name, init_value, user_id)
        self.remove_by_name(player_name, group_id)
        self.add_item(item, group_id)
        yield event.plain_result(f"已添加/更新{player_name}的先攻：{init_value}")
        async for result in self.initiative(event):
            yield result

    @filter.command("ed")
    async def end_current_round(self , event: AstrMessageEvent):
        group_id = event.get_group_id()
        current_item = init_list[group_id][current_index[group_id]]
        next_item = self.next_turn(group_id)
        if not next_item:
            yield event.plain_result("先攻列表为空，无法推进回合")
        else:
            yield event.plain_result(f"{current_item.name}的回合结束 → \n {next_item.name}的回合 (先攻: {next_item.init_value})")

    
    # ========================================================= #

    @filter.command("name")
    async def generate_name(self, event: AstrMessageEvent, language: str = "cn", num: int = 5, sex: str = None):
        names = generate_names(language=language, num=num, sex=sex)
        yield event.plain_result(get_output("generated_names", num = num, names=", ".join(names)))

    # ------------------ CoC角色生成 ------------------ #
    @filter.command("coc")
    async def generate_coc_character(self, event: AstrMessageEvent, x: int = 1):
        characters = [roll_character() for _ in range(x)]
        results = []
        for i, char in enumerate(characters):
            results.append(format_character(char, index=i+1))
        yield event.plain_result(get_output("character_list.coc", characters="\n\n".join(results)))

    # ------------------ DnD角色生成 ------------------ #
    @filter.command("dnd")
    async def generate_dnd_character(self, event: AstrMessageEvent, x: int = 1):
        characters = [roll_dnd_character() for _ in range(x)]
        results = []
        for i, char in enumerate(characters):
            results.append(format_dnd_character(char, index=i+1))
        yield event.plain_result(get_output("character_list.dnd", characters="\n\n".join(results)))
        
    # ======================== LOG相关 ============================= #
    @filter.command_group("log")
    async def log(event: AstrMessageEvent):
        pass


    @log.command("new")
    async def cmd_log_new(self, event: AstrMessageEvent):
        group = event.message_obj.group_id
        parts = event.message_str.strip().split()
        name = parts[2] if len(parts) >= 3 else None
        ok, info = await logger_core.new_session(group, name)
        return event.plain_result(info)


    @log.command("end")
    async def cmd_log_end(self, event: AstrMessageEvent):
        group = event.message_obj.group_id
        ok, info = await logger_core.end_session(group)
        return event.plain_result(info)


    @log.command("off")
    async def cmd_log_off(self, event: AstrMessageEvent):
        group = event.message_obj.group_id
        ok, info = await logger_core.pause_sessions(group)
        return event.plain_result(info)


    @log.command("on")
    async def cmd_log_on(self, event: AstrMessageEvent):
        group = event.message_obj.group_id
        parts = event.message_str.strip().split()
        name = parts[2] if len(parts) >= 3 else None
        ok, info = await logger_core.resume_session(group, name)
        return event.plain_result(info)


    @log.command("list")
    async def cmd_log_list(self, event: AstrMessageEvent):
        group = event.message_obj.group_id
        lines = await logger_core.list_sessions(group)
        return event.plain_result("\n".join(lines))


    @log.command("del")
    async def cmd_log_del(self, event: AstrMessageEvent):
        group = event.message_obj.group_id
        parts = event.message_str.strip().split()
        if len(parts) < 3:
            return event.plain_result(get_output("log.delete_error"))
        name = parts[2]
        ok, info = await logger_core.delete_session(group, name)
        return event.plain_result(info)


    @log.command("get")
    async def cmd_log_get(self, event: AstrMessageEvent):
        
        group = event.message_obj.group_id
        parts = event.message_str.strip().split()
        
        name = parts[2]
        grp = await logger_core.load_group(group)
        sec = grp.get(name)

        logger.info(f"{name}, {group}")

        if not sec:
            return event.plain_result(get_output("log.session_not_found", session_name=name))

        info = await logger_core.export_session(group, sec, name)
        
        return event.plain_result(info)


    @log.command("stat")
    async def cmd_log_stat(self, event: AstrMessageEvent):
        group = event.message_obj.group_id
        parts = event.message_str.strip().split()
        name = parts[2] if len(parts) >= 3 else None
        all_flag = len(parts) >= 4 and parts[3] == "--all"
        lines = await logger_core.stat_sessions(group, name, all_flag)
        return event.plain_result("\n".join(lines))
    # ======================== LOG相关 ============================= #
    
    # 注册指令 /dicehelp
    @filter.command("bothelp")
    async def help ( self , event: AstrMessageEvent):
        help_text = (
        "要让风铃带你们跑团吗？那要好好学习怎么跑团呀。"
        "基础掷骰教程：.dicehelp roll\n"
        "进阶掷骰表达式：.dicehelp expr\n"
        "人物卡管理: .dicehelp pc\n"
        "属性值管理：.dicehelp st\n"
        "记录管理：.dicehelp log\n"
        "其余杂项指令：.dicehelp coc\n"
        
        "DnD 相关: .dicehelp dnd\n"

        "其他规则\n"
        "`/rv 骰子数量 难度` - 进行吸血鬼规则掷骰判定\n"
        )

        yield event.plain_result(help_text)
        
    @command_group("dicehelp")
    async def dicehelp(self, event : AstrMessageEvent) :
        pass

    @dicehelp.command("roll")
    async def help_roll ( self , event: AstrMessageEvent):
        yield event.plain_result(get_output("help.dice"))
        
    @dicehelp.command("expr")
    async def help_expr ( self , event: AstrMessageEvent):
        yield event.plain_result(get_output("help.expr"))
        
    @dicehelp.command("pc")
    async def help_pc ( self , event: AstrMessageEvent):
        yield event.plain_result(get_output("help.pc"))
        
    @dicehelp.command("st")
    async def help_st ( self , event: AstrMessageEvent):
        yield event.plain_result(get_output("help.st"))

    @dicehelp.command("log")
    async def help_log ( self , event: AstrMessageEvent):
        yield event.plain_result(get_output("help.log"))
        
    @dicehelp.command("coc")
    async def help_coc ( self , event: AstrMessageEvent):
        yield event.plain_result(get_output("help.coc"))
        
    @dicehelp.command("dnd")
    async def help_dnd ( self , event: AstrMessageEvent):
        yield event.plain_result(get_output("help.dnd"))
        
    @filter.command("fireball")
    async def fireball_cmd(self, event: AstrMessageEvent, ring: int = 3):
        result = dice_mod.fireball(ring)
        yield event.plain_result(result)

    @filter.command("jrrp")
    async def roll_RP_cmd(self, event: AstrMessageEvent):
        user_id = event.get_sender_id()
        result = dice_mod.roll_RP(user_id)
        yield event.plain_result(result)

    @filter.command("setcoc")
    async def setcoc_cmd(self, event: AstrMessageEvent, command: str = " "):
        group_id = event.get_group_id()
        result = modify_coc_great_sf_rule_command(group_id, command)
        yield event.plain_result(result)

    
    # 识别所有信息
    @event_message_type(EventMessageType.GROUP_MESSAGE)
    async def identify_command(self, event: AstrMessageEvent): 

        message = event.message_obj.message_str
        
         # ------------------- 日志收集逻辑 -------------------
        group_id = event.message_obj.group_id
        
        if group_id:
            user_id = event.message_obj.sender.user_id
            nickname = getattr(event.message_obj.sender, "nickname", "")
            timestamp = int(event.message_obj.timestamp)
            components = getattr(event.message_obj, "message", [])

            # 调用功能性模块添加消息
            await logger_core.add_message(
                group_id=group_id,
                user_id=user_id,
                nickname=nickname,
                timestamp=timestamp,
                text=message,
                components=components
            )
        # ----------------------------------------------------
        
        target_user_id = str(event.get_sender_id())
        for comp in getattr(event.message_obj, "message", []):
            if isinstance(comp, Comp.At):
                target_user_id = str(comp.qq)
                break
                
        # 在保留完整日志后，把文本里的 CQ 码和 @ 残留抹掉，
        # 确保它们绝对不会掉进下面那个“诡异”的正则解析器里
        message = re.sub(r'\[CQ:at.*?\]', '', message)
        if '@' in message:
            message = message.split('@')[0]
        
        # logger.info(f"{message}, {target_user_id}")
        
        # yield event.plain_result(message)

        random.seed(int(time.time() * 1000))
        
        if not any(message.startswith(prefix) for prefix in self.wakeup_prefix):
            return
        
        message = re.sub(r'\s+', '', message[1:])

        m = re.match(r'^([a-z]+)', message, re.I)

        if not m:
            #raise ValueError('无法识别的指令格式!')
            return
        
        cmd  = m.group(1).lower() if m else ""
        expr = message[m.end():].strip()
        remark = None
        
        skill_value = ""
        dice_count = "1"
        
        if cmd[0:2] == "en":
            sv_match = re.search(r'\d+$', message)
            if sv_match:
                skill_value = sv_match.group()
                expr = message[2:len(message)-len(skill_value)]
                cmd = "en"
            else:
                skill_value = None
                expr = message[2:]
                cmd = "en"
                
        if cmd[0:2] == "ra":
            # --- 
            # ⬇️ V2: 适配 .ra[次数]#[b/p]... 的全新解析器 ⬇️
            # ---
            
            # 1. 设置所有变量的默认值
            roll_times = "1"
            cmd = "ra" # (稍后会变成 "rab" 或 "rap")
            dice_count = "0"
            skill_name = ""
            skill_value = None
            
            # 获取 "ra" 后面的所有内容
            expr = message[2:] # e.g., "10#p2侦查70" or "b2侦查70"

            # 2. 检查是否存在 "#" (新格式 vs 旧格式)
            hash_match = re.match(r'^(\d+)#(.+)', expr) # 匹配开头的 "10#..."
            
            if hash_match:
                # --- A. 新格式 (.ra10#...) ---
                roll_times = hash_match.group(1) # e.g., "10"
                expr = hash_match.group(2)       # e.g., "p2侦查70", "b侦查", "侦查70"
            else:
                # --- B. 旧格式 (.ra...) ---
                roll_times = "1"
                # expr 保持不变 (e.g., "b2侦查70", "侦查70")

            # 3. 此时, expr 已被统一为 "p2侦查70", "b侦查70", "侦查70" 等
            #    我们现在解析 b/p
            
            if expr.startswith('b'):
                cmd = "rab"
                expr = expr[1:] # 剥离 'b', 剩下 e.g., "2侦查70", "侦查"
            elif expr.startswith('p'):
                cmd = "rap"
                expr = expr[1:] # 剥离 'p'

            # 4. 如果是 b/p, 查找奖惩骰个数 (N)
            
            PureNumber = False
            
            if cmd != "ra": # (rab 或 rap)
                expr = expr.strip()
                PureNumber = False

                # --- 1. 处理显式分隔符 'c' (优先级最高) ---
                if 'c' in expr:
                    parts = expr.split('c', 1)
                    dice_count = parts[0].strip() or "1" # .rabc50 -> dice_count="1"
                    expr = parts[1].strip()
                    # 如果 c 后面是纯数字，标记为纯数字模式
                    if expr.isdigit():
                        skill_value = int(expr)
                        PureNumber = True
                        skill_name = "指定值"
                        expr = f"{str(skill_value)}"
                
                # --- 2. 智能拦截纯数字 (例如 .rab50) ---
                elif expr.isdigit():
                    dice_count = "1"
                    skill_value = int(expr)
                    PureNumber = True
                    skill_name = "指定值"
                    expr = f"{str(skill_value)}"
                    # expr 保持原样或重置，供后续统一处理
                
                # --- 3. 标准解析 (例如 .rab2侦查) ---
                else:
                    dice_match = re.match(r'^(\d+)', expr)
                    if dice_match:
                        matched_num = dice_match.group(1)
                        remaining = expr[len(matched_num):].strip()
                        
                        # 再次检查：如果是 .rab2 这种后面没东西的，其实也是纯数字
                        if not remaining:
                            dice_count = "1"
                            skill_value = int(matched_num)
                            PureNumber = True
                        else:
                            dice_count = matched_num
                            expr = remaining
                    else:
                        dice_count = "1"

            logger.info(f"{skill_name} : {skill_value}")

            # 5. 此时, expr 只剩下 "侦查70", "侦查", "70"
            #    我们用你原来的逻辑提取末尾的 skill_value
            
            if not PureNumber :
                sv_match = re.search(r'(\d+)$', expr)
                if sv_match:
                    skill_value = sv_match.group(1)        # e.g., "70"
                    skill_name = expr[:-len(skill_value)]  # e.g., "侦查"
                else:
                    skill_value = None                     # e.g., "侦查"
                    skill_name = expr                      # e.g., "侦查"

            # 6. 处理 ".ra70" 这种省略技能名的边缘情况
            if not skill_name and skill_value:
                skill_name = skill_value
                
            # 7. 清理 skill_name
            skill_name = skill_name.strip()
                
        elif cmd[0:2] == "rd":
            raw = message[2:].strip()
            dice_match = re.match(r'(\d+)', raw)
            
            if dice_match:
                dice_size = dice_match.group(1)
                expr = f"1d{dice_size}"
                remark = raw[(len(dice_size)):].strip()
            else:
                expr = "1d100"
                remark = raw.strip()
                
        elif cmd[0] == "r":
            content = message[1:].strip()
            if not content:
                expr = "1d100" # 默认掷骰
                remark = ""
            else:
                match = re.match(r'([\d#+\-*xXdkvbpBP]+)', content, re.IGNORECASE)
            
                if match:
                    expr = match.group(1)
                    remark = content[match.end():].strip()
                else:
                    expr = "1d100" # 默认掷骰
                    remark = content.strip()
                
        # result_message = (f"m={m},message={message},cmd={cmd},expr={expr}.")
        # yield event.plain_result(result_message)

        if cmd == "r":
            await self.handle_roll_dice(event, expr, remark)
        elif cmd == "rd":
            await self.handle_roll_dice(event, expr, remark)
        elif cmd == "rh":
            async for result in self.roll_hidden(event) :
                yield result
        elif cmd == "rab":
            await self.roll_attribute_bonus(event, dice_count, expr, skill_value, roll_times, target_user_id)
        elif cmd == "rap":
            await self.roll_attribute_penalty(event, dice_count, expr, skill_value, roll_times, target_user_id)
        elif cmd == "ra":
            await self.roll_attribute(event, expr, skill_value, roll_times, target_user_id)
        elif cmd == "en":
            await self.pc_grow_up(event, expr, skill_value)
        elif cmd == "sc":
            async for result in self.pc_san_check(event, expr) :
                yield result
        elif cmd == "li":
            async for result in self.pc_long_term_insanity(event):
                yield result
        elif cmd == "ti":
            async for result in self.pc_temporary_insanity(event):
                yield result
        elif cmd == "ri":
            async for result in self.roll_initiative(event, expr):
                yield result