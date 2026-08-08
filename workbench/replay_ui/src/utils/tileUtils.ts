// src/replay_ui/src/utils/tileUtils.ts
// 牌图 SVG 文件名映射（对应 tiles/riichi-mahjong-tiles/Regular/）
export const TILE_SVG_NAME: Record<string, string> = {
  // 数牌万子
  '1m': 'Man1', '2m': 'Man2', '3m': 'Man3', '4m': 'Man4', '5m': 'Man5',
  '6m': 'Man6', '7m': 'Man7', '8m': 'Man8', '9m': 'Man9',
  // 数牌饼子
  '1p': 'Pin1', '2p': 'Pin2', '3p': 'Pin3', '4p': 'Pin4', '5p': 'Pin5',
  '6p': 'Pin6', '7p': 'Pin7', '8p': 'Pin8', '9p': 'Pin9',
  // 数牌索子
  '1s': 'Sou1', '2s': 'Sou2', '3s': 'Sou3', '4s': 'Sou4', '5s': 'Sou5',
  '6s': 'Sou6', '7s': 'Sou7', '8s': 'Sou8', '9s': 'Sou9',
  // 赤宝牌
  '5mr': 'Man5-Dora', '5pr': 'Pin5-Dora', '5sr': 'Sou5-Dora',
  // 字牌
  'E': 'Ton', 'S': 'Nan', 'W': 'Shaa', 'N': 'Pei',
  'P': 'Haku', 'F': 'Hatsu', 'C': 'Chun',
};

// 排序权重：m(0-8) p(9-17) s(18-26) 字(27-33)
export const TILE_ORDER: Record<string, number> = {
  '1m': 0, '2m': 1, '3m': 2, '4m': 3, '5m': 4, '5mr': 4, '6m': 5, '7m': 6, '8m': 7, '9m': 8,
  '1p': 9, '2p': 10, '3p': 11, '4p': 12, '5p': 13, '5pr': 13, '6p': 14, '7p': 15, '8p': 16, '9p': 17,
  '1s': 18, '2s': 19, '3s': 20, '4s': 21, '5s': 22, '5sr': 22, '6s': 23, '7s': 24, '8s': 25, '9s': 26,
  'E': 27, 'S': 28, 'W': 29, 'N': 30, 'P': 31, 'F': 32, 'C': 33,
};

export const TILE_DISPLAY_NAMES: Record<string, string> = {
  '1m': '一萬', '2m': '二萬', '3m': '三萬', '4m': '四萬', '5m': '五萬',
  '6m': '六萬', '7m': '七萬', '8m': '八萬', '9m': '九萬',
  '1p': '一筒', '2p': '二筒', '3p': '三筒', '4p': '四筒', '5p': '五筒',
  '6p': '六筒', '7p': '七筒', '8p': '八筒', '9p': '九筒',
  '1s': '一索', '2s': '二索', '3s': '三索', '4s': '四索', '5s': '五索',
  '6s': '六索', '7s': '七索', '8s': '八索', '9s': '九索',
  '5mr': '赤五萬', '5pr': '赤五筒', '5sr': '赤五索',
  'E': '東', 'S': '南', 'W': '西', 'N': '北',
  'P': '白', 'F': '發', 'C': '中',
};

export function sortHand(hand: string[], tsumoPai?: string | null): string[] {
  if (!tsumoPai) {
    return [...hand].sort((a, b) => (TILE_ORDER[a] ?? 99) - (TILE_ORDER[b] ?? 99));
  }

  let removedTsumo = false;
  const others = hand.filter((tile) => {
    if (!removedTsumo && tile === tsumoPai) {
      removedTsumo = true;
      return false;
    }
    return true;
  });
  const sorted = [...others].sort((a, b) => (TILE_ORDER[a] ?? 99) - (TILE_ORDER[b] ?? 99));
  if (removedTsumo) {
    sorted.push(tsumoPai);
  }
  return sorted;
}

export function tileSvgUrl(tile: string): string {
  const name = TILE_SVG_NAME[tile] ?? 'Blank';
  return `/tiles/${name}.svg`;
}

export function tileDisplayName(tile: string): string {
  return TILE_DISPLAY_NAMES[tile] ?? tile;
}

// 获取牌的分数颜色（用于特殊牌高亮）
export function tileHighlightColor(tile: string): string | null {
  if (tile === '5mr') return '#c0392b';
  if (tile === '5pr') return '#c0392b';
  if (tile === '5sr') return '#c0392b';
  return null;
}

// 动作类型标签
export function actionLabel(action: { type: string; pai?: string; tsumogiri?: boolean; consumed?: string[] }): string {
  switch (action.type) {
    case 'dahai':
      return action.tsumogiri ? `摸切 ${action.pai}` : `打 ${action.pai}`;
    case 'reach':
      return action.pai ? `${action.pai} 立直` : '立直';
    case 'reach_accepted':
      return '立直接受';
    case 'chi': {
      if (action.consumed && action.consumed.length >= 2) {
        const sorted = [...action.consumed].sort((a, b) => (TILE_ORDER[a] ?? 99) - (TILE_ORDER[b] ?? 99));
        return `吃 ${sorted.join('')}+${action.pai}`;
      }
      return `吃 ${action.pai}`;
    }
    case 'pon':
      return `碰 ${action.pai}`;
    case 'daiminkan':
      return `大明杠 ${action.pai}`;
    case 'ankan':
      return `暗杠 ${action.pai}`;
    case 'kakan':
      return `加杠 ${action.pai}`;
    case 'hora':
      return '和牌';
    case 'ryukyoku':
      return '流局';
    case 'none':
      return '过';
    default:
      return action.type;
  }
}

type ComparableAction = {
  type: string;
  actor?: number;
  pai?: string;
  target?: number;
  consumed?: string[];
  tsumogiri?: boolean;
};

function normalizeReplayActionType(type: string | undefined): string {
  if (type === "pass") return "none";
  return type ?? "";
}

export function normalizeTileKeepAka(tile: string | null | undefined): string {
  if (!tile) return '';
  if (tile === '5mr' || tile === '5pr' || tile === '5sr') return tile;
  return tile.endsWith('r') ? tile.slice(0, -1) : tile;
}

export function normalizeTileFamily(tile: string | null | undefined): string {
  return normalizeTileKeepAka(tile).replace(/r$/, '');
}

function normalizedConsumedKey(consumed: string[] | undefined): string {
  return [...(consumed ?? [])]
    .map((tile) => normalizeTileFamily(tile))
    .sort()
    .join('|');
}

export function actionComparableKey(action: ComparableAction | null | undefined): string {
  if (!action) return '';
  const type = normalizeReplayActionType(action.type);
  if (type === 'none' || type === 'ryukyoku') {
    return JSON.stringify({ type });
  }
  if (type === 'reach') {
    return JSON.stringify({ type, pai: action.pai ? normalizeTileKeepAka(action.pai) : null });
  }
  if (type === 'dahai') {
    return JSON.stringify({
      type,
      pai: normalizeTileKeepAka(action.pai),
    });
  }
  if (type === 'hora') {
    return JSON.stringify({
      type,
      target: action.target ?? null,
      pai: action.pai ? normalizeTileKeepAka(action.pai) : null,
    });
  }
  if (type === 'chi' || type === 'pon' || type === 'daiminkan' || type === 'ankan' || type === 'kakan') {
    return JSON.stringify({
      type,
      target: action.target ?? null,
      pai: action.pai ? normalizeTileFamily(action.pai) : null,
      consumed: normalizedConsumedKey(action.consumed),
    });
  }
  return JSON.stringify({
    type,
    pai: normalizeTileKeepAka(action.pai),
    target: action.target ?? null,
    consumed: normalizedConsumedKey(action.consumed),
  });
}

export function sameReplayAction(
  a: ComparableAction | null | undefined,
  b: ComparableAction | null | undefined,
): boolean {
  if (!a || !b) return false;
  const typeA = normalizeReplayActionType(a.type);
  const typeB = normalizeReplayActionType(b.type);
  if (typeA !== typeB) return false;

  if (typeA === 'none' || typeA === 'ryukyoku') {
    return true;
  }
  if (typeA === 'reach') {
    if (!a.pai && !b.pai) return true;
    return Boolean(a.pai && b.pai && normalizeTileKeepAka(a.pai) === normalizeTileKeepAka(b.pai));
  }

  if (typeA === 'dahai') {
    return normalizeTileKeepAka(a.pai) === normalizeTileKeepAka(b.pai);
  }

  if (typeA === 'hora') {
    const paiA = a.pai ? normalizeTileKeepAka(a.pai) : null;
    const paiB = b.pai ? normalizeTileKeepAka(b.pai) : null;
    return (
      (a.target ?? null) === (b.target ?? null) &&
      (paiA === null || paiB === null || paiA === paiB)
    );
  }

  if (typeA === 'chi' || typeA === 'pon' || typeA === 'daiminkan' || typeA === 'ankan' || typeA === 'kakan') {
    return (
      (a.target ?? null) === (b.target ?? null) &&
      (!a.pai || !b.pai || normalizeTileFamily(a.pai) === normalizeTileFamily(b.pai)) &&
      normalizedConsumedKey(a.consumed) === normalizedConsumedKey(b.consumed)
    );
  }

  return actionComparableKey(a) === actionComparableKey(b);
}

type ReplayDecisionLike = {
  is_obs?: boolean;
  comparison_exempt?: string | null;
  actor_to_move?: number | null;
  chosen?: ComparableAction | null;
  gt_action?: ComparableAction | null;
  candidates?: Array<{ action?: ComparableAction | null }>;
  teacher_review?: TeacherReviewLike | null;
  teacher_reviews?: TeacherReviewLike[];
};

type TeacherReviewLike = {
  model?: string | null;
  actual_action?: ComparableAction | null;
  expected_action?: ComparableAction | null;
  is_equal?: boolean | null;
  q_loss?: number | null;
  top1?: { action?: ComparableAction | null } | null;
};

function actionBelongsToPlayer(action: ComparableAction | null | undefined, playerId: number): boolean {
  return action?.actor === playerId;
}

export function isReplayPlayerDecision(entry: ReplayDecisionLike, playerId: number): boolean {
  if (entry.is_obs) return false;
  if (entry.actor_to_move === playerId) return true;
  if (actionBelongsToPlayer(entry.chosen, playerId)) return true;
  if (actionBelongsToPlayer(entry.gt_action, playerId)) return true;
  return Boolean(entry.candidates?.some((candidate) => actionBelongsToPlayer(candidate.action, playerId)));
}

export function isReplayDiffForPlayer(entry: ReplayDecisionLike, playerId: number): boolean {
  return (
    isReplayPlayerDecision(entry, playerId) &&
    !entry.comparison_exempt &&
    entry.gt_action !== null &&
    entry.gt_action !== undefined &&
    !sameReplayAction(entry.chosen, entry.gt_action)
  );
}

export function isReplayReviewDiffForPlayer(
  entry: ReplayDecisionLike,
  playerId: number,
  activeTeacherModel?: string | null,
): boolean {
  if (!isReplayPlayerDecision(entry, playerId)) return false;
  if (entry.comparison_exempt) return false;

  const teacherReviews = entry.teacher_reviews && entry.teacher_reviews.length > 0
    ? entry.teacher_reviews
    : entry.teacher_review ? [entry.teacher_review] : [];
  if (teacherReviews.length === 0) {
    return isReplayDiffForPlayer(entry, playerId);
  }

  const review = activeTeacherModel
    ? teacherReviews.find((item) => item.model === activeTeacherModel) ?? teacherReviews[0]
    : teacherReviews[0];
  if (!review) return false;
  const actualAction = review.actual_action
    ?? (entry.gt_action == null && entry.chosen?.type === 'none' ? entry.chosen : null);
  const expectedAction = review.expected_action ?? review.top1?.action ?? null;
  if (actualAction && expectedAction && sameReplayAction(actualAction, expectedAction)) {
    return false;
  }
  if (review.is_equal === false) return true;
  if (typeof review.q_loss === 'number' && Number.isFinite(review.q_loss) && review.q_loss > 1e-9) {
    return true;
  }

  if (actualAction && expectedAction) {
    return !sameReplayAction(actualAction, expectedAction);
  }
  return false;
}
