import type { ReplayData } from '../types/replay';

function isPlaceholderName(name: string): boolean {
  return /^Bot\d+$/.test(name) || /^P\d+$/.test(name);
}

function machineFallbackName(botType: string, idx: number): string {
  return `${botType}-${idx + 1}号机`;
}

export function normalizeReplayPlayerNames(data: ReplayData | null | undefined): string[] {
  const rawNames = data?.player_names;
  const botType = data?.bot_type ?? 'bot';

  if (rawNames && rawNames.length === 4 && rawNames.some((name) => !isPlaceholderName(name))) {
    return rawNames;
  }

  if (rawNames && rawNames.length === 4) {
    return rawNames.map((name, idx) => (isPlaceholderName(name) ? machineFallbackName(botType, idx) : name));
  }

  return [0, 1, 2, 3].map((idx) => machineFallbackName(botType, idx));
}

export function replayPlayerDisplayName(
  playerNames: string[],
  pid: number | null | undefined,
): string {
  if (pid === null || pid === undefined || pid < 0) return '未知玩家';
  return playerNames[pid] ?? `P${pid}`;
}
