import { LibraryBig, ListMusic, Rss } from 'lucide-react'

const items = [
  { id: 'library', label: 'Библиотека', icon: LibraryBig },
  { id: 'tracker', label: 'Трекер', icon: Rss },
  { id: 'queue', label: 'Очередь', icon: ListMusic },
]

export default function BottomNav({ view, onNavigate }) {
  return (
    <nav className="fixed inset-x-0 bottom-0 z-40 flex h-14 border-t border-border-subtle bg-bg-base md:hidden">
      {items.map(({ id, label, icon: Icon }) => {
        const active = view === id
        return (
          <button
            key={id}
            type="button"
            onClick={() => onNavigate(id)}
            className={`flex flex-1 flex-col items-center justify-center gap-0.5 text-[11px] font-medium transition-colors ${
              active ? 'text-primary' : 'text-text-muted'
            }`}
          >
            <Icon size={20} />
            <span>{label}</span>
          </button>
        )
      })}
    </nav>
  )
}
