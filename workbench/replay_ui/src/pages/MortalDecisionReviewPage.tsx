import { useEffect, useMemo, useState } from 'react';
import { Upload, ListFilter } from 'lucide-react';
import { MahjongTable } from '../components/BattleBoard/MahjongTable';
import { Tile } from '../components/BattleBoard/Tile';
import type { Action, BattleState, DiscardEntry, MeldEntry } from '../types/battle';
import type { LogitTileData } from '../utils/replayAdapter';
import { PageHeader, PageShell, SectionTitle } from '../components/Layout/PageScaffold';

type ReviewActionRow = {
  index: number;
  type: string;
  pai?: string | null;
  canonical_key?: string;
  mjai_events?: Action[];
  rulebase_score?: number | null;
  mortal_q?: number | null;
  teacher_prob?: number | null;
  student_before_score?: number | null;
  student_after_score?: number | null;
  rulebase_relative?: number | null;
  mortal_q_relative?: number | null;
  student_before_relative?: number | null;
  student_after_relative?: number | null;
  student_before_prob?: number | null;
  student_after_prob?: number | null;
  teacher_rank?: number | null;
  rulebase_rank?: number | null;
  student_before_rank?: number | null;
  student_after_rank?: number | null;
  is_rulebase_top1?: boolean;
  is_mortal_top1?: boolean;
  is_student_before_top1?: boolean;
  is_student_after_top1?: boolean;
  mortal_source_action_ids?: number[];
  mortal_available_action_ids?: number[];
  mortal_mask_available?: boolean;
  native_limitation_missing_q?: boolean;
};

type ReviewPlayer = {
  seat: number;
  is_actor?: boolean;
  hand?: unknown[];
  draw?: unknown;
  discards?: unknown[];
  melds?: unknown[];
  riichi?: boolean;
  score?: number;
};

type ReviewCase = {
  case_id: string;
  run_id?: string;
  episode_id?: string;
  step_id?: number | string;
  actor: number;
  row_scope?: string;
  review_reason?: string;
  selected_before?: string;
  selected_after?: string;
  teacher_top1?: string;
  rulebase_top1?: string;
  selected_changed?: boolean;
  teacher_disagreed?: boolean;
  is_native_limitation?: boolean;
  native_limitation_kind?: string;
  native_mortal?: {
    decision_actor?: number;
    decision_event_index?: number;
    mask_true_ids?: number[];
    missing_action_types?: string[];
  };
  round_state?: {
    bakaze?: string;
    kyoku?: number;
    honba?: number;
    oya?: number;
    riichi_sticks?: number;
    kyotaku?: number;
    dora_indicators?: unknown[];
    scores?: number[];
    wall_remaining?: number;
    last_discard?: unknown;
    seat_winds?: unknown[];
  };
  players?: ReviewPlayer[];
  legal_actions?: ReviewActionRow[];
  visibility?: {
    actor_hand_visible?: boolean;
    opponent_hands_visible?: boolean;
    oracle_fields_available?: boolean;
  };
};

const REVIEW_FILTERS = [
  ['all', '全部'],
  ['selected_changed', '真实变化'],
  ['teacher_disagreement', 'Mortal 分歧'],
  ['reach_related', '立直'],
  ['kan_related', '杠'],
  ['call_related', '副露'],
  ['native_limitation', 'Native 限制'],
] as const;

const TILE_ID_TO_NAME = [
  '1m', '2m', '3m', '4m', '5m', '6m', '7m', '8m', '9m',
  '1p', '2p', '3p', '4p', '5p', '6p', '7p', '8p', '9p',
  '1s', '2s', '3s', '4s', '5s', '6s', '7s', '8s', '9s',
  'E', 'S', 'W', 'N', 'P', 'F', 'C',
];

export function MortalDecisionReviewPage() {
  const [cases, setCases] = useState<ReviewCase[]>([]);
  const [selectedIndex, setSelectedIndex] = useState(0);
  const [filter, setFilter] = useState<(typeof REVIEW_FILTERS)[number][0]>('all');
  const [showOpponentHands, setShowOpponentHands] = useState(false);
  const [loadError, setLoadError] = useState<string | null>(null);

  const visibleCases = useMemo(() => {
    const filtered = cases.filter((item) => {
      if (filter === 'all') return true;
      if (filter === 'native_limitation') return Boolean(item.is_native_limitation) || String(item.review_reason ?? '').includes('native_limitation');
      return String(item.review_reason ?? '').includes(filter);
    });
    return [...filtered].sort((a, b) => riskScore(b) - riskScore(a));
  }, [cases, filter]);

  const selectedCase = visibleCases[Math.min(selectedIndex, Math.max(0, visibleCases.length - 1))] ?? null;
  const battleState = selectedCase ? reviewCaseToBattleState(selectedCase) : null;
  const logitData = selectedCase ? reviewCaseLogitData(selectedCase) : [];
  const revealedOpponentHands = selectedCase ? reviewCaseRevealedHands(selectedCase) : null;

  useEffect(() => {
    const casesUrl = new URLSearchParams(window.location.search).get('cases');
    if (!casesUrl) return;
    let cancelled = false;
    fetch(casesUrl)
      .then((response) => {
        if (!response.ok) throw new Error(`${response.status} ${response.statusText}`);
        return response.text();
      })
      .then((text) => {
        if (cancelled) return;
        loadText(text);
      })
      .catch((error) => {
        if (!cancelled) setLoadError(String(error));
      });
    return () => {
      cancelled = true;
    };
  }, []);

  const loadText = (text: string) => {
    const rows = text
      .split(/\r?\n/)
      .filter(Boolean)
      .map((line) => JSON.parse(line) as ReviewCase);
    setCases(rows);
    setSelectedIndex(0);
  };

  const loadFile = async (file: File) => {
    setLoadError(null);
    try {
      loadText(await file.text());
    } catch (error) {
      setLoadError(String(error));
    }
  };

  return (
    <PageShell width={1600}>
      <PageHeader
        eyebrow="Mortal"
        title="Mortal Action-Q 决策审阅"
        description="加载 decision_review_cases.jsonl，用项目现有牌桌 UI 查看学生策略、Mortal teacher 和 rulebase 的分歧。"
      />

      <div className="card" style={{ display: 'grid', gap: 14 }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 12, flexWrap: 'wrap' }}>
          <label className="btn-primary" style={{ height: 38, padding: '0 16px', display: 'inline-flex', alignItems: 'center', gap: 8, cursor: 'pointer' }}>
            <Upload size={16} />
            加载 JSONL
            <input
              type="file"
              accept=".jsonl,.json"
              style={{ display: 'none' }}
              onChange={(event) => {
                const file = event.target.files?.[0];
                if (file) void loadFile(file);
              }}
            />
          </label>
          <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
            <ListFilter size={16} color="var(--text-secondary)" />
            <select value={filter} onChange={(event) => { setFilter(event.target.value as typeof filter); setSelectedIndex(0); }} style={selectStyle}>
              {REVIEW_FILTERS.map(([value, label]) => <option key={value} value={value}>{label}</option>)}
            </select>
          </div>
          <button
            className="btn-primary"
            onClick={() => setShowOpponentHands((value) => !value)}
            style={{ height: 38, padding: '0 16px', background: showOpponentHands ? 'var(--success)' : 'var(--accent)' }}
          >
            {showOpponentHands ? '隐藏他家手牌（实际可见视角）' : '显示他家手牌（Oracle Debug）'}
          </button>
          <span style={{ color: showOpponentHands ? 'var(--warning)' : 'var(--text-secondary)', fontSize: 13 }}>
            当前视角：{showOpponentHands ? 'Oracle debug' : '实际可见'}
          </span>
          <span style={{ color: 'var(--text-secondary)', fontSize: 13 }}>
            {cases.length ? `${visibleCases.length} / ${cases.length} cases` : '未加载'}
          </span>
          {loadError && <span style={{ color: 'var(--danger)', fontSize: 13 }}>{loadError}</span>}
        </div>
      </div>

      <div style={{ display: 'grid', gridTemplateColumns: '320px minmax(0, 1fr)', gap: 14, marginTop: 14, minHeight: 760 }}>
        <div className="card" style={{ padding: 0, overflow: 'hidden' }}>
          <div style={{ padding: 14 }}>
            <SectionTitle title="Cases" description="默认按风险排序：真实变化、分歧、杠/副露/立直优先。" />
          </div>
          <div style={{ maxHeight: 720, overflow: 'auto', borderTop: '1px solid var(--border)' }}>
            {visibleCases.map((item, index) => (
              <button
                key={`${item.case_id}-${index}`}
                onClick={() => setSelectedIndex(index)}
                style={{
                  ...caseButtonStyle,
                  background: item === selectedCase ? 'var(--card-hover)' : 'var(--card-bg)',
                  borderLeft: item === selectedCase ? '3px solid var(--accent)' : '3px solid transparent',
                }}
              >
                <strong style={{ display: 'block', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{item.case_id}</strong>
                <span style={{ color: 'var(--text-secondary)', fontSize: 12 }}>{item.review_reason} | step {item.step_id}</span>
                <span style={{ color: 'var(--text-secondary)', fontSize: 12, display: 'block', marginTop: 4 }}>
                  {labelAction(item.selected_before)} -&gt; {labelAction(item.selected_after)} | Mortal {labelAction(item.teacher_top1)}
                </span>
              </button>
            ))}
          </div>
        </div>

        <div style={{ display: 'grid', gridTemplateRows: 'minmax(620px, 1fr) auto', gap: 14, minWidth: 0 }}>
          <div className="card" style={{ padding: 0, overflow: 'hidden', minHeight: 620 }}>
            {battleState ? (
              <MahjongTable
                state={battleState}
                onAction={() => undefined}
                isMyTurn={false}
                selectedTile={null}
                selectedTileIdx={null}
                onTileSelect={() => undefined}
                autoHora={false}
                setAutoHora={() => undefined}
                noMeld={false}
                setNoMeld={() => undefined}
                autoTsumogiri={false}
                setAutoTsumogiri={() => undefined}
                suppressActionBar
                mode="replay"
                logitData={logitData}
                revealedOpponentHands={showOpponentHands ? revealedOpponentHands : null}
                onToggleOpponentHands={() => setShowOpponentHands((value) => !value)}
              />
            ) : (
              <div style={{ padding: 24, color: 'var(--text-secondary)' }}>
                请选择一个 decision_review_cases.jsonl 文件。
              </div>
            )}
          </div>

          {selectedCase && <ReviewDetail caseItem={selectedCase} />}
        </div>
      </div>
    </PageShell>
  );
}

function ReviewDetail({ caseItem }: { caseItem: ReviewCase }) {
  const rows = [...(caseItem.legal_actions ?? [])].sort(
    (a, b) => scoreSortValue(b, caseItem) - scoreSortValue(a, caseItem),
  );
  return (
    <div className="card">
      <SectionTitle
        title="Action Scores"
        description={caseItem.is_native_limitation
          ? 'Native limitation case：这行未进入训练，只用于牌桌审阅。没有 Mortal Q 的 legal action 会标为 missing Mortal Q。'
          : '按 student after raw logit 从高到低排序。Teacher P 是 Mortal action-Q 在 full-legal support 上 softmax 后的监督概率。'}
      />
      {caseItem.is_native_limitation && (
        <div style={{ padding: 10, marginBottom: 12, border: '1px solid var(--warning)', borderRadius: 8, color: 'var(--warning)', fontSize: 12 }}>
          <strong>{caseItem.native_limitation_kind ?? caseItem.review_reason}</strong>
          <div style={{ color: 'var(--text-secondary)', marginTop: 4 }}>
            Mortal native decision actor={caseItem.native_mortal?.decision_actor ?? '-'} event={caseItem.native_mortal?.decision_event_index ?? '-'}；
            missing={caseItem.native_mortal?.missing_action_types?.join(', ') || '-'}；
            mask ids={caseItem.native_mortal?.mask_true_ids?.join(', ') || '-'}
          </div>
        </div>
      )}
      <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 10, marginBottom: 12, fontSize: 13 }}>
        <div><strong>before:</strong> {labelAction(caseItem.selected_before)}</div>
        <div><strong>after:</strong> {labelAction(caseItem.selected_after)}</div>
        <div><strong>Mortal:</strong> {labelAction(caseItem.teacher_top1)}</div>
      </div>
      <div style={{ color: 'var(--text-secondary)', fontSize: 12, marginBottom: 10 }}>
        Rank 列中 1 表示该打分源的最高分；Mortal Rank=Mortal Q 排名，Before/After Rank=学生更新前/后的排名。
      </div>
      <div style={{ overflowX: 'auto' }}>
        <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 12 }}>
          <thead>
            <tr>
              {['#', 'Action', 'Mortal Q', 'Teacher P', 'Before', 'After', 'Mortal Rank', 'Before Rank', 'After Rank', 'Flags'].map((head) => (
                <th key={head} style={thStyle}>{head}</th>
              ))}
            </tr>
          </thead>
          <tbody>
            {rows.map((row) => (
              <tr key={row.index} style={{ background: row.is_mortal_top1 ? 'rgba(34,197,94,0.08)' : undefined }}>
                <td style={tdStyle}>{row.index}</td>
                <td style={tdStyle}>
                  <strong>{row.type}</strong>
                  <ActionTiles row={row} />
                  <div style={{ color: 'var(--text-secondary)', marginTop: 4 }}>{actionText(row)}</div>
                </td>
                <td style={tdStyle}>{fmt(row.mortal_q)}</td>
                <td style={tdStyle}>{fmt(row.teacher_prob)}</td>
                <td style={tdStyle}>{scoreWithProb(row.student_before_score, row.student_before_prob)}</td>
                <td style={tdStyle}>{scoreWithProb(row.student_after_score, row.student_after_prob)}</td>
                <td style={tdStyle}>{row.teacher_rank ?? '-'}</td>
                <td style={tdStyle}>{row.student_before_rank ?? '-'}</td>
                <td style={tdStyle}>{row.student_after_rank ?? '-'}</td>
                <td style={tdStyle}>{[
                  row.is_mortal_top1 ? 'Mortal' : '',
                  row.is_student_before_top1 ? 'before' : '',
                  row.is_student_after_top1 ? 'after' : '',
                  row.native_limitation_missing_q ? 'missing Mortal Q' : '',
                ].filter(Boolean).join(', ')}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}

function scoreSortValue(row: ReviewActionRow, caseItem: ReviewCase): number {
  const value = caseItem.is_native_limitation ? row.mortal_q : row.student_after_score;
  return Number(value ?? Number.NEGATIVE_INFINITY);
}

function ActionTiles({ row }: { row: ReviewActionRow }) {
  const tiles = actionTiles(row);
  if (!tiles.length) {
    return <div style={{ color: 'var(--text-secondary)', marginTop: 5 }}>no tile</div>;
  }
  return (
    <div style={{ display: 'flex', alignItems: 'center', gap: 6, flexWrap: 'wrap', minHeight: 34, marginTop: 7 }}>
      {tiles.map((tile, index) => (
        <span key={`${tile}-${index}`} style={{ width: 24, height: 32, display: 'inline-flex', alignItems: 'center', justifyContent: 'center' }}>
          <Tile tile={tile} size="small" />
        </span>
      ))}
    </div>
  );
}

function scoreWithProb(score: number | null | undefined, prob?: number | null) {
  return (
    <div>
      <div>{fmt(score)}</div>
      {typeof prob === 'number' && Number.isFinite(prob) && (
        <div style={{ color: 'var(--text-secondary)', fontSize: 11 }}>p {fmt(prob)}</div>
      )}
    </div>
  );
}

function reviewCaseToBattleState(item: ReviewCase): BattleState {
  const actor = Number(item.actor ?? 0);
  const round = item.round_state ?? {};
  const players = item.players ?? [];
  const actorPlayer = players.find((player) => Number(player.seat) === actor);
  const scores = round.scores ?? players.map((player) => player.score ?? 25000);
  while (scores.length < 4) scores.push(25000);
  const discards = seatArray((seat) => toDiscards(players.find((player) => Number(player.seat) === seat)?.discards));
  const melds = seatArray((seat) => toMelds(players.find((player) => Number(player.seat) === seat)?.melds));
  const lastDiscard = toLastDiscard(round.last_discard);
  return {
    game_id: item.run_id ?? 'mortal-review',
    phase: 'playing',
    winner: null,
    bakaze: String(round.bakaze ?? 'E'),
    kyoku: Number(round.kyoku ?? 1),
    honba: Number(round.honba ?? 0),
    kyotaku: Number(round.kyotaku ?? round.riichi_sticks ?? 0),
    oya: Number(round.oya ?? 0),
    scores,
    dora_markers: toTiles(round.dora_indicators),
    actor_to_move: actor,
    last_discard: lastDiscard,
    hand: toTiles(actorPlayer?.hand),
    tsumo_pai: toTile(actorPlayer?.draw) || null,
    discards,
    melds,
    reached: seatArray((seat) => Boolean(players.find((player) => Number(player.seat) === seat)?.riichi)),
    pending_reach: [false, false, false, false],
    needs_input: false,
    input_context: null,
    legal_actions: toLegalActions(item.legal_actions, actor),
    remaining_wall: Number(round.wall_remaining ?? 0),
    human_player_id: actor,
    player_info: seatArray((seat) => ({
      player_id: seat,
      name: seat === actor ? `Actor ${seat}` : `P${seat}${seatWindLabel(round.seat_winds, seat)}`,
      type: seat === actor ? 'human' : 'bot',
    })),
    replay_draw_actor: actor,
  };
}

function reviewCaseRevealedHands(item: ReviewCase): string[][] {
  return seatArray((seat) => toTiles((item.players ?? []).find((player) => Number(player.seat) === seat)?.hand));
}

function reviewCaseLogitData(item: ReviewCase): LogitTileData[] {
  const actorPlayer = (item.players ?? []).find((player) => Number(player.seat) === Number(item.actor));
  const hand = toTiles(actorPlayer?.hand);
  const draw = toTile(actorPlayer?.draw);
  const scores = new Map<string, number>();
  for (const row of item.legal_actions ?? []) {
    const event = row.mjai_events?.[0];
    if (event?.type !== 'dahai' || !event.pai) continue;
    scores.set(event.pai, row.student_after_score ?? row.mortal_q ?? 0);
  }
  const values = [...scores.values()];
  const min = values.length ? Math.min(...values) : 0;
  const max = values.length ? Math.max(...values) : 1;
  const range = Math.max(0.001, max - min);
  return [...hand, ...(draw ? [draw] : [])].map((pai, index, all) => {
    const score = scores.get(pai);
    return {
      pai,
      score,
      pct: score === undefined ? 0 : Math.max(6, ((score - min) / range) * 100),
      isChosen: Boolean((item.legal_actions ?? []).find((row) => row.is_student_after_top1 && row.mjai_events?.[0]?.type === 'dahai' && row.mjai_events[0].pai === pai)),
      isGt: Boolean((item.legal_actions ?? []).find((row) => row.is_mortal_top1 && row.mjai_events?.[0]?.type === 'dahai' && row.mjai_events[0].pai === pai)),
      isTsumo: Boolean(draw && index === all.length - 1),
    };
  });
}

function toLegalActions(rows: ReviewActionRow[] | undefined, actor: number): Action[] {
  const result = (rows ?? [])
    .map((row) => row.mjai_events?.[0] ?? actionFromRow(row, actor))
    .map((action) => action ? normalizeActionActor(action, actor) : null)
    .filter((action): action is Action => Boolean(action));
  return result.length ? result : [{ type: 'none', actor }];
}

function normalizeActionActor(action: Action, actor: number): Action {
  return { ...action, actor };
}

function actionFromRow(row: ReviewActionRow, actor: number): Action | null {
  const tile = toTile(row.pai) || tileFromCanonical(row.canonical_key);
  switch (row.type) {
    case 'DISCARD':
    case 'REACH_DISCARD':
      return tile ? { type: 'dahai', actor, pai: tile } : null;
    case 'PASS':
      return { type: 'none', actor };
    case 'RON':
      return { type: 'hora', actor };
    case 'PON':
      return { type: 'pon', actor, pai: tile || undefined, consumed: consumedFromCanonical(row.canonical_key) };
    case 'CHI':
      return { type: 'chi', actor, pai: tile || undefined, consumed: consumedFromCanonical(row.canonical_key) };
    case 'DAIMINKAN':
      return { type: 'daiminkan', actor, pai: tile || undefined, consumed: consumedFromCanonical(row.canonical_key) };
    case 'ANKAN':
      return { type: 'ankan', actor, pai: tile || undefined, consumed: consumedFromCanonical(row.canonical_key) };
    case 'KAKAN':
      return { type: 'kakan', actor, pai: tile || undefined, consumed: consumedFromCanonical(row.canonical_key) };
    case 'TSUMO':
      return { type: 'hora', actor, is_tsumo: true };
    case 'RYUKYOKU':
      return { type: 'ryukyoku', actor };
    default:
      return null;
  }
}

function actionTiles(row: ReviewActionRow): string[] {
  const event = row.mjai_events?.[0];
  const consumed = Array.isArray(event?.consumed) ? event.consumed.map(toTile).filter((tile): tile is string => Boolean(tile)) : consumedFromCanonical(row.canonical_key);
  const pai = toTile(event?.pai ?? row.pai) || tileFromCanonical(row.canonical_key);
  if (row.type === 'PASS' || row.type === 'RYUKYOKU') return [];
  if (consumed.length && pai) return [...consumed, pai];
  if (consumed.length) return consumed;
  return pai ? [pai] : [];
}

function actionText(row: ReviewActionRow): string {
  const tiles = actionTiles(row);
  if (!tiles.length) return row.type;
  if (['PON', 'CHI', 'DAIMINKAN', 'ANKAN', 'KAKAN'].includes(row.type)) {
    return `${row.type} ${tiles.join(' ')}`;
  }
  return `${row.type} ${tiles[tiles.length - 1]}`;
}

function toDiscards(value: unknown): DiscardEntry[] {
  return (Array.isArray(value) ? value : []).map((entry) => {
    if (typeof entry === 'object' && entry !== null) {
      const record = entry as Record<string, unknown>;
      return {
        pai: toTile(record.pai ?? record.tile) || '',
        tsumogiri: Boolean(record.tsumogiri),
        reach_declared: Boolean(record.reach_declared ?? record.reach),
      };
    }
    return { pai: toTile(entry) || '', tsumogiri: false, reach_declared: false };
  }).filter((entry) => entry.pai);
}

function toMelds(value: unknown): MeldEntry[] {
  return (Array.isArray(value) ? value : []).map((entry) => {
    const record = typeof entry === 'object' && entry !== null ? entry as Record<string, unknown> : {};
    const type = String(record.type ?? 'pon') as MeldEntry['type'];
    return {
      type,
      pai: toTile(record.pai ?? record.tile) || '',
      consumed: toTiles(record.consumed),
      target: Number(record.from ?? record.target ?? 0),
    };
  }).filter((entry) => entry.pai || entry.consumed.length);
}

function toLastDiscard(value: unknown): BattleState['last_discard'] {
  if (!value || typeof value !== 'object') return null;
  const record = value as Record<string, unknown>;
  const pai = toTile(record.pai ?? record.tile);
  if (!pai) return null;
  return { actor: Number(record.actor ?? 0), pai };
}

function toTiles(value: unknown): string[] {
  return (Array.isArray(value) ? value : []).map(toTile).filter((tile): tile is string => Boolean(tile));
}

function toTile(value: unknown): string | null {
  if (value === null || value === undefined || value === '') return null;
  if (typeof value === 'object') {
    const record = value as Record<string, unknown>;
    return toTile(record.pai ?? record.tile ?? record.name);
  }
  const text = String(value);
  if (/^\d+$/.test(text)) {
    return TILE_ID_TO_NAME[Number(text)] ?? text;
  }
  return text === 'None' ? null : text;
}

function tileFromCanonical(key: string | undefined): string | null {
  const match = String(key ?? '').match(/tile=([^|]+)/);
  if (!match || match[1] === '-1') return null;
  return toTile(match[1]);
}

function consumedFromCanonical(key: string | undefined): string[] {
  const match = String(key ?? '').match(/consumed=([^|]*)/);
  if (!match || !match[1]) return [];
  return match[1].split(',').map(toTile).filter((tile): tile is string => Boolean(tile));
}

function seatArray<T>(factory: (seat: number) => T): [T, T, T, T] {
  return [factory(0), factory(1), factory(2), factory(3)];
}

function seatWindLabel(value: unknown[] | undefined, seat: number): string {
  const wind = toTile(value?.[seat]);
  return wind ? ` ${wind}` : '';
}

function riskScore(item: ReviewCase): number {
  const reason = String(item.review_reason ?? '');
  return (item.is_native_limitation ? 120 : 0)
    + (item.selected_changed && item.teacher_disagreed ? 100 : 0)
    + (reason.includes('kan_related') ? 40 : 0)
    + (reason.includes('call_related') ? 30 : 0)
    + (reason.includes('reach_related') ? 25 : 0)
    + (reason.includes('teacher_disagreement') ? 10 : 0);
}

function labelAction(value: string | undefined): string {
  if (!value) return '-';
  const type = value.split('|', 1)[0];
  const tile = tileFromCanonical(value);
  return tile ? `${type} ${tile}` : type;
}

function fmt(value: number | null | undefined): string {
  return typeof value === 'number' && Number.isFinite(value) ? value.toFixed(3) : '-';
}

const selectStyle: React.CSSProperties = {
  height: 38,
  border: '1px solid var(--border)',
  borderRadius: 8,
  padding: '0 12px',
  background: 'var(--card-bg)',
  color: 'var(--text-primary)',
};

const caseButtonStyle: React.CSSProperties = {
  display: 'block',
  width: '100%',
  textAlign: 'left',
  border: 0,
  borderBottom: '1px solid var(--border)',
  padding: '11px 12px',
  color: 'var(--text-primary)',
  cursor: 'pointer',
};

const thStyle: React.CSSProperties = {
  textAlign: 'left',
  padding: '8px 10px',
  borderBottom: '1px solid var(--border)',
  color: 'var(--text-secondary)',
};

const tdStyle: React.CSSProperties = {
  padding: '8px 10px',
  borderBottom: '1px solid var(--border)',
  verticalAlign: 'top',
};
