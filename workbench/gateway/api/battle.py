from __future__ import annotations

import asyncio
import os
import random
import sys
import gc
from typing import Any, Dict, List, Optional

from fastapi import FastAPI, HTTPException, APIRouter, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

sys.path.insert(
    0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
)

from gateway.battle import BattleConfig, BattleManager, BattleRoom, get_manager
from gateway.bot_driver import BotDriver
from gateway.rating import RatingStore, battle_player_identity
from inference.bot_registry import SUPPORTED_BOT_NAMES, create_runtime_bot
from mahjong_env.legal_actions import enumerate_legal_actions

app = FastAPI()
manager = get_manager()

project_root = os.path.dirname(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
)
tiles_path = os.path.join(project_root, "tiles", "riichi-mahjong-tiles", "Regular")
if os.path.exists(tiles_path):
    app.mount("/tiles", StaticFiles(directory=tiles_path), name="tiles")

router = APIRouter(prefix="/api/battle")

# NOTE: router 在文件末尾 include 到 app（需在所有路由定义之后）

bots: Dict[int, Any] = {}
BOT_TYPE = os.environ.get("BOT_TYPE", "mortal")
SUPPORTED_BOT_MODELS = set(SUPPORTED_BOT_NAMES)


def _cleanup_bot(bot: Any) -> None:
    """释放 bot 占用的模型资源"""
    if hasattr(bot, "model") and bot.model is not None:
        del bot.model
    if hasattr(bot, "optimizer") and bot.optimizer is not None:
        del bot.optimizer


def _cleanup_all_bots() -> None:
    """清理所有 bot 并强制 GC"""
    global bots
    for bot in bots.values():
        _cleanup_bot(bot)
    bots.clear()
    gc.collect()


# 当前选用的 bot 模型名称（由 start_battle 设置）
current_bot_model: str = "mortal"


def _build_bot_names(bot_model: str, count: int) -> List[str]:
    return [f"{bot_model}-{i + 1}号机" for i in range(count)]


def get_or_create_bot(bot_id: int) -> Any:
    if bot_id not in bots:
        bot_name = current_bot_model
        if bot_name not in SUPPORTED_BOT_MODELS:
            raise ValueError(f"Unsupported bot model: {bot_name}")
        print(f"[Bot] Creating {bot_name} bot for seat {bot_id}")
        bots[bot_id] = create_runtime_bot(
            bot_name=bot_name,
            player_id=bot_id,
            project_root=project_root,
            device="cuda",
            verbose=False,
        )
    return bots[bot_id]


bot_driver = BotDriver(manager, get_or_create_bot)
_advance_lock = asyncio.Lock()
rating_store = RatingStore()


def _has_pending_post_call_discard(room: BattleRoom, actor: int) -> bool:
    """chi/pon 后尚未打牌的阶段：没有 last_tsumo，但手牌张数已处于“待打牌”状态。"""
    if room.phase != "playing":
        return False
    if room.state.actor_to_move != actor:
        return False
    if room.state.last_tsumo[actor] is not None:
        return False
    if room.state.last_discard or room.state.last_kakan:
        return False
    if room.pending_rinshan:
        return False
    hand_size = sum(room.state.players[actor].hand.values())
    return hand_size % 3 == 2


def _prepare_player_state(room: BattleRoom, player_id: int) -> Dict[str, Any]:
    """返回玩家可见状态；若轮到该玩家摸牌，先做幂等补摸。"""
    if (
        room.phase == "playing"
        and room.state.actor_to_move == player_id
        and room.state.last_tsumo[player_id] is None
        and room.human_player_id == player_id
        and room.remaining_wall() >= 1
        and not room.state.last_discard
        and not room.state.last_kakan
    ):
        if not _has_pending_post_call_discard(room, player_id):
            manager.draw(room, player_id)
    return manager.get_state_for_player(room, player_id=player_id)


def _finish_hand_if_game_ended(room: BattleRoom) -> None:
    if room.phase == "hand_result" and manager.is_game_ended(room):
        manager.finalize_game(room)


def _record_rating_if_finished(room: BattleRoom) -> list[dict] | None:
    if room.phase != "ended" or room.rating_recorded:
        return None
    players = [
        battle_player_identity(
            player.get("id"),
            player.get("name"),
            seat,
        )
        for seat, player in enumerate(room.config.players[:4])
    ]
    if len(players) != 4:
        return None
    result = rating_store.record_result(
        players=players,
        scores=room.state.scores,
        initial_oya=0,
    )
    room.rating_recorded = True
    manager.finalize_game(room, rating_updates=result)
    return result


def _prepare_battle_state(room: BattleRoom, player_id: int) -> Dict[str, Any]:
    state = _prepare_player_state(room, player_id)
    _finish_hand_if_game_ended(room)
    _record_rating_if_finished(room)
    if room.phase != state.get("phase") or room.game_result:
        state = manager.get_state_for_player(room, player_id=player_id)
    return state


class PlayerInfo(BaseModel):
    id: str
    name: str
    type: str


class StartBattleRequest(BaseModel):
    player_name: str = "Player"
    bot_count: int = 3
    seed: Optional[int] = None
    bot_model: str = "mortal"
    game_length: str = "hanchan"


class Start4BotRequest(BaseModel):
    seed: Optional[int] = None
    bot_model: str = "mortal"
    game_length: str = "hanchan"


class StartBattleResponse(BaseModel):
    game_id: str
    state: Dict[str, Any]


class GetStateResponse(BaseModel):
    state: Dict[str, Any]


class ActionRequest(BaseModel):
    game_id: str
    action: Dict[str, Any]


class ActionResponse(BaseModel):
    success: bool
    state: Dict[str, Any]
    bot_action: Optional[Dict[str, Any]] = None
    rating_updates: Optional[List[Dict[str, Any]]] = None


@router.post("/start", response_model=StartBattleResponse)
async def start_battle(req: StartBattleRequest) -> StartBattleResponse:
    global current_bot_model
    if req.bot_model not in SUPPORTED_BOT_MODELS:
        raise HTTPException(status_code=400, detail=f"Unsupported bot_model: {req.bot_model}")
    current_bot_model = req.bot_model
    _cleanup_all_bots()

    human_seat = random.randrange(4)
    players: List[PlayerInfo] = []
    bot_names = _build_bot_names(req.bot_model, req.bot_count)
    bot_index = 0
    for seat in range(4):
        if seat == human_seat:
            players.append(PlayerInfo(id="human", name=req.player_name, type="human"))
        else:
            players.append(
                PlayerInfo(
                    id=f"bot_{bot_index}",
                    name=bot_names[bot_index],
                    type="bot",
                )
            )
            bot_index += 1

    game_length = req.game_length if req.game_length in {"tonpu", "hanchan"} else "hanchan"
    config = BattleConfig(
        player_count=4,
        players=[p.model_dump() for p in players],
        game_length=game_length,
        allow_west_round=game_length != "tonpu",
    )

    room = manager.create_room(config, seed=req.seed)
    room.human_player_id = human_seat

    manager.start_kyoku(room, seed=req.seed)
    room.bot_event_cursor = {}

    for bot_id in range(4):
        if bot_id != human_seat:
            get_or_create_bot(bot_id).reset()
    bot_driver.sync_all_bots(room)

    state = _prepare_battle_state(room, player_id=human_seat)
    return StartBattleResponse(game_id=room.game_id, state=state)


@router.post("/close/{game_id}")
async def close_battle(game_id: str) -> Dict[str, bool]:
    """主动关闭游戏房间，释放内存"""
    manager.close_room(game_id)
    return {"success": True}


@router.post("/start_4bot")
async def start_4bot(req: Start4BotRequest) -> Dict[str, Any]:
    """4 Bot对战模式，自动运行直到游戏结束"""
    global current_bot_model
    if req.bot_model not in SUPPORTED_BOT_MODELS:
        raise HTTPException(
            status_code=400, detail=f"Unsupported bot_model: {req.bot_model}"
        )
    current_bot_model = req.bot_model
    _cleanup_all_bots()

    bot_names = _build_bot_names(req.bot_model, 4)
    players: List[PlayerInfo] = [
        PlayerInfo(id=f"bot_{i}", name=bot_names[i], type="bot")
        for i in range(4)
    ]

    game_length = req.game_length if req.game_length in {"tonpu", "hanchan"} else "hanchan"
    config = BattleConfig(
        player_count=4,
        players=[p.model_dump() for p in players],
        game_length=game_length,
        allow_west_round=game_length != "tonpu",
    )
    room = manager.create_room(config, seed=req.seed)
    room.human_player_id = -1  # 表示无人类玩家
    room.bot_event_cursor = {}

    for bot_id in range(4):
        get_or_create_bot(bot_id).reset()

    import asyncio

    asyncio.create_task(bot_driver.run_4bot_game(room.game_id, req.seed))

    state = _prepare_battle_state(room, player_id=0)
    return {"game_id": room.game_id, "state": state}


@router.get("/state/{game_id}", response_model=GetStateResponse)
async def get_state(game_id: str, player_id: int = 0) -> GetStateResponse:
    room = manager.get_room(game_id)
    if not room:
        raise HTTPException(status_code=404, detail="Game not found")

    state = _prepare_battle_state(room, player_id=player_id)
    return GetStateResponse(state=state)


@router.get("/export_mjai/{game_id}")
async def export_mjai(game_id: str):
    """导出mjai格式JSONL"""
    room = manager.get_room(game_id)
    if not room:
        raise HTTPException(status_code=404, detail="Game not found")

    import json

    content = "\n".join(json.dumps(e, ensure_ascii=False) for e in room.events)
    return Response(content=content, media_type="application/jsonl")


@router.get("/export_tenhou6/{game_id}")
async def export_tenhou6(game_id: str):
    """导出tenhou6格式JSON"""
    room = manager.get_room(game_id)
    if not room:
        raise HTTPException(status_code=404, detail="Game not found")

    import sys

    sys.path.insert(
        0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    )
    from tools.mjai_jsonl_to_tenhou6 import convert_mjai_jsonl_to_tenhou6

    tenhou_data = convert_mjai_jsonl_to_tenhou6(room.events)
    return tenhou_data


class QuitResponse(BaseModel):
    success: bool


@router.post("/heartbeat/{game_id}")
async def heartbeat(game_id: str) -> Dict[str, Any]:
    """前端定期发送心跳，维持在线状态。"""
    import time

    room = manager.get_room(game_id)
    if not room:
        raise HTTPException(status_code=404, detail="Game not found")
    room.last_heartbeat = time.time()
    room.disconnected = False
    return {"ok": True, "phase": room.phase}


@router.get("/reconnect/{game_id}")
async def reconnect(game_id: str, player_id: int = 0) -> Dict[str, Any]:
    """断线重连：返回当前完整游戏状态。"""
    import time

    room = manager.get_room(game_id)
    if not room:
        raise HTTPException(status_code=404, detail="Game not found")
    room.last_heartbeat = time.time()
    room.disconnected = False
    state = _prepare_battle_state(room, player_id=player_id)
    return {"reconnected": True, "state": state}


@router.post("/quit/{game_id}", response_model=QuitResponse)
async def quit_battle(game_id: str) -> QuitResponse:
    """人类玩家主动退出，将其座位移交给 bot 接管，游戏继续（4-bot 模式）。"""
    room = manager.get_room(game_id)
    if not room:
        raise HTTPException(status_code=404, detail="Game not found")
    human_id = room.human_player_id
    room.human_player_id = -1  # 标记为全 bot 模式
    room.disconnected = True
    # 补充第 human_id 号 bot
    get_or_create_bot(human_id).reset()
    # 后台继续跑完对局
    if room.phase == "playing":
        asyncio.create_task(bot_driver.run_4bot_game_from_current(room))
    return QuitResponse(success=True)


@router.post("/action", response_model=ActionResponse)
async def do_action(req: ActionRequest) -> ActionResponse:
    room = manager.get_room(req.game_id)
    if not room:
        raise HTTPException(status_code=404, detail="Game not found")

    action = req.action
    action_type = action.get("type")
    actor = action.get("actor", 0)

    # 委托给 BattleManager 处理游戏逻辑
    error = manager.process_human_action(room, actor, action)
    if error:
        raise HTTPException(status_code=400, detail=error)

    human_player_id = room.human_player_id
    state = _prepare_battle_state(room, player_id=human_player_id if human_player_id >= 0 else 0)
    rating_updates = (state.get("game_result") or {}).get("rating_updates") or None
    return ActionResponse(success=True, state=state, bot_action=None, rating_updates=rating_updates)


@router.post("/advance/{game_id}", response_model=ActionResponse)
async def advance_bot(game_id: str) -> ActionResponse:
    """推进一个 bot 步骤，前端轮询调用以依次显示每家出牌"""
    room = manager.get_room(game_id)
    if not room:
        raise HTTPException(status_code=404, detail="Game not found")

    human_player_id = room.human_player_id
    last_bot_action: Optional[Dict] = None

    async with _advance_lock:
        if room.phase == "playing":
            next_actor = room.state.actor_to_move
            if next_actor is not None and next_actor != human_player_id:
                is_discard_response = bool(
                    room.state.last_discard
                    and room.state.last_discard.get("actor") != next_actor
                )
                is_kakan_response = bool(
                    room.state.last_kakan
                    and room.state.last_kakan.get("actor") != next_actor
                )
                has_pending_post_call_discard = _has_pending_post_call_discard(
                    room, next_actor
                )
                needs_draw_stage = (
                    not is_discard_response
                    and not is_kakan_response
                    and not has_pending_post_call_discard
                    and room.replay_draw_actor != next_actor
                    and room.state.last_tsumo[next_actor] is None
                )

                if needs_draw_stage:
                    draw_tile = manager.prepare_turn(room, next_actor)
                    if room.phase == "playing" and draw_tile is None and room.replay_draw_actor != next_actor:
                        last_bot_action = await bot_driver.take_turn(room, next_actor)
                elif (
                    room.replay_draw_actor == next_actor
                    or has_pending_post_call_discard
                    or is_discard_response
                    or is_kakan_response
                ):
                    last_bot_action = await bot_driver.take_turn(room, next_actor)

    state = _prepare_battle_state(room, player_id=human_player_id if human_player_id >= 0 else 0)
    rating_updates = (state.get("game_result") or {}).get("rating_updates") or None
    return ActionResponse(success=True, state=state, bot_action=last_bot_action, rating_updates=rating_updates)


@router.post("/next_kyoku/{game_id}", response_model=ActionResponse)
async def next_kyoku(game_id: str) -> ActionResponse:
    room = manager.get_room(game_id)
    if not room:
        raise HTTPException(status_code=404, detail="Game not found")
    if room.phase != "hand_result":
        raise HTTPException(status_code=400, detail="Current game is not waiting for next kyoku")
    if manager.is_game_ended(room):
        manager.finalize_game(room)
        _record_rating_if_finished(room)
        raise HTTPException(status_code=400, detail="Game has ended")
    if not manager.next_kyoku(room):
        manager.finalize_game(room)
        _record_rating_if_finished(room)
        raise HTTPException(status_code=400, detail="Game has ended")

    manager.start_kyoku(room, seed=None)
    room.bot_event_cursor = {}
    bot_driver.sync_all_bots(room)

    player_id = room.human_player_id if room.human_player_id >= 0 else 0
    state = _prepare_battle_state(room, player_id=player_id)
    return ActionResponse(success=True, state=state, bot_action=None, rating_updates=None)


@router.get("/ratings")
async def list_ratings() -> Dict[str, Any]:
    return {"profiles": rating_store.list_profiles()}


@router.get("/rating/{player_id}")
async def get_rating(player_id: str, display_name: str | None = None) -> Dict[str, Any]:
    return {"profile": rating_store.get_profile(player_id, display_name=display_name)}


app.include_router(router)


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        "gateway.api.battle:app",
        host="0.0.0.0",
        port=8000,
    )
