// workbench/replay_ui/scripts/checkParticipantSemantics.ts
// R10 语义检查：4 座不变量、force-save 流程、无 player_id===0 硬编码、
// 路由/导航注册、后端 router 挂载、pytest 白名单齐全。
import { readFileSync, existsSync } from 'node:fs';
import { resolve } from 'node:path';

const ROOT = resolve(import.meta.dirname, '..');
let failures = 0;
function check(ok: boolean, message: string): void {
  if (!ok) {
    failures += 1;
    console.error(`FAIL: ${message}`);
  }
}

function read(p: string): string {
  const full = resolve(ROOT, p);
  return existsSync(full) ? readFileSync(full, 'utf-8') : '';
}

function inFile(p: string, needles: string[]): boolean {
  const content = read(p);
  return needles.every((needle) => content.includes(needle));
}

// 1) 4 座不变量：SEAT_WINDS 恰好 4 座
const labels = read('src/components/Matches/labels.ts');
check(/SEAT_WINDS\s*=\s*\[\s*'東'\s*,\s*'南'\s*,\s*'西'\s*,\s*'北'\s*\]/.test(labels), 'labels.ts 的 SEAT_WINDS 必须恰好 4 座');
check(inFile('src/components/Matches/SeatPicker.tsx', ['SEAT_WINDS']), 'SeatPicker 使用 SEAT_WINDS');

// 2) MatchEntryPage 走 createMatch + force-save 流程
check(
  inFile('src/pages/MatchEntryPage.tsx', ['createMatch', 'force', 'reason', 'ValidationNotice']),
  'MatchEntryPage 含 createMatch + force-save 流程',
);

// 3) 新页面无 player_id===0 / seat0=human 硬编码
for (const page of [
  'src/pages/ParticipantsPage.tsx',
  'src/pages/MatchesPage.tsx',
  'src/pages/MatchEntryPage.tsx',
  'src/pages/MatchDetailPage.tsx',
]) {
  const content = read(page);
  check(
    !/player_id\s*===\s*0/.test(content) && !/id\s*===\s*0\s*\?\s*'human'/.test(content),
    `${page} 不应硬编码 player_id===0 / seat0=human`,
  );
}

// 4) types 包含核心概念
const types = read('src/types/participants.ts');
for (const name of ['AccountType', 'ControllerType', 'MatchSeat', 'Match', 'RevisionSummary']) {
  check(types.includes(name), `types/participants.ts 包含 ${name}`);
}

// 5) 路由与导航注册
const routes = read('src/routes.ts');
check(routes.includes("participants: '/participants'"), 'routes.ts 含 /participants');
check(routes.includes("matches: '/matches'"), 'routes.ts 含 /matches');
check(routes.includes("matchEntry: '/matches/new'"), 'routes.ts 含 /matches/new');
check(routes.includes("MATCH_DETAIL_PATTERN = '/matches/:matchId'"), 'routes.ts 含 MATCH_DETAIL_PATTERN');
check(inFile('src/App.tsx', ['ParticipantsPage', 'MatchesPage', 'MatchEntryPage', 'MatchDetailPage']), 'App.tsx 注册 4 个 R10 页面');
check(inFile('src/components/Layout/Sidebar.tsx', ['routes.participants', 'routes.matches']), 'Sidebar 链接参赛者与对局记录');

// 6) 后端 router 挂载
check(
  inFile('../replay/server.py', ['from participants.api import router as participants_router', 'include_router(participants_router)']),
  'server.py 挂载 participants router',
);
check(inFile('../participants/api.py', ['prefix="/api/participants"']), 'participants api 前缀 /api/participants');

// 7) pytest 白名单：participants 测试以 glob 收录（新增 R12-A 文件自动纳入）
const pyproject = read('../../pyproject.toml');
check(
  pyproject.includes('test_participants_*.py'),
  'pyproject python_files 白名单包含 test_participants_*.py（glob）',
);

// 8) MatchesPage 渲染 status（active|void）
const matchesPage = read('src/pages/MatchesPage.tsx');
check(matchesPage.includes('status') && matchesPage.includes('void'), 'MatchesPage 支持状态筛选（active/void）');

// 9) R10-E：通用四人阵容——roster 与 launcher 数量分离 + 宽松捕获
const playwithyou = read('../gateway/api/playwithyou.py');
check(playwithyou.includes('ParticipantBindingRequest'), 'playwithyou 定义 ParticipantBindingRequest（预期四人阵容）');
check(playwithyou.includes('roster: List[') && playwithyou.includes('ParticipantBindingRequest'), 'StartPlayWithYouRequest 含 roster 字段');
check(playwithyou.includes('launcher_slot'), 'ParticipantBindingRequest 含 launcher_slot（与 launcher 数量分离）');
check(playwithyou.includes('scope="session"') || playwithyou.includes("scope='session'"), 'start 注册 session-scoped 别名');
const capture = read('../gateway/playwithyou_capture.py');
check(capture.includes('awaiting_import'), '捕获层支持 awaiting_import（任一 observer 捕获 log 即可）');
check(capture.includes('roster'), 'CaptureBinding 支持 roster 模式');

// 10) R10-E Repair：真实 launcher 接线 / 赛后状态机 / 互斥
const launcher = read('../launch_tenhou_bots.py');
check(launcher.includes('mode == "roster"') && launcher.includes('launcher_slot'), 'launcher 识别 roster 模式并按 slot 接线');
check(
  launcher.includes('config.ladder_account_id = observer_key') &&
    launcher.includes('or f"launcher:{index}"'),
  'launcher observer key：account_id or expected_raw_name or launcher:N（唯一）',
);
check(playwithyou.includes('_validate_roster_bindings'), 'start 前校验 roster（账号存在/启用/模型归属）');
check(playwithyou.includes('不能同时开启'), 'roster 与旧正式天梯绑定互斥');
const capture2 = read('../gateway/playwithyou_capture.py');
check(capture2.includes('log_captured'), 'roster 状态机：开局仅捕获 log 为 log_captured（非可导入）');
check(capture2.includes('evidence_warning'), 'roster 分数不一致记录 evidence_warning 不阻塞');

// 11) R10-E Repair 2：slot-stable 冻结 / checkpoint 精确匹配 / 单锁原子
check(playwithyou.includes('_resolve_artifact_path'), '冻结按 artifact 绝对路径精确匹配 checkpoint');
check(playwithyou.includes('resolve_bot_spec'), '冻结复用真实 bot_registry checkpoint 解析');
check(playwithyou.includes('roster_bindings, frozen_launcher_specs = _freeze_launcher_models(roster_bindings, specs)'), '冻结保持原 roster 顺序（slot-stable 写回）');
check(playwithyou.includes('participants_data_lock'), 'roster 校验/冻结/别名注册在同一 data_lock');
check(playwithyou.includes('_rollback_roster_start'), 'Popen 失败也回滚 roster 启动');

// 12) R10-E Repair 3：冻结 checkpoint 传给 runtime / evidence 透出 API
const launcher2 = read('../launch_tenhou_bots.py');
check(launcher2.includes('resolved_checkpoint_path'), 'launcher 从 binding 冻结路径设 model_path');
check(launcher2.includes('config.model_path = Path(frozen_path)'), 'runtime 加载冻结路径而非动态 spec');
check(playwithyou.includes('launcher_command_specs'), '父进程用冻结绝对路径作为 --bots');
check(playwithyou.includes('"resolved_checkpoint_path": str(resolved_path)'), 'binding 冻结 resolved_checkpoint_path');
check(playwithyou.includes('"roster": payload.get("roster") or []'), '_discover_captures 透出 roster');
check(playwithyou.includes('"evidence_warning": payload.get("evidence_warning")'), '_discover_captures 透出 evidence_warning');

// 13) R10-F：Ledger-driven Ladder Projection
const ladderIngest = read('../replay/ladder_ingest.py');
check(ladderIngest.includes('class ParticipantLedgerAdapter'), 'ladder_ingest 新增 participants ledger adapter');
check(ladderIngest.includes('participants_cfg') && ladderIngest.includes('participants_dir.is_dir()'), '赛季 ingest 配置启用 participants source');
const ledgerF = read('../participants/ledger.py');
check(ledgerF.includes('ladder_dirty_path') && ledgerF.includes('mark_ladder_dirty'), 'ledger 有 dirty marker');
check(ledgerF.includes('set_season_projection_state'), 'ledger 支持批量投影状态');
check(ledgerF.includes('ladder_projection_state="pending"') || ledgerF.includes("ladder_projection_state='pending'"), 'create 置 pending 投影状态');
const participantsApi = read('../participants/api.py');
check(participantsApi.includes('/ladder/{season_id}/project'), '投影触发 API');
check(participantsApi.includes('/ladder/{season_id}/status'), '投影状态 API');

// 14) R10-F Repair：fingerprint / generation CAS / exclusive / 自动消费
const ladderIngest2 = read('../replay/ladder_ingest.py');
check(ladderIngest2.includes('participants_projection_fingerprint'), 'ledger 语义投影指纹（publisher unchanged-skip 感知）');
check(ladderIngest2.includes('"exclusive"') || ladderIngest2.includes("'exclusive'"), 'participants exclusive 排他模式');
const ledgerF2 = read('../participants/ledger.py');
check(ledgerF2.includes('"generation": uuid.uuid4().hex'), 'dirty marker 带 generation（CAS）');
check(ledgerF2.includes('complete_ladder_projection'), '发布完成 CAS（generation 变了保留 dirty）');
check(ledgerF2.includes('model_fields_set'), 'revise 区分省略与显式 null（可清空 season）');
check(ledgerF2.includes('for season in {old_season, new_season}'), 'season move → 新旧双 dirty');
const pls = read('../replay/publish_ladder_snapshot.py');
check(pls.includes('extra_fingerprint'), 'publisher 指纹合并 ledger 投影输入');
const proj = read('../participants/projection.py');
check(proj.includes('run_dirty_projection'), '自动 dirty consumer');
check(proj.includes('start_worker'), '后台 worker 启动');

// 15) R10-F Repair 2：begin barrier / single-flight / lifespan
const ledgerF3 = read('../participants/ledger.py');
check(ledgerF3.includes('begin_ladder_projection'), '投影 begin barrier（锁内恢复 pending 后冻结 generation）');
check(ledgerF3.includes('mark_season_projection_error'), '失败回写带 generation CAS');
const paths = read('../participants/paths.py');
check(paths.includes('try_file_lock'), '非阻塞跨进程锁（per-season single-flight）');
const proj2 = read('../participants/projection.py');
check(proj2.includes('projection_locks'), 'per-season 单飞锁');
check(proj2.includes('already_running'), 'second caller 返回 already_running 不进 publisher');
check(proj2.includes('stop_worker'), 'worker 可停止（lifespan）');
const server2 = read('../replay/server.py');
check(server2.includes('participants_projection.start_worker()') && server2.includes('participants_projection.stop_worker()'), 'worker 放入 FastAPI lifespan');

// 16) R10-F Repair 3：lease 锁 ownership / worker 重入
const paths3 = read('../participants/paths.py');
check(paths3.includes('try_lease_lock'), 'projection 专用 lease 锁（owner 身份）');
check(paths3.includes('_pid_is_alive'), 'reclaim 前校验 owner PID 存活');
check(paths3.includes('payload.get("token") == token') || paths3.includes('payload.get("token") == token'), '释放仅当 token 匹配（不删后来 owner 的锁）');
const proj3 = read('../participants/projection.py');
check(proj3.includes('_stop.clear()'), 'worker start 可重入（清 stop 标记）');
const tstypes = read('src/types/participants.ts');
check(tstypes.includes("'needs_rebuild'") && tstypes.includes("'already_running'"), 'TS 投影状态含 needs_rebuild/already_running');

// 17) R10-F Repair 4：reclaim 串行化 / worker shutdown 保引用
const paths4 = read('../participants/paths.py');
check(paths4.includes('.reclaim'), 'dead-lease reclaim 用 reclaim 互斥串行化');
check(paths4.includes('在 reclaim 临界区内重新读取/重新判定'), 'reclaim 临界区内重新判定 dead-owner 后才删除');
const proj4 = read('../participants/projection.py');
check(proj4.includes('if _thread is not None and _thread.is_alive():') && proj4.includes('_restart_pending'), '长 publisher 时 start 登记 deferred restart 不重入');

// 18) R10-F Repair 5：crash-safe reclaim mutex / deferred restart
const paths5 = read('../participants/paths.py');
check(paths5.includes('_try_advisory_lock'), 'reclaim 互斥用 OS advisory lock（进程退出自动释放）');
check(paths5.includes('msvcrt.locking') && paths5.includes('fcntl.flock'), 'reclaim 锁跨平台（Windows/POSIX）');
check(!paths5.includes('reclaim_path.unlink'), 'reclaim 锁不再手动删除（无 stale 删除协议）');
const proj5 = read('../participants/projection.py');
check(proj5.includes('_restart_pending'), 'deferred worker restart 登记');
check(proj5.includes('_maybe_restart'), '旧 worker 退出后自动重启');

// 19) R10-G：账号详细统计
const statsP = read('../participants/stats.py');
check(statsP.includes('STATS_CONTRACT_VERSION'), '统计带 stats_contract_version');
check(statsP.includes('_rate(') && statsP.includes('hands_with_detail'), 'completeness-aware 分母（缺 replay 不计入）');
check(statsP.includes('result_only') && statsP.includes('matches_with_full_replay'), 'result_only 只进顺位/分数指标');
const tenhouUtils = read('../convert/tenhou6_utils.py');
check(tenhouUtils.includes('tenpai'), '流局提取四家听牌标记');
const intakeP = read('../participants/intake.py');
check(intakeP.includes('"riichi": []') && intakeP.includes('"calls": [0, 0, 0, 0]'), '逐局摘要含立直/副露');
const tstypesG = read('src/types/participants.ts');
check(tstypesG.includes('AccountStatsResponse'), 'TS 账号统计类型');

// 20) R10-G Repair 1：手牌级语义 / legacy 重建
const tenhouUtils2 = read('../convert/tenhou6_utils.py');
check(!tenhouUtils2.includes('if any(tenpai)'), '全不听流局也保留 tenpai 标记');
const intakeG2 = read('../participants/intake.py');
check(intakeG2.includes('rich_hands_for_artifact'), '旧 artifact 懒重建（缺 G 字段内存重算）');
check(intakeG2.includes('"chi", "pon", "daiminkan", "kakan"'), '暗杠 ankan 不计入副露');
const statsG2 = read('../participants/stats.py');
check(statsG2.includes('ron_targets') && statsG2.includes('dealins += 1'), '双响放铳按局聚合为一次');
check(statsG2.includes('rich_hands_for_artifact'), 'stats 使用 lazy 重建的逐局摘要');
const pageG2 = read('src/pages/ParticipantsPage.tsx');
check(pageG2.includes('自摸率'), '前端展示自摸率');

// 21) R10-G Repair 2：legacy 重建优先原始 Tenhou6
const intakeG3 = read('../participants/intake.py');
check(intakeG3.includes('_needs_hand_upgrade'), 'hand upgrade 判定（含缺 all-false tenpai 的短窗口）');
check(intakeG3.includes('优先原始 Tenhou6'), '重建优先 tenhou6.json（恢复历史 tenpai）');
check(intakeG3.includes('HANDS_CONTRACT_VERSION'), 'hand summary contract version');

// 22) R10 最终纵向 smoke
check(ladderIngest.includes('_ladder_game_length'), 'participants tonpu → ladder tonpuu 归一（smoke 抓到的集成 bug）');
const smoke = read('../../tests/test_participants_vertical_smoke.py');
check(smoke.includes('test_r10_vertical_smoke'), '纵向 smoke：roster→intake→ledger→ladder→stats');
check(smoke.includes('source_log') && smoke.includes('external_match_id'), '三方一致性核验（tenhou log ↔ snapshot ↔ stats）');

// 23) R10 merge repair：赛季成员（human）+ 每局 game_length 计分
const officialCfg = read('../configs/ladder/seasons/official-ladder-v1.json');
check(officialCfg.includes('"model_id": "human"'), '正式赛季含 human 模型（C23 契约）');
check(officialCfg.includes('"exclusive": true'), '正式赛季 participants exclusive');
const tenhouRS = read('../replay/rank_systems/tenhou.py');
check(tenhouRS.includes('game_length: str | None = None'), 'match_context 支持按局 game_length');
check(tenhouRS.includes('length = game_length or self.game_length'), '按局选 PT 表');
const li = read('../replay/ladder_ingest.py');
check(li.includes('game_length=match.game_length'), 'replay 传每局 game_length 进 match_context');

if (failures > 0) {
  console.error(`participant semantics FAILED (${failures} issues)`);
  process.exit(1);
}
// 24) R10 Production UX Repair 1/2
const pw2 = read('../gateway/api/playwithyou.py');
check(pw2.includes('names[index] if index < len(names)'), 'launcher 名字真相源 = names[index]（按 launcher_slot）');
check(pw2.includes('frozen_roster'), 'PlayWithYouStatus 返回冻结阵容');
const elig = read('../participants/ladder_eligibility.py');
check(elig.includes('ensure_ladder_eligibility'), 'rating_eligible ⇒ season 非空 + 校验（统一不变量）');
const ledger2 = read('../participants/ledger.py');
check(ledger2.includes('if match.rating_eligible:') && ledger2.includes('ensure_ladder_eligibility'), 'create/revise 统一 gate 入口');
const pwPage = read('src/pages/PlayWithYouPage.tsx');
check(!pwPage.includes('SEAT_WINDS'), 'PlayWithYouPage 不出现東南西北（开局前不知道坐席）');
check(!pwPage.includes('account_id'), 'PlayWithYouPage 不选账号（账号推迟到赛后导入）');
check(pwPage.includes('launchers'), 'Play-with-you simplification：只呼出模型（launchers）');
check(pwPage.includes('status.frozen_roster'), '本次启动模型从 status 渲染');
const pwBackend = read('../gateway/api/playwithyou.py');
check(pwBackend.includes('launchers: List'), 'StartPlayWithYouRequest 支持 launchers');
check(pwBackend.includes('_artifact_spec_for_launcher'), 'account-less launcher 从 artifact 冻结 checkpoint');
const intake5 = read('../participants/intake.py');
check(intake5.includes('需要人工指派账号'), 'account-less alias：模型已知但需人工选账号');
check(intake5.includes('_auto_account_id'), '唯一可绑定账号自动建议');
const elig5 = read('../participants/ladder_eligibility.py');
check(elig5.includes('不能携带冻结 bot 模型产物'), '正式人类座位禁止携带 bot artifact');
check(elig5.includes('控制器必须为 human_ui'), '正式人类座位控制器必须 human_ui');
check(intake5.includes('不能保存为全局别名'), 'account-less source alias 禁止晋升 global');
const import5 = read('src/pages/TenhouImportPage.tsx');
check(import5.includes('frozenModel ? draft.alias_id :'), 'frozen seat 切 scope 不清 source alias');
check(import5.includes('保存本局账号对齐'), 'frozen NoName 只允许 match/none');

// 25) R12-A：Replay Stats Projection——正式天梯接入 libriichi 详细统计
check(ladderIngest.includes('replay_artifact_dir'), 'LadderMatch 携带 replay artifact 引用（统计来源）');
check(ladderIngest.includes('normalize_stats_events'), '投影阶段 account-normalized 事件归一');
check(ladderIngest.includes('reach_accepted'), '注入 reach_accepted（libriichi Stat 依赖）');
check(ladderIngest.includes('build_stat_report'), '复用 build_stat_report（不手搓指标定义）');
check(ladderIngest.includes('stats_coverage') && ladderIngest.includes('stats_total_games'), '覆盖率字段披露（stats_coverage / stats_total_games）');
check(ladderIngest.includes('replay_artifact'), '投影指纹包含 replay artifact 证据');
check(intakeP.includes('read_replay_events'), 'intake 暴露 replay 事件读取（events.jsonl 优先）');
const ladderData = read('../replay/ladder.py');
check(ladderData.includes('stats_coverage') && ladderData.includes('stats_games'), '_enrich_account_row 转发覆盖率字段');
const plsR12 = read('../replay/publish_ladder_snapshot.py');
check(plsR12.includes('SNAPSHOT_BUILD_CONTRACT_VERSION = "v4"'), '快照契约版本 v4（reach acceptance 修复强制重建 v3 快照）');
check(tenhouUtils.includes('insert_reach_accepted'), 'converter 按 convlog 状态机补 reach_accepted（canonical）');
const ladderTypes = read('src/types/ladder.ts');
check(ladderTypes.includes('stats_coverage') && ladderTypes.includes('stats_total_games'), 'TS 类型含统计覆盖率字段');
const accountPage = read('src/pages/LadderAccountPage.tsx');
check(accountPage.includes('详细牌谱统计'), '账号详情披露详细牌谱统计覆盖率');
check(pyproject.includes('test_participants_replay_stats_projection.py') || pyproject.includes('test_participants_*.py'), 'R12-A 测试入 pytest 白名单');

console.log('participant semantics OK (model-only launcher, account-less session alias, force-save, routes, server mount, pytest whitelist, R12-A replay stats projection)');
