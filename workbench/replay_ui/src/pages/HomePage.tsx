import { useNavigate } from 'react-router-dom';
import { BarChart2, Swords, Bot } from 'lucide-react';
import { PageHeader, PageShell } from '../components/Layout/PageScaffold';

export function HomePage() {
  const navigate = useNavigate();

  return (
    <PageShell width={720}>
      <div style={{ textAlign: 'center' }}>
        <PageHeader
          eyebrow="Compatibility Entry"
          title="Keqing1"
          description="这是兼容保留的 `/home` 入口。当前 GUI 支持 mortal、70k、weak mortal 和 rulebase。"
        />

        <div style={{ display: 'flex', flexDirection: 'column', gap: 14, alignItems: 'center' }}>
          <button
            onClick={() => navigate('/review')}
            style={{
              width: '100%',
              maxWidth: 400,
              padding: '16px 24px',
              borderRadius: 'var(--radius-md)',
              border: 'none',
              background: 'linear-gradient(135deg, var(--accent) 0%, var(--accent-hover) 100%)',
              color: 'var(--btn-primary-text)',
              fontSize: 16,
              fontWeight: 700,
              cursor: 'pointer',
              display: 'flex',
              alignItems: 'center',
              justifyContent: 'center',
              gap: 10,
              boxShadow: '0 4px 12px var(--accent-shadow)',
              transition: 'transform 0.2s, box-shadow 0.2s',
            }}
            onMouseEnter={e => {
              e.currentTarget.style.transform = 'translateY(-2px)';
              e.currentTarget.style.boxShadow = '0 6px 16px var(--accent-shadow)';
            }}
            onMouseLeave={e => {
              e.currentTarget.style.transform = 'translateY(0)';
              e.currentTarget.style.boxShadow = '0 4px 12px var(--accent-shadow)';
            }}
          >
            <BarChart2 size={20} />
            牌谱分析 Review
          </button>

          <button
            onClick={() => navigate('/battle')}
            style={{
              width: '100%',
              maxWidth: 400,
              padding: '14px 24px',
              borderRadius: 'var(--radius-md)',
              border: '1px solid var(--border)',
              background: 'var(--card-bg)',
              color: 'var(--text-primary)',
              fontSize: 15,
              fontWeight: 600,
              cursor: 'pointer',
              display: 'flex',
              alignItems: 'center',
              justifyContent: 'center',
              gap: 10,
              transition: 'transform 0.2s, border-color 0.2s, box-shadow 0.2s',
            }}
            onMouseEnter={e => {
              e.currentTarget.style.transform = 'translateY(-1px)';
              e.currentTarget.style.borderColor = 'var(--accent)';
              e.currentTarget.style.boxShadow = '0 4px 12px var(--accent-shadow)';
            }}
            onMouseLeave={e => {
              e.currentTarget.style.transform = 'translateY(0)';
              e.currentTarget.style.borderColor = 'var(--border)';
              e.currentTarget.style.boxShadow = 'none';
            }}
          >
            <Swords size={18} />
            开始对战
          </button>

          <button
            onClick={() => navigate('/bot-battle')}
            style={{
              width: '100%',
              maxWidth: 400,
              padding: '14px 24px',
              borderRadius: 'var(--radius-md)',
              border: '1px solid var(--border)',
              background: 'var(--card-bg)',
              color: 'var(--text-primary)',
              fontSize: 15,
              fontWeight: 600,
              cursor: 'pointer',
              display: 'flex',
              alignItems: 'center',
              justifyContent: 'center',
              gap: 10,
            }}
          >
            <Bot size={18} />
            4 Bot 对战
          </button>
        </div>
      </div>
    </PageShell>
  );
}
