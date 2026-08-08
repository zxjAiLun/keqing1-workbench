// src/replay_ui/src/pages/ReviewPage.tsx
import { useNavigate } from 'react-router-dom';
import { History } from 'lucide-react';
import { UploadForm } from '../components/Upload/UploadForm';
import type { ReplayData } from '../types/replay';
import { PageHeader, PageShell, SectionTitle } from '../components/Layout/PageScaffold';
import { legacyRoutes, reviewWorkspaceUrl, routes } from '../routes';

export function ReviewPage() {
  const navigate = useNavigate();

  const handleDataLoaded = (data: unknown) => {
    const replayData = data as ReplayData;
    if (replayData.replay_id) {
      navigate(
        reviewWorkspaceUrl(replayData.replay_id, {
          playerId: replayData.player_id ?? 0,
          teacherReports: replayData.teacher_report_paths ?? [],
        }),
        { replace: true },
      );
    } else {
      // 无 replay_id（未持久化）时走 legacy state 通道打开 Workspace
      navigate(legacyRoutes.gameReplay, { state: { replayData }, replace: true });
    }
  };

  return (
    <PageShell width={1180}>
      <PageHeader
        title="新建 Review"
        actions={(
          <button
            type="button"
            onClick={() => navigate(routes.reviewLibrary)}
            className="btn-secondary"
            style={{ height: 32, display: 'inline-flex', alignItems: 'center', gap: 6 }}
          >
            <History size={14} />
            Review Library
          </button>
        )}
      />

      <section className="card" style={{ padding: 12 }}>
          <SectionTitle title="牌谱输入" />
          <UploadForm onDataLoaded={handleDataLoaded} />
      </section>

    </PageShell>
  );
}
