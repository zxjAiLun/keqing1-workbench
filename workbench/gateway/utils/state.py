from .decoder import Meld


class State:
    def __init__(self, name: str = 'NoName', room: str = '0_0'):
        self.name: str = name
        lobby_part, game_type = room.split('_', 1)
        self.lobby_id: int | None = None
        if lobby_part.upper().startswith('L') and lobby_part[1:].isdigit():
            self.lobby_id = int(lobby_part[1:])
            lobby_part = str(self.lobby_id)
        self.room: str = f'{lobby_part},{game_type}'
        # 手牌(天鳳インデックス)
        self.hand: list[int] = []
        # 立直をかけているか
        self.in_riichi: bool = False
        # 壁牌の枚数
        self.live_wall: int | None = None
        # 副露のリスト
        self.melds: list[Meld] = []
        # 待ち
        self.wait: set[int] = set()
        # 天凤分配的实际座位号（从 UN 消息解析）
        self.seat: int = 0
        # 四位玩家名（从 UN 消息解析），供 start_game 的 names 字段使用
        # （原生 libriichi Bot 要求 names 为长度 4 的数组）
        self.names: list[str] = ["", "", "", ""]
        # 已发出荣和/自摸请求，等待天凤确认，忽略后续事件
        self.hora_pending: bool = False
