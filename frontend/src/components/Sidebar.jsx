import { ChevronsLeft, LibraryBig, ListMusic, MicVocal, Rss, Settings, Vault } from 'lucide-react'

const items = [
  { id: 'library', label: 'Библиотека', icon: LibraryBig },
  { id: 'artists', label: 'Исполнители', icon: MicVocal },
  { id: 'queue', label: 'Очередь', icon: ListMusic, badge: 'queue' },
  { id: 'tracker', label: 'Трекер', icon: Rss },
]

export default function Sidebar({ view, onNavigate, counts, collapsed, onToggle, onOpenSettings }) {
  return (
    <aside
      className={`sticky top-0 hidden h-screen shrink-0 flex-col border-r border-border-subtle bg-bg-base transition-[width] duration-200 md:flex ${
        collapsed ? 'w-[68px]' : 'w-60'
      }`}
    >
      <div className="flex h-16 items-center gap-3 px-4">
        <Vault size={24} className="shrink-0 text-primary" />
        {!collapsed && (
          <span className="font-display text-[18px] font-bold tracking-tight text-text-primary">
            SoundVault
          </span>
        )}
      </div>

      <nav className="mt-2 flex flex-1 flex-col gap-1 overflow-y-auto px-2">
        {items.map(({ id, label, icon: Icon, badge }) => {
          const active = view === id
          return (
            <button
              key={id}
              type="button"
              onClick={() => onNavigate(id)}
              title={collapsed ? label : undefined}
              className={`relative flex h-10 items-center gap-3 rounded-md px-3 text-[14px] font-medium transition-colors ${
                active
                  ? 'bg-primary-tint text-primary'
                  : 'text-text-secondary hover:bg-surface-2 hover:text-text-primary'
              } ${collapsed ? 'justify-center px-0' : ''}`}
            >
              {active && (
                <span className="absolute left-0 top-1/2 h-5 w-[3px] -translate-y-1/2 rounded-full bg-primary" />
              )}
              <Icon size={20} className="shrink-0" />
              {!collapsed && <span className="flex-1 truncate text-left">{label}</span>}
              {!collapsed && badge === 'queue' && counts.queue > 0 && (
                <span className="rounded-xs bg-surface-3 px-1.5 py-0.5 font-mono text-[10.5px] text-text-secondary">
                  {counts.queue}
                </span>
              )}
            </button>
          )
        })}
      </nav>

      <div className="border-t border-border-subtle p-3">
        <button
          type="button"
          onClick={onOpenSettings}
          title={collapsed ? 'Настройки' : undefined}
          className={`mb-1 flex h-9 w-full items-center gap-2 text-text-muted transition-colors hover:text-text-primary ${
            collapsed ? 'justify-center' : 'px-2'
          }`}
        >
          <Settings size={18} className="shrink-0" />
          {!collapsed && <span className="text-[13px]">Настройки</span>}
        </button>
        <button
          type="button"
          onClick={onToggle}
          title={collapsed ? 'Развернуть' : 'Свернуть'}
          className={`flex h-9 w-full items-center gap-2 text-text-muted transition-colors hover:text-text-primary ${
            collapsed ? 'justify-center' : 'px-2'
          }`}
        >
          <ChevronsLeft
            size={28}
            className={`shrink-0 transition-transform ${collapsed ? 'rotate-180' : ''}`}
          />
          {!collapsed && <span className="text-[13px]">Свернуть</span>}
        </button>
      </div>
    </aside>
  )
}
