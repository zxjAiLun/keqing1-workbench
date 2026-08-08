import cases from '../../../tests/fixtures/replay_action_semantics_matrix.json' with { type: 'json' };
import { sameReplayAction } from '../src/utils/tileUtils.ts';

type ComparableAction = {
  type: string;
  actor?: number;
  pai?: string;
  target?: number;
  consumed?: string[];
  tsumogiri?: boolean;
};

type Case = {
  name: string;
  left: ComparableAction;
  right: ComparableAction;
  same: boolean;
};

for (const testCase of cases as Case[]) {
  const actual = sameReplayAction(testCase.left, testCase.right);
  if (actual !== testCase.same) {
    throw new Error(
      `sameReplayAction mismatch for ${testCase.name}: expected ${testCase.same}, got ${actual}`,
    );
  }
}

console.log(`replay action semantics OK (${(cases as Case[]).length} cases)`);
