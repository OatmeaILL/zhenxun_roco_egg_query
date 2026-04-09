from typing import Optional
import httpx

from nonebot.plugin import PluginMetadata
from zhenxun.configs.utils import Command, PluginExtraData
from zhenxun.services.log import logger
from zhenxun.ui.models import ImageCell, TextCell
from zhenxun.utils.message import MessageUtils
from zhenxun.utils.rules import ensure_group
from zhenxun import ui
from nonebot_plugin_alconna import Alconna, Args, on_alconna
from nonebot_plugin_uninfo import Uninfo

__plugin_meta__ = PluginMetadata(
    name="洛克王国世界孵蛋查询",
    description="根据宠物蛋径蛋重查询可能孵出的宠物",
    usage="""
    发送"查蛋"查看使用说明
    发送"查蛋 蛋径 蛋重"进行查询
    示例：
      查蛋 0.35 7.64
      查蛋 0.35m 7.64kg
    """.strip(),
    extra=PluginExtraData(
        author="OatmeaILL",
        version="1.0",
        commands=[
            Command(command="查蛋"),
            Command(command="查蛋 蛋径 蛋重"),
        ],
    ).to_dict(),
)

# ！！！！API配置 注意 此API为公开接口，请勿滥用！！！请勿短时间大量请求！！！
#同时 此API有可能随时失效或数据格式更新！可以自己尝试修改。此API获取自B站的洛克王国世界WIKI
API_URL = "https://roco.gptvip.chat/api/magic-egg-lookup"
API_TIMEOUT = 10.0


def parse_height_input(input_str: str) -> Optional[float]:
    """解析蛋径输入"""
    if not input_str:
        return None
    input_str = input_str.strip().lower()
    if input_str.endswith("m"):
        input_str = input_str[:-1]
    try:
        return float(input_str)
    except ValueError:
        return None


def parse_weight_input(input_str: str) -> Optional[float]:
    """解析蛋重输入"""
    if not input_str:
        return None
    input_str = input_str.strip().lower()
    if input_str.endswith("kg"):
        input_str = input_str[:-2]
    try:
        return float(input_str)
    except ValueError:
        return None


async def query_pet_api(height_m: float, weight_kg: float) -> dict:
    """调用API查询宠物"""
    try:
        async with httpx.AsyncClient(timeout=API_TIMEOUT) as client:
            response = await client.get(
                API_URL,
                params={"height_m": height_m, "weight_kg": weight_kg}
            )
            response.raise_for_status()
            return response.json()
    except httpx.TimeoutException:
        raise Exception("API请求超时，请稍后重试")
    except httpx.HTTPStatusError as e:
        raise Exception(f"API请求失败: {e.response.status_code}")
    except Exception as e:
        raise Exception(f"请求异常: {str(e)}")


# 帮助匹配器没参数走这里
help_matcher = on_alconna(
    Alconna("查蛋"),
    priority=6,
    block=True,
    rule=ensure_group,
)


@help_matcher.handle()
async def _(session: Uninfo):
    """无参数时显示帮助"""
    help_text = """
洛克查蛋插件使用说明

用法：查蛋 <蛋径> <蛋重>

蛋径：支持米(m)，如 0.35m 或 0.35
蛋重：支持千克(kg)，如 7.64kg 或 7.64

使用例：
  • 查蛋 0.35 7.64
  • 查蛋 0.35m 7.64kg
注意:查询结果的置信度不代表宠物的准确概率，请自行判断。
""".strip()
    await MessageUtils.build_message(help_text).finish()


# 查询匹配器 有参数走这里 
search_matcher = on_alconna(
    Alconna("查蛋", Args["height", str], Args["weight", str]),
    priority=5,
    block=True,
    rule=ensure_group,
)


@search_matcher.handle()
async def _(session: Uninfo, height: str, weight: str):
    """处理洛克查蛋命令"""
    await handle_pet_query(height, weight)

async def handle_pet_query(height: str, weight: str):
    """处理宠物查询"""
    # 解析输入
    height_value = parse_height_input(height)
    weight_value = parse_weight_input(weight)

    # 检查解析结果
    if height_value is None:
        await MessageUtils.build_message(
            f"无法解析蛋径：{height}\n"
            "请输入有效的数值（例如 0.35或0.35m）"
        ).finish()
        return

    if weight_value is None:
        await MessageUtils.build_message(
            f"无法解析蛋重：{weight}\n"
            "请输入有效的数值（如 7.64 或 7.64kg）"
        ).finish()
        return

    # 调用API
    try:
        result = await query_pet_api(height_value, weight_value)
    except Exception as e:
        await MessageUtils.build_message(f"查询失败：{str(e)}").finish()
        return

    # 检查API返回
    if not result.get("ok", False):
        await MessageUtils.build_message("API返回错误，请稍后重试").finish()
        return

    matched = result.get("matched", [])
    matched_count = result.get("matched_count", 0)

    if not matched:
        await MessageUtils.build_message(
            f"暂无匹配结果\n"
            f"蛋径 {height_value}m，蛋重 {weight_value}kg\n"
            "请尝试调整参数"
        ).finish()
        return

#调用真寻的表格工具输出结果图片
    # 构建表格数据
    column_name = ["序号", "宠物头像", "名称", "匹配度", "置信度", "属性", "进化链"]
    data_list = []

    # 置信度颜色
    color_map = {
        "high": "#52C41A",    # 绿
        "medium": "#FAAD14",  # 橙
        "low": "#FF4D4F",     # 红
    }

    for idx, pet in enumerate(matched):
        # 获取类型: pet 或 egg
        source_kind = pet.get("source_kind", "pet")
        display_name = pet.get("display_name", "未知")
        fit_score = pet.get("fit_score", 0)
        confidence = pet.get("confidence", "low")
        confidence_text = pet.get("confidence_text", "低")

        # 蛋类型标记
        is_egg = source_kind == "egg"

        # 获取宠物详情 (pet_preview可能为None，egg类型为null)
        pet_preview = pet.get("pet_preview") or {}

        if is_egg:
            # egg类型：没有详细直接走   
            type_name = "蛋"
            avatar_url = ""
            evolution_text = "🥚 蛋形态"
        else:
            # pet类型：有完整数据的话就走else
            type_name = pet_preview.get("type_name", "未知") if pet_preview else "未知"
            avatar_url = pet_preview.get("avatar_url", "") if pet_preview else ""
            evolution_chain = pet_preview.get("evolution_chain", []) if pet_preview else []
            if evolution_chain:
                evolution_text = " → ".join(evolution_chain)
            else:
                evolution_text = display_name

        # 构建头像单元格
        if avatar_url:
            avatar_cell = ImageCell(src=avatar_url, shape="circle")
        else:
            # 蛋类型用🥚emoji代替 如果这里表格渲染不出来emoji再手动换成文字
            avatar_cell = TextCell(content="🥚")

        # 置信度颜色
        confidence_color = color_map.get(confidence.lower(), "#999999")

        # 名称根据类型添加标记
        if is_egg:
            display_name = f"🥚{display_name}"

        # 构建行数据
        row = [
            TextCell(content=f"{idx + 1}"),  # 排名
            avatar_cell,                          # 头像
            TextCell(content=display_name, color="#1890FF" if not is_egg else "#FAAD14"),  # 名称
            TextCell(content=f"{fit_score:.1f}%"),  # 匹配度
            TextCell(content=confidence_text, color=confidence_color),  # 置信度
            TextCell(content=type_name),          # 属性
            TextCell(content=evolution_text),     # 进化链
        ]
        data_list.append(row)

    # 构建图片表格
    title = "洛克查蛋结果"
    tip = f"蛋径 {height_value}m | 蛋重 {weight_value}kg | 共 {matched_count} 个匹配"

    table = ui.table(title, tip)
    table.set_headers(column_name).add_rows(data_list)

    result_image = await ui.render(table)
    await MessageUtils.build_message(result_image).finish()
