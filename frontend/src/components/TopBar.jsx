import { ChevronLeft, ChevronRight, Plus, Search, Settings } from 'lucide-react'

export default function TopBar({ onSearch, search, onAddTracker, onOpenSettings }) {
  return (
    <header className="sticky top-0 z-10 hidden h-16 items-center gap-4 border-b border-border-subtle bg-bg-main/80 px-6 backdrop-blur-md md:flex">
      <div className="flex items-center gap-2">
        <button
          type="button"
          disabled
          className="flex h-8 w-8 items-center justify-center rounded-full bg-surface-1 text-text-disabled"
          aria-label="Назад"
        >
          <ChevronLeft size={18} />
        </button>
        <button
          type="button"
          disabled
          className="flex h-8 w-8 items-center justify-center rounded-full bg-surface-1 text-text-disabled"
          aria-label="Вперёд"
        >
          <ChevronRight size={18} />
        </button>
      </div>

      <div className="flex h-9 w-[400px] max-w-[40vw] items-center gap-2 rounded-sm border border-transparent bg-surface-inset px-3 focus-within:border-strong focus-within:shadow-focus-ring">
        <Search size={16} className="shrink-0 text-text-muted" />
        <input
          value={search}
          onChange={(e) => onSearch(e.target.value)}
          placeholder="Поиск по библиотеке…"
          className="w-full bg-transparent text-[12.5px] text-text-primary placeholder:text-text-muted focus:outline-none"
        />
      </div>

      <div className="ml-auto flex items-center gap-3">
        <button
          type="button"
          onClick={onOpenSettings}
          className="flex h-9 w-9 items-center justify-center rounded-sm bg-surface-1 text-text-secondary transition-colors hover:bg-surface-2 hover:text-text-primary"
          aria-label="Настройки"
        >
          <Settings size={17} />
        </button>
        <button
          type="button"
          onClick={onAddTracker}
          className="flex h-9 items-center gap-2 rounded-sm bg-primary px-4 text-[14px] font-medium text-on-primary transition-colors hover:bg-primary-hover active:bg-primary-pressed"
        >
          <Plus size={16} />
          Добавить раздачу
        </button>
        <div className="h-8 w-8 rounded-full bg-surface-2 ring-1 ring-border-strong" />
      </div>
    </header>
  )
}
