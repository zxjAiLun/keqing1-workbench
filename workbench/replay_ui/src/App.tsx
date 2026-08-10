import { BrowserRouter, Navigate, Route, Routes, useLocation } from 'react-router-dom';
import { MainLayout } from './components/Layout/MainLayout';
import { DashboardPage } from './pages/DashboardPage';
import { HomePage } from './pages/HomePage';
import { ReviewPage } from './pages/ReviewPage';
import { ReviewHistoryPage } from './pages/ReviewHistoryPage';
import { ReplayViewPage } from './pages/ReplayViewPage';
import { GameBoardReplayPage } from './pages/GameBoardReplayPage';
import { BattlePage } from './pages/BattlePage';
import { BotBattlePage } from './pages/BotBattlePage';
import { PlayWithYouPage } from './pages/PlayWithYouPage';
import { SelfplayAnomaliesPage } from './pages/SelfplayAnomaliesPage';
import { MortalDecisionReviewPage } from './pages/MortalDecisionReviewPage';
import { LadderPage } from './pages/LadderPage';
import { LadderAccountPage } from './pages/LadderAccountPage';
import { LadderModelPage } from './pages/LadderModelPage';
import { SeasonManagerPage } from './pages/SeasonManagerPage';
import { ParticipantsPage } from './pages/ParticipantsPage';
import { MatchesPage } from './pages/MatchesPage';
import { MatchEntryPage } from './pages/MatchEntryPage';
import { MatchDetailPage } from './pages/MatchDetailPage';
import { TenhouImportPage } from './pages/TenhouImportPage';
import { ThemeProvider } from './context/ThemeContext';
import { LADDER_ACCOUNT_PATTERN, LADDER_MODEL_PATTERN, legacyRoutes, MATCH_DETAIL_PATTERN, REVIEW_WORKSPACE_PATTERN, routes } from './routes';
import './styles/globals.css';

/** 兼容重定向：完整保留 query string（包括重复出现的 teacher_reports）。 */
function PreserveSearchRedirect({ to }: { to: string }) {
  const location = useLocation();
  return <Navigate to={{ pathname: to, search: location.search }} replace />;
}

/**
 * 旧 /game-replay 入口：
 * - 带 ?id=xxx 时重定向到 canonical /reviews/:replayId，其余查询参数原样保留；
 * - 无 id 时（内部路由 state 传数据）继续渲染 Workspace 组件。
 */
function LegacyGameReplayRedirect() {
  const location = useLocation();
  const params = new URLSearchParams(location.search);
  const replayId = params.get('id');
  if (!replayId) return <GameBoardReplayPage />;
  params.delete('id');
  const search = params.toString();
  return (
    <Navigate
      to={`${routes.reviewWorkspace(replayId)}${search ? `?${search}` : ''}`}
      replace
    />
  );
}

export default function App() {
  return (
    <ThemeProvider>
      <BrowserRouter>
        <Routes>
          <Route element={<MainLayout />}>
            {/* canonical 路由 */}
            <Route path={routes.home} element={<DashboardPage />} />
            <Route path={routes.reviewNew} element={<ReviewPage />} />
            <Route path={routes.reviewLibrary} element={<ReviewHistoryPage />} />
            <Route path={REVIEW_WORKSPACE_PATTERN} element={<GameBoardReplayPage />} />
            <Route path={routes.tenhou} element={<PlayWithYouPage />} />
            <Route path={routes.diagnosticsCasebook} element={<SelfplayAnomaliesPage />} />
            <Route path={routes.ladder} element={<LadderPage />} />
            <Route path={routes.seasonManager} element={<SeasonManagerPage />} />
            <Route path={LADDER_ACCOUNT_PATTERN} element={<LadderAccountPage />} />
            <Route path={LADDER_MODEL_PATTERN} element={<LadderModelPage />} />
            <Route path={routes.participants} element={<ParticipantsPage />} />
            <Route path={routes.matches} element={<MatchesPage />} />
            <Route path={routes.matchEntry} element={<MatchEntryPage />} />
            <Route path={routes.matchImport} element={<TenhouImportPage />} />
            <Route path={MATCH_DETAIL_PATTERN} element={<MatchDetailPage />} />

            {/* 旧主入口兼容重定向 */}
            <Route path="/review" element={<PreserveSearchRedirect to={routes.reviewNew} />} />
            <Route path="/review-history" element={<PreserveSearchRedirect to={routes.reviewLibrary} />} />
            <Route path="/play-with-you" element={<PreserveSearchRedirect to={routes.tenhou} />} />
            <Route path="/selfplay-anomalies" element={<PreserveSearchRedirect to={routes.diagnosticsCasebook} />} />
            <Route path={legacyRoutes.gameReplay} element={<LegacyGameReplayRedirect />} />

            {/* 隐藏 legacy 路由：组件保留，不再出现在主导航 */}
            <Route path={legacyRoutes.decisionList} element={<ReplayViewPage />} />
            <Route path={legacyRoutes.mortalReview} element={<MortalDecisionReviewPage />} />
            <Route path="/keqingrl-review" element={<PreserveSearchRedirect to={legacyRoutes.mortalReview} />} />
            <Route path="/game" element={<PreserveSearchRedirect to={routes.reviewLibrary} />} />

            {/* Battle：代码与路由保留，主导航移除，后续正式 archive */}
            <Route path="/battle" element={<BattlePage />} />
            <Route path="/bot-battle" element={<BotBattlePage />} />
          </Route>
          <Route path="/home" element={<HomePage />} />
        </Routes>
      </BrowserRouter>
    </ThemeProvider>
  );
}
